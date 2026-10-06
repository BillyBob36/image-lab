import io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from fastapi import FastAPI,Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from PIL import Image
from gallery import ImageStore
from qwen.api import register,GalleryCloud
from qwen.store import Store
from qwen.engine import Engine
from qwen.cloud import Busy
from capabilities import MODELS,validate_size

class QwenTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.gallery=ImageStore(Path(self.tmp.name));self.provider=Mock()
        self.provider.inspect.return_value={'status':'occupied','label':'Autre tâche'}
        self.app=FastAPI();self.app.add_middleware(SessionMiddleware,secret_key='test-only')
        register(self.app,self.gallery,lambda r:r.headers.get('x-owner','a'),self.provider,start_engine=False)
        self.store=self.app.state.qwen_store;self.engine=self.app.state.qwen_engine
        self.addCleanup(self.store.db.close)
        self.client=TestClient(self.app);self.addCleanup(self.client.close)
        self.token=self.client.get('/api/qwen/session').json()['token'];self.headers={'X-Qwen-Token':self.token}
        out=io.BytesIO();Image.new('RGB',(64,64),'blue').save(out,'PNG');self.png=out.getvalue()
    def upload(self):
        r=self.client.post('/api/qwen/uploads',headers=self.headers,files={'file':('image.png',self.png,'image/png')})
        self.assertEqual(r.status_code,200,r.text);return r.json()
    def create(self,**kw):
        return self.client.post('/api/qwen/jobs',headers=self.headers,json={'prompt':'  Texte exact\n sans réécriture  ',**kw})
    def test_prompt_exact_order_seed_and_idempotency(self):
        a=self.upload();b=self.upload();self.headers['Idempotency-Key']='same'
        x=self.create(references=[b['id'],a['id']],seed=0,count=3);y=self.create(references=[b['id'],a['id']],seed=0,count=3)
        self.assertEqual(x.status_code,201,x.text);self.assertEqual(x.json()['id'],y.json()['id'])
        stored=self.store.get('jobs',x.json()['id']);self.assertEqual(stored['prompt'],'  Texte exact\n sans réécriture  ')
        self.assertEqual(stored['references'],[b['id'],a['id']]);self.assertEqual(stored['seed'],0)
        self.assertEqual(self.create(prompt='different').status_code,409)
    def test_owner_isolation_in_jobs_refs_and_mutations(self):
        ref=self.upload();job=self.create(references=[ref['id']]).json()
        headers={**self.headers,'x-owner':'b'}
        self.assertEqual(self.client.get('/api/qwen/jobs',headers=headers).json()['jobs'],[])
        for path in [f"/jobs/{job['id']}",f"/uploads/{ref['id']}/image"]:
            self.assertEqual(self.client.get('/api/qwen'+path,headers=headers).status_code,404)
        self.assertEqual(self.client.post(f"/api/qwen/jobs/{job['id']}/cancel",headers=headers).status_code,404)
        self.assertEqual(self.client.post('/api/qwen/jobs',headers=headers,json={'prompt':'x','references':[ref['id']]}).status_code,404)
        public=json.dumps(job);self.assertNotIn('owner',public);self.assertNotIn(self.tmp.name,public)
    def test_csrf_and_origin(self):
        self.assertEqual(self.client.post('/api/qwen/jobs',json={'prompt':'x'}).status_code,403)
        self.assertEqual(self.client.post('/api/qwen/jobs',headers={**self.headers,'Origin':'https://different.example'},json={'prompt':'x'}).status_code,403)
    def test_validation_and_unsupported_hardware(self):
        for value in [{'width':2000},{'height':4096},{'steps':0},{'count':7},{'seed':-1},{'prompt':' '}]:self.assertEqual(self.create(**value).status_code,422)
        self.assertEqual(self.client.post('/api/qwen/gpu/a10/start',headers=self.headers).status_code,409)
        self.assertEqual(self.client.post('/api/qwen/gpu/a100/stop',headers=self.headers).status_code,409)
    def test_busy_queue_and_cancel_never_calls_provider(self):
        job=self.create().json();self.engine.tick();self.provider.ensure_ready.assert_not_called();self.provider.submit.assert_not_called()
        self.client.post(f"/api/qwen/jobs/{job['id']}/cancel",headers=self.headers)
        self.assertFalse(self.store.pending());self.assertFalse(self.engine.wanted)
    def test_resume_receipt_does_not_resubmit(self):
        job=self.create().json();id=job['id'];self.store.update_job(id,_remote={'submittedAt':0},status='running')
        self.provider.poll.return_value={'stage':'cancelled','results':[],'progress':0}
        self.engine.tick();self.provider.submit.assert_not_called();self.provider.ensure_ready.assert_not_called()
        self.assertEqual(self.store.get('jobs',id)['status'],'cancelled')
    def test_capabilities_and_dimensions_are_consistent(self):
        caps=MODELS['qwen-image-2.1']['caps'];self.assertFalse(caps['mask']);self.assertFalse(caps['nativeTransparency']);self.assertTrue(caps['seed'])
        self.assertEqual(validate_size('qwen-image-2.1','2048x1152'),'2048x1152')
        for size in ['auto','3840x2160','1024x1000']:
            with self.assertRaises(ValueError):validate_size('qwen-image-2.1',size)

if __name__=='__main__':unittest.main()
