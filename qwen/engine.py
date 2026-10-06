import json,threading,time
from .cloud import Busy,Stopped,clean_error

class Engine:
    def __init__(self,store,provider):
        self.store=store;self.provider=provider;self.lock=threading.RLock();self.closed=threading.Event();self.event=threading.Event()
        self.wanted=False;self.manual_wake=False;self.blocked=False;self.stop_requested=False;self.last_use=time.time();self.last_inspect=0
        self._state={'status':'checking','label':'Vérification de l’A100'};self.thread=None
    def state(self):
        with self.lock:return {**self._state,'wakeRequested':self.wanted,'lastActivity':self.last_use}
    def notify(self,value):
        with self.lock:self._state={**value,'updatedAt':time.time()}
    def wake(self,manual=True):self.wanted=True;self.manual_wake|=manual;self.blocked=False;self.stop_requested=False;self.last_use=time.time();self.event.set()
    def request_stop(self):self.stop_requested=True;self.wanted=False;self.event.set()
    def start(self):
        self.thread=threading.Thread(target=self.lead,daemon=True,name='qwen-queue');self.thread.start()
    def lead(self):
        import os
        while not self.closed.is_set():
            lease=(self.store.root/'atelier.lock').open('a+b')
            if lease.seek(0,2)==0:lease.write(b'0');lease.flush()
            lease.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(lease.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:
                lease.close();self.notify({'status':'starting','label':'La file est suivie par une autre instance du serveur'});self.closed.wait(5);continue
            self._lease=lease
            try:
                if hasattr(self.provider,'reload_session'):self.provider.reload_session()
                self.loop()
            finally:lease.close()
            return
    def close(self):self.closed.set();self.event.set()
    def loop(self):
        while not self.closed.is_set():
            try:self.tick()
            except Busy as e:self.notify({'status':'occupied','label':str(e)});self.last_inspect=time.time()
            except Stopped:self.notify({'status':'off','label':'A100 éteinte'});self.stop_requested=False
            except Exception as e:
                self.notify({'status':'error','label':clean_error(e)});self.last_inspect=time.time();self.wanted=False;self.blocked=True
            self.event.wait(5);self.event.clear()
    def tick(self):
        if self.blocked and not self.stop_requested:return
        pending=self.store.pending()
        if self.stop_requested and not pending:
            self.provider.stop();self.stop_requested=False;self.notify({'status':'off','label':'A100 éteinte'});return
        if time.time()-self.last_inspect>20:
            self.notify(self.provider.inspect());self.last_inspect=time.time()
        if pending:self.wanted=True
        if pending and pending[0].get('_remote'):
            self.process(pending[0]);self.last_use=time.time();return
        if self.wanted:
            if self.state()['status']=='occupied':return
            self.provider.ensure_ready(self.notify,lambda:self.stop_requested and not self.store.pending())
            self.last_use=time.time()
            if pending:self.process(pending[0]);self.last_use=time.time()
            else:
                self.wanted=False;self.manual_wake=False;self.notify({'status':'ready','label':'Prête à générer','owned':True})
        minutes=self.store.settings()['idleMinutes']
        if not pending and minutes and self.state()['status']=='ready' and time.time()-self.last_use>minutes*60:
            self.provider.stop();self.notify({'status':'off','label':'A100 éteinte après inactivité'})
    def process(self,job):
        id=job['id']
        with self.store.lock:
            job=self.store.get('jobs',id)
            if job['status']=='cancelled':return
            if job.get('cancelRequested') and not job.get('_remote'):
                self.store.update_job(id,status='cancelled',message='Annulée');return
            if not job.get('_remote'):self.store.update_job(id,status='preparing',message='Envoi de la demande')
        self.notify({'status':'generating','label':'Génération en cours','owned':True,'jobId':id})
        try:
            if not job.get('_remote'):
                self.provider.submit(job,self.store,lambda r:self.store.update_job(id,_remote=r,status='running',message='Chargement de Qwen'))
            job=self.store.get('jobs',id);cancel_sent=False;failures=0
            while not self.closed.is_set():
                job=self.store.get('jobs',id)
                if job.get('cancelRequested') and not cancel_sent:
                    self.provider.cancel(job);cancel_sent=True;self.store.update_job(id,message='Annulation en cours')
                try:progress=self.provider.poll(job);failures=0
                except Exception:
                    failures+=1
                    if failures>=8:raise
                    self.store.update_job(id,message='Connexion interrompue, reprise du suivi…');self.closed.wait(5);continue
                if not progress:
                    if time.time()-job['_remote']['submittedAt']>300:raise RuntimeError('La demande a été envoyée mais le service ne répond pas. Aucun second appel n’a été lancé.')
                    self.closed.wait(3);continue
                stage=progress.get('stage');count=len(progress.get('results',[]));outputs=job.get('results',[])
                if count>len(outputs):outputs=self.provider.collect(job,progress['results'],self.store)
                message={'loading':'Chargement de Qwen','generating':f"Image {progress.get('image',1)} sur {job['count']} · étape {progress.get('step',0)}/{job['steps']}",'complete':'Terminée','cancelled':'Annulée','failed':progress.get('error','La génération a échoué.')}.get(stage,'Génération en cours')
                status=stage if stage in ['complete','cancelled','failed'] else 'running'
                job=self.store.update_job(id,status=status,progress=progress.get('progress',0),message=message,results=outputs)
                if status in ['complete','cancelled','failed']:
                    folder=self.store.root/'jobs'/id;folder.mkdir(exist_ok=True)
                    (folder/'metadata.json').write_text(json.dumps(self.store.public_job(job),indent=2,ensure_ascii=False),encoding='utf-8');break
                self.closed.wait(3)
        except Exception as error:
            # Keep the remote receipt for explicit recovery; never resubmit blindly.
            self.store.update_job(id,status='failed',message=clean_error(error))
        self.notify({'status':'ready','label':'Prête à générer','owned':True})
