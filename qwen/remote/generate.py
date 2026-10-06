"""Prompt et références transmis directement au Qwen installé, sans filtre ajouté."""
from pathlib import Path
import hashlib,io,json,sys,threading,time,urllib.request
import torch
from PIL import Image
from diffusers import QwenImage21Pipeline

root=Path(sys.argv[1]);job=json.loads((root/'job.json').read_text());cfg=json.loads((root/'private.json').read_text())
state={'stage':'loading','progress':0,'results':[]};lock=threading.Lock();done=threading.Event()
class Cancelled(Exception):pass
def publish():
    with lock:data=json.loads(json.dumps(state))
    (root/'progress.json').write_text(json.dumps(data))
    try:
        request=urllib.request.Request(cfg['statusWrite'],data=json.dumps(data).encode(),method='PUT',headers={'x-ms-blob-type':'BlockBlob','Content-Type':'application/json'})
        urllib.request.urlopen(request,timeout=30).close()
    except Exception:pass
def heartbeat():
    while not done.wait(3):publish()
def cancel():
    if (root/'cancel').exists():raise Cancelled()
threading.Thread(target=heartbeat,daemon=True).start()
try:
    cancel();assert torch.cuda.is_available() and 'A100' in torch.cuda.get_device_name(0)
    images=[]
    for i,ref in enumerate(job['inputs']):
        path=root/f'input-{i}.png';urllib.request.urlretrieve(cfg['inputReads'][i],path)
        assert hashlib.sha256(path.read_bytes()).hexdigest()==ref['sha256']
        images.append(Image.open(path).convert('RGB'))
    cancel()
    pipe=QwenImage21Pipeline.from_pretrained(cfg['modelRoot']+'/model',torch_dtype=torch.bfloat16,local_files_only=True).to('cuda')
    pipe.set_progress_bar_config(disable=True)
    for index in range(job['count']):
        cancel();seed=(job['seed']+index)%(2**31);started=time.time()
        def progress(p,step,t,values):
            cancel()
            with lock:state.update(stage='generating',progress=round((index+(step+1)/job['steps'])/job['count']*95),image=index+1,step=step+1,steps=job['steps'])
            return values
        kwargs={'prompt':job['prompt'],'width':job['width'],'height':job['height'],'output_resolution':1024,'num_inference_steps':job['steps'],'true_cfg_scale':job['guidance'],'use_kv_cache':True,'generator':torch.Generator('cuda').manual_seed(seed),'callback_on_step_end':progress}
        if images:kwargs['image']=images
        if job.get('negativePrompt'):kwargs['negative_prompt']=job['negativePrompt']
        image=pipe(**kwargs).images[0].convert('RGB');buffer=io.BytesIO();image.save(buffer,format='PNG');data=buffer.getvalue()
        (root/f'result-{index}.png').write_bytes(data)
        request=urllib.request.Request(cfg['resultWrites'][index],data=data,method='PUT',headers={'x-ms-blob-type':'BlockBlob','Content-Type':'image/png'})
        urllib.request.urlopen(request,timeout=120).close()
        receipt={'index':index,'seed':seed,'width':image.width,'height':image.height,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'seconds':round(time.time()-started,2)}
        with lock:state['results'].append(receipt)
        publish()
    with lock:state.update(stage='complete',progress=100,complete=True)
except Cancelled:
    with lock:state.update(stage='cancelled',complete=True,cancelled=True)
except Exception as e:
    with lock:state.update(stage='failed',complete=True,error=type(e).__name__+': '+str(e).split('?')[0][:600])
finally:
    done.set();publish()
