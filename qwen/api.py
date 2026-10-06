"""Authenticated durable Qwen jobs, isolated by the existing Image Lab owner."""
from pathlib import Path
from typing import Annotated
import hashlib,io,json,secrets,threading,time,uuid,zipfile
from urllib.parse import urlsplit
from PIL import Image,ImageOps
from fastapi import APIRouter,Request,HTTPException,UploadFile,File
from fastapi.responses import FileResponse,StreamingResponse
from pydantic import BaseModel,Field,model_validator
from starlette.concurrency import run_in_threadpool
from .store import Store,FINAL
from .engine import Engine
from .cloud import Cloud
from .infra.arm import a10_state
from .infra.transport import CONFIG

class JobParams(BaseModel):
    prompt:str=Field(min_length=1,max_length=16000)
    negativePrompt:str=Field(default='',max_length=16000)
    references:list[str]=Field(default_factory=list,max_length=8)
    width:int=Field(default=2048,ge=256,le=2048)
    height:int=Field(default=1152,ge=256,le=2048)
    count:int=Field(default=1,ge=1,le=6)
    steps:int=Field(default=40,ge=4,le=60)
    guidance:float=Field(default=1,ge=1,le=7)
    seed:int|None=Field(default=None,ge=0,le=2**31-1)
    title:str=Field(default='',max_length=100)
    @model_validator(mode='after')
    def validate(self):
        if not self.prompt.strip():raise ValueError('Le prompt est requis.')
        if self.width%32 or self.height%32:raise ValueError('Les dimensions Qwen doivent être des multiples de 32.')
        if len(set(self.references))!=len(self.references):raise ValueError('Une référence est présente deux fois.')
        return self

class Preferences(BaseModel):
    idleMinutes:int=Field(ge=0,le=120)

class GalleryCloud(Cloud):
    def __init__(self,data,gallery):super().__init__(data);self.gallery=gallery
    def collect(self,job,results,store):
        outputs=super().collect(job,results,store)
        for result in outputs:
            previous=next((item for item in job.get('results',[]) if item['index']==result['index'] and item.get('sha256')==result.get('sha256') and item.get('gallery')),None)
            if previous:
                result['gallery']=previous['gallery']
                continue
            items=self.gallery.save([Path(result['path']).read_bytes()],owner=job['owner'],prompt=job['prompt'],
                model='qwen-image-2.1',mode='edit' if job['references'] else 'generate',quality=f"{job['steps']} steps, seed {result['seed']}",deduplicate=True)
            result['gallery']=items[0]
        return outputs

def register(app,gallery,owner,provider=None,start_engine=True):
    gallery._check_root()
    store=Store(gallery.root/'qwen')
    engine=Engine(store,provider or GalleryCloud(store.root,gallery))
    router=APIRouter(prefix='/api/qwen');a10={'id':'a10','power':'unknown','running':False,'supported':False,'reason':'Vérification de l’A10 en cours.'}
    app.state.qwen_store=store;app.state.qwen_engine=engine
    def check(request):
        # Session is signed by the existing OAuth middleware. Tokens are per user session.
        expected=request.session.get('qwen_csrf','')
        if not expected or not secrets.compare_digest(request.headers.get('x-qwen-token',''),expected):raise HTTPException(403,'Recharge la page pour renouveler la session Qwen.')
        origin=request.headers.get('origin')
        if origin and urlsplit(origin).netloc!=request.headers.get('host'):raise HTTPException(403,'Origine non autorisée.')
    def owned(request,table,id):
        value=store.get(table,id)
        if not value or value.get('owner')!=owner(request):raise HTTPException(404,'Élément introuvable.')
        return value
    def public(job):
        value=store.public_job(job)
        for ref in value['references']:ref.pop('owner',None)
        for result in value['results']:
            saved=result.get('gallery')
            if saved and not gallery.get(job['owner'],saved['id']):result['gallery']=None
        return value
    def observe_a10():
        nonlocal a10
        while not engine.closed.is_set():
            try:a10=a10_state(CONFIG['subscription'])
            except Exception:a10={**a10,'power':'unknown','running':False,'reason':'État de l’A10 indisponible. Qwen reste utilisable sur l’A100.'}
            engine.closed.wait(60)
    if start_engine:
        @app.on_event('startup')
        def start():
            engine.start();threading.Thread(target=observe_a10,daemon=True,name='a10-observer').start()
        @app.on_event('shutdown')
        def stop():engine.close()
    @router.get('/session')
    def session(request:Request):
        request.session.setdefault('qwen_csrf',secrets.token_urlsafe(32))
        return {'token':request.session['qwen_csrf']}
    @router.get('/state')
    def state(request:Request):
        gpu=engine.state();pending=store.pending()
        return {'a100':{**gpu,'running':gpu.get('running',gpu['status'] in ['ready','generating','available','preparing']),'supported':True},
                'a10':a10,'settings':store.settings(),'queueLength':len(pending),
                'ownQueueLength':sum(j['owner']==owner(request) for j in pending)}
    @router.post('/gpu/{gpu}/start',status_code=202)
    def wake(gpu:str,request:Request):
        check(request)
        if gpu!='a100':raise HTTPException(409,'Cette installation Qwen BF16 est validée sur l’A100. Le mode A10 avec déport en RAM reste indisponible.')
        engine.wake();return {'accepted':True}
    @router.post('/gpu/a100/stop',status_code=202)
    def stop_gpu(request:Request):
        check(request)
        if store.pending():raise HTTPException(409,'Des générations sont en cours ou en attente.')
        if not engine.state().get('owned'):raise HTTPException(409,'Cette instance appartient à une autre tâche.')
        engine.request_stop();return {'accepted':True}
    @router.put('/settings')
    def settings(value:Preferences,request:Request):
        check(request);return store.put('settings','preferences',value.model_dump())
    @router.post('/uploads')
    async def upload(request:Request,file:Annotated[UploadFile,File()]):
        check(request);data=await file.read(30*1024*1024+1)
        if len(data)>30*1024*1024:raise HTTPException(413,'Image trop lourde : 30 Mo maximum.')
        def save():
            try:
                with Image.open(io.BytesIO(data)) as raw:
                    if raw.format not in ['PNG','JPEG','WEBP'] or max(raw.size)>8192 or raw.width*raw.height>40_000_000 or min(raw.size)<16:raise ValueError()
                    im=ImageOps.exif_transpose(raw).convert('RGB');im.load()
            except Exception:raise HTTPException(422,'Utilise un PNG, JPEG ou WebP valide, de 16 à 8192 pixels de côté et de 40 mégapixels maximum.')
            id=uuid.uuid4().hex;path=store.root/'uploads'/(id+'.png');im.save(path)
            value={'id':id,'owner':owner(request),'name':(file.filename or 'image.png').replace('\\','/').split('/')[-1][:160],
                'width':im.width,'height':im.height,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'createdAt':time.time()}
            store.put('uploads',id,value)
            return {k:v for k,v in value.items() if k not in ['owner','path']}|{'url':f'/api/qwen/uploads/{id}/image'}
        await run_in_threadpool(gallery.ensure_writable,1)
        return await run_in_threadpool(save)
    @router.get('/uploads/{id}/image')
    def reference(id:str,request:Request):return FileResponse(owned(request,'uploads',id)['path'],media_type='image/png',headers={'Cache-Control':'private, no-store'})
    @router.get('/jobs')
    def jobs(request:Request):
        pending=store.pending();positions={j['id']:i+1 for i,j in enumerate(pending)}
        return {'jobs':[public(j)|{'position':positions.get(j['id'])} for j in sorted(store.all('jobs'),key=lambda j:j['createdAt'],reverse=True) if j['owner']==owner(request)]}
    @router.post('/jobs',status_code=201)
    async def create(value:JobParams,request:Request):
        check(request)
        for id in value.references:owned(request,'uploads',id)
        await run_in_threadpool(gallery.ensure_writable,value.count)
        key=request.headers.get('idempotency-key')
        if key and len(key)>100:raise HTTPException(400,'Identifiant de demande invalide.')
        try:job=store.create_job({**value.model_dump(),'owner':owner(request)},owner(request)+':'+key if key else None)
        except ValueError as e:raise HTTPException(409,str(e))
        engine.wake(False);return public(job)
    @router.get('/jobs/{id}')
    def job(id:str,request:Request):return public(owned(request,'jobs',id))
    @router.post('/jobs/{id}/cancel')
    def cancel(id:str,request:Request):
        check(request)
        with store.lock:
            value=owned(request,'jobs',id)
            if value['status'] in FINAL:return public(value)
            value=store.update_job(id,cancelRequested=True)
            if value['status']=='queued':value=store.update_job(id,status='cancelled',message='Annulée')
            if not store.pending() and not engine.manual_wake:engine.wanted=False
        return public(value)
    @router.post('/jobs/{id}/resume')
    def resume(id:str,request:Request):
        check(request);value=owned(request,'jobs',id)
        if value['status']!='failed' or not value.get('_remote'):raise HTTPException(409,'Le suivi de cette demande ne peut pas reprendre.')
        engine.wake(False);return public(store.update_job(id,status='running',message='Reprise du suivi'))
    @router.get('/jobs/{id}/images/{index}')
    def image(id:str,index:int,request:Request):
        job=owned(request,'jobs',id);value=next((r for r in job['results'] if r['index']==index),None)
        if not value:raise HTTPException(404,'Image indisponible.')
        return FileResponse(value['path'],media_type='image/png',headers={'Cache-Control':'private, no-store'})
    @router.get('/jobs/{id}/download')
    def download(id:str,request:Request):
        value=owned(request,'jobs',id)
        if not value['results']:raise HTTPException(404,'Aucune image disponible.')
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_STORED) as archive:
            for r in value['results']:archive.write(r['path'],f'image-{r["index"]+1:02}.png')
            archive.writestr('parametres.json',json.dumps(public(value),ensure_ascii=False,indent=2))
        buffer.seek(0);return StreamingResponse(buffer,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="qwen-{id[:8]}.zip"'})
    app.include_router(router)
    return engine
