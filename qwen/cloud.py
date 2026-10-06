"""Contrôle de la révision Qwen uniquement ; aucune modification du template partagé."""
from pathlib import Path
import hashlib,json,os,re,shlex,sys,threading,time,uuid
ROOT=Path(__file__).resolve().parent
from .infra.transport import Container,CONFIG,azure
from .infra.blob import service,CONTAINER,signed,upload
from azure.core.exceptions import ResourceNotFoundError

class Busy(Exception):pass
class Stopped(Exception):pass
def clean_error(error):
    value=str(error)
    value=re.sub(r'https?://[^\s\"\']+','[adresse de service]',value)
    if 'AADSTS' in value or 'az login' in value:return 'La connexion Azure doit être renouvelée. Ouvre un terminal et exécute az login.'
    return value[-700:]

class Connection(Container):
    def __init__(self,revision):
        self.config=CONFIG;self.app=azure('containerapp','show','-g',CONFIG['resource_group'],'-n',CONFIG['app']);self.revision=revision
        replicas=azure('containerapp','replica','list','-g',CONFIG['resource_group'],'-n',CONFIG['app'],'--revision',revision)
        ready=[r for r in replicas if any(c.get('ready') for c in r['properties']['containers'])]
        if not ready:raise Busy('L’A100 démarre encore.')
        replica=ready[0];self.replica=replica['name'];container=next(c for c in replica['properties']['containers'] if c['name']==CONFIG['container'])
        endpoint=container['logStreamEndpoint'].split('/subscriptions/')[0].replace('https://','wss://')
        self.endpoint=f"{endpoint}/subscriptions/{CONFIG['subscription']}/resourceGroups/{CONFIG['resource_group']}/containerApps/{CONFIG['app']}/revisions/{revision}/replicas/{self.replica}/containers/{CONFIG['container']}/exec"
        self._token=None;self._token_time=0;self._connection=None;self._closed=threading.Event()
    def put(self,local,remote):
        self.checked_path(remote);path=Path(local);digest=hashlib.sha256(path.read_bytes()).hexdigest();url=upload(path,'qwen-studio/control/'+digest+'/'+path.name)
        code="import pathlib,urllib.request,hashlib;p=pathlib.Path("+repr(remote)+");p.parent.mkdir(parents=True,exist_ok=True);urllib.request.urlretrieve("+repr(url)+",p);assert hashlib.sha256(p.read_bytes()).hexdigest()=="+repr(digest)
        self.run(shlex.join(['python','-c',code]),timeout=90)

class Cloud:
    def __init__(self,data):
        self.cfg=json.loads((ROOT/'config.json').read_text());self.data=Path(data)
        self.private=self.data/'private';self.private.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.session_file=self.data/'cloud-session.json'
        self.session=json.loads(self.session_file.read_text()) if self.session_file.exists() else {}
        self.revision=self.cfg['revision']
    def reload_session(self):
        self.session=json.loads(self.session_file.read_text()) if self.session_file.exists() else {}
    def save(self):self.session_file.write_text(json.dumps(self.session,indent=2))
    def inspect(self):
        if self.cfg.get('sharedGpuApp'):
            shared={**CONFIG,'app':self.cfg['sharedGpuApp']}
            revisions=azure('containerapp','revision','list','--all','-g',CONFIG['resource_group'],'-n',shared['app'],config=shared)
            if any(r['properties'].get('active') or r['properties'].get('replicas',0) for r in revisions):
                return {'status':'occupied','label':'A100 utilisée par un traitement d’avatars','running':True,'checkedAt':time.time()}
        revisions=azure('containerapp','revision','list','--all','-g',CONFIG['resource_group'],'-n',CONFIG['app'])
        active=[r for r in revisions if r['properties'].get('active') or r['properties'].get('replicas',0)]
        others=[r['name'] for r in active if r['name']!=self.revision]
        running=any(r['properties'].get('replicas',0)>0 for r in active)
        if others:return {'status':'occupied','label':'Occupée par une autre tâche','running':running,'checkedAt':time.time()}
        if not active:return {'status':'off','label':'A100 éteinte','checkedAt':time.time()}
        own=next((r for r in active if r['name']==self.revision),None)
        if own and self.session.get('owned'):
            ready=self.session.get('ready') and own['properties'].get('replicas',0)>0
            return {'status':'ready' if ready else 'starting','label':'Prête à générer' if ready else 'Démarrage de l’A100','checkedAt':time.time(),'owned':True,'running':running}
        return {'status':'available','label':'A100 disponible, connexion possible','checkedAt':time.time(),'running':running}
    def status_blob(self,name):
        try:return json.loads(service().get_blob_client(CONTAINER,name).download_blob(connection_timeout=15,read_timeout=30).readall())
        except ResourceNotFoundError:return None
    def ensure_ready(self,notify,cancelled=lambda:False):
        info=self.inspect()
        if info['status']=='occupied':raise Busy(info['label'])
        if info['status']=='off':
            # The immutable revision contains only sleep, never the production worker.
            rev=azure('containerapp','revision','show','-g',CONFIG['resource_group'],'-n',CONFIG['app'],'--revision',self.revision)
            assert rev['properties']['template']['containers'][0]['args']==['-lc','exec sleep infinity']
            azure('containerapp','revision','activate','-g',CONFIG['resource_group'],'-n',CONFIG['app'],'--revision',self.revision)
            self.session={'owned':True,'ready':False,'id':uuid.uuid4().hex};self.save()
        if info['status']!='ready':
            notify({'status':'preparing' if info.get('running') else 'starting',
                    'label':'Connexion à Qwen' if info.get('running') else 'Démarrage de l’A100',
                    'running':bool(info.get('running'))})
        deadline=time.time()+1200;c=None
        while time.time()<deadline:
            if cancelled():self.stop();raise Stopped()
            try:
                c=Connection(self.revision)
                c.on_console_wait=lambda seconds:notify({'status':'preparing','label':f'Connexion Azure limitée temporairement · reprise dans {seconds}s','running':True,'owned':True})
                busy=c.run('nvidia-smi --query-compute-apps=pid --format=csv,noheader',timeout=30).strip()
                break
            except Busy:
                if c:c.close()
                c=None
                time.sleep(10)
            except RuntimeError as error:
                if 'ClusterExecFailure' not in str(error):raise
                if c:c.close()
                c=None
                time.sleep(10)
        if c is None:raise RuntimeError('L’A100 n’a pas démarré dans le délai prévu. Tu peux réessayer.')
        with c:
            if busy:raise Busy('Un calcul utilise encore l’A100.')
            if self.session.get('ready') and self.session.get('replica')==c.replica:return
            self.session.update(owned=True,ready=False,replica=c.replica)
            self.session.setdefault('id',uuid.uuid4().hex);self.save()
            if self.session.pop('initFailed',False):self.session['prepareId']=uuid.uuid4().hex
            self.session.setdefault('prepareId',uuid.uuid4().hex);self.save()
            prefix='qwen-studio/prepare/'+self.session['prepareId'];remote=self.cfg['remoteRoot']+'/prepare-'+self.session['prepareId']
            cfg={**self.cfg,'modelRoot':self.cfg['remoteModelRoot'],'archiveRead':signed(self.cfg['archive']),'statusWrite':signed(prefix+'/progress.json',write=True)}
            path=self.private/('prepare-'+self.session['id']+'.json');path.write_text(json.dumps(cfg))
            c.put(path,remote+'/private.json');c.put(ROOT/'remote/prepare.py',remote+'/prepare.py')
            marker=c.run('test -f '+shlex.quote(remote+'/progress.json')+' && cat '+shlex.quote(remote+'/progress.json')+' || true',timeout=30).strip()
            previous=json.loads(marker) if marker else {}
            if previous.get('stage')!='ready':
                c.run(f"mkdir -p {shlex.quote(remote)}; if mkdir {shlex.quote(remote+'/launch.lock')} 2>/dev/null; then nohup python {shlex.quote(remote+'/prepare.py')} {shlex.quote(remote+'/private.json')} > {shlex.quote(remote+'/runtime.log')} 2>&1 </dev/null & fi\nprintf 'PREPARATION_STARTED\\n'",timeout=45)
            else:self.session.update(ready=True);self.save();notify({'status':'ready','label':'Prête à générer','owned':True});return
        labels={'preparing':'Préparation du modèle','restoring':'Restauration de Qwen','checking':'Vérification du modèle','extracting':'Installation du modèle','cache-checking':'Vérification du cache permanent','cache-model':'Chargement depuis le cache permanent','cache-environment':'Préparation de l’environnement Python','cache-extracting':'Installation de l’environnement Python','ready':'Prête à générer'}
        deadline=time.time()+1800
        while time.time()<deadline:
            if cancelled():self.stop();raise Stopped()
            state=self.status_blob(prefix+'/progress.json') or {}
            stage=state.get('stage','preparing')
            if stage=='failed':
                self.session['initFailed']=True;self.save()
                raise RuntimeError('Préparation de Qwen échouée : '+state.get('error','erreur du service'))
            total=state.get('totalBytes') or self.cfg['archiveBytes']
            notify({'status':'ready' if stage=='ready' else 'preparing','label':labels.get(stage,'Préparation de Qwen'),'progress':round(state.get('downloadedBytes',0)/total*100),'owned':True,'cache':state.get('cache',False)})
            if stage=='ready':self.session.update(ready=True);self.save();return
            time.sleep(5)
        self.session['initFailed']=True;self.save()
        raise RuntimeError('La préparation du modèle a dépassé le délai prévu.')
    def submit(self,job,store,on_remote):
        id=job['id'];prefix='qwen-studio/jobs/'+id;remote=self.cfg['remoteRoot']+'/jobs/'+id
        folder=store.root/'jobs'/id;folder.mkdir(exist_ok=True)
        refs=[store.get('uploads',id) for id in job['references']]
        manifest={k:job[k] for k in ['prompt','negativePrompt','width','height','count','steps','guidance','seed']}
        manifest['inputs']=[{'sha256':r['sha256']} for r in refs]
        path=folder/'request.json';path.write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
        cfg={'modelRoot':self.cfg['remoteModelRoot'],'inputReads':[upload(r['path'],prefix+'/inputs/'+str(i)+'.png') for i,r in enumerate(refs)],'statusWrite':signed(prefix+'/progress.json',write=True),'resultWrites':[signed(prefix+f'/result-{i}.png',write=True) for i in range(job['count'])]}
        private=self.private/(id+'.json');private.write_text(json.dumps(cfg))
        receipt={'prefix':prefix,'path':remote,'submittedAt':time.time()}
        with Connection(self.revision) as c:
            if c.replica!=self.session.get('replica'):raise RuntimeError('L’A100 a redémarré. Réveille-la puis relance la génération.')
            c.put(path,remote+'/job.json');c.put(private,remote+'/private.json');c.put(ROOT/'remote/generate.py',remote+'/generate.py')
            on_remote(receipt)  # Persist before sending: a lost acknowledgement never repeats a job.
            python=self.cfg['remoteModelRoot']+'/venv/bin/python'
            c.run(f"if mkdir {shlex.quote(remote+'/launch.lock')} 2>/dev/null; then nohup {shlex.quote(python)} {shlex.quote(remote+'/generate.py')} {shlex.quote(remote)} > {shlex.quote(remote+'/runtime.log')} 2>&1 </dev/null & fi\nprintf 'GENERATION_SUBMITTED\\n'",timeout=60)
        return receipt
    def poll(self,job):return self.status_blob(job['_remote']['prefix']+'/progress.json')
    def cancel(self,job):
        with Connection(self.revision) as c:c.run('touch '+shlex.quote(job['_remote']['path']+'/cancel'),timeout=30)
    def collect(self,job,results,store):
        folder=store.root/'jobs'/job['id'];folder.mkdir(exist_ok=True);collected=[]
        for r in results:
            path=folder/f'image-{r["index"]}.png'
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=r['sha256']:
                temp=path.with_suffix('.part')
                with temp.open('wb') as f:service().get_blob_client(CONTAINER,job['_remote']['prefix']+f'/result-{r["index"]}.png').download_blob(max_concurrency=4).readinto(f)
                if hashlib.sha256(temp.read_bytes()).hexdigest()!=r['sha256']:raise RuntimeError('Le transfert de l’image est incomplet. Réessaie la récupération.')
                temp.replace(path)
            collected.append({**r,'path':str(path)})
        return collected
    def stop(self):
        if not self.session.get('owned'):raise Busy('Cette instance A100 appartient à une autre tâche.')
        revisions=azure('containerapp','revision','list','--all','-g',CONFIG['resource_group'],'-n',CONFIG['app'])
        own=next((r for r in revisions if r['name']==self.revision),None)
        if not own or not (own['properties'].get('active') or own['properties'].get('replicas',0)):
            self.session={};self.save();return
        try:
            with Connection(self.revision) as c:
                if self.session.get('replica') and c.replica!=self.session['replica']:raise Busy('L’instance A100 a changé ; arrêt refusé.')
                if c.run('nvidia-smi --query-compute-apps=pid --format=csv,noheader',timeout=30).strip():raise Busy('Un calcul est encore actif sur l’A100 ; arrêt refusé.')
        except Busy:raise
        azure('containerapp','revision','deactivate','-g',CONFIG['resource_group'],'-n',CONFIG['app'],'--revision',self.revision)
        self.session={};self.save()
