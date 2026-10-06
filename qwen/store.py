from pathlib import Path
import json,sqlite3,threading,time,uuid,secrets

FINAL={'complete','failed','cancelled','imported'}
class Store:
    def __init__(self,root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        (self.root/'uploads').mkdir(exist_ok=True);(self.root/'jobs').mkdir(exist_ok=True)
        self.lock=threading.RLock();self.db=sqlite3.connect(self.root/'studio.sqlite',check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        for table in ['jobs','uploads','settings']:
            self.db.execute(f'CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        self.db.commit()
    def put(self,table,id,data):
        assert table in ['jobs','uploads','settings']
        with self.lock:
            self.db.execute(f'INSERT OR REPLACE INTO {table}(id,data) VALUES (?,?)',(id,json.dumps(data,ensure_ascii=False)));self.db.commit()
        return data
    def get(self,table,id):
        assert table in ['jobs','uploads','settings']
        with self.lock:r=self.db.execute(f'SELECT data FROM {table} WHERE id=?',(id,)).fetchone()
        return json.loads(r[0]) if r else None
    def all(self,table):
        assert table in ['jobs','uploads','settings']
        with self.lock:rows=self.db.execute(f'SELECT data FROM {table}').fetchall()
        return [json.loads(r[0]) for r in rows]
    def update_job(self,id,**values):
        with self.lock:
            job=self.get('jobs',id)
            if not job:raise KeyError(id)
            job.update(values);job['updatedAt']=time.time();return self.put('jobs',id,job)
    def create_job(self,params,request_id=None):
        with self.lock:
            if request_id:
                old=next((j for j in self.all('jobs') if j.get('requestId')==request_id),None)
                if old:
                    if old.get('_request')!=params:raise ValueError('Cette demande a déjà été utilisée avec d’autres paramètres.')
                    return old
            for id in params.get('references',[]):
                if not self.get('uploads',id):raise ValueError('Une image de référence n’est plus disponible.')
            seed=params.get('seed');seed=secrets.randbelow(2**31) if seed is None else seed
            job={**params,'id':uuid.uuid4().hex,'seed':seed,'status':'queued','progress':0,'message':'En attente','createdAt':time.time(),'updatedAt':time.time(),'results':[],'requestId':request_id,'_request':params,'cancelRequested':False}
            return self.put('jobs',job['id'],job)
    def pending(self):return sorted([j for j in self.all('jobs') if j['status'] not in FINAL],key=lambda j:j['createdAt'])
    def settings(self):return {'idleMinutes':15,**(self.get('settings','preferences') or {})}
    def public_job(self,job):
        value={k:v for k,v in job.items() if not k.startswith('_') and k not in ['requestId','owner']}
        value['references']=[{**self.get('uploads',id),'url':f'/api/qwen/uploads/{id}/image'} for id in job.get('references',[]) if self.get('uploads',id)]
        for ref in value['references']:ref.pop('path',None)
        value['results']=[{k:v for k,v in r.items() if k!='path'}|{'url':f'/api/qwen/jobs/{job["id"]}/images/{r["index"]}'} for r in job.get('results',[])]
        value['canResume']=bool(job.get('_remote')) and job['status']=='failed'
        return value
