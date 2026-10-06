"""Restaure le modèle connu ; aucune installation ni modification de ses poids."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,shutil,subprocess,sys,tarfile,threading,time,urllib.request

cfg=json.loads(Path(sys.argv[1]).read_text());root=Path(cfg['modelRoot']);work=Path(sys.argv[1]).parent
state={'stage':'preparing','downloadedBytes':0};lock=threading.Lock();done=threading.Event()
def publish():
    (work/'progress.json').write_text(json.dumps(state))
    try:
        request=urllib.request.Request(cfg['statusWrite'],data=json.dumps(state).encode(),method='PUT',headers={'x-ms-blob-type':'BlockBlob','Content-Type':'application/json'})
        urllib.request.urlopen(request,timeout=30).close()
    except Exception:pass
def heartbeat():
    while not done.wait(3):publish()
threading.Thread(target=heartbeat,daemon=True).start()
try:
    marker=root/'qwen-studio-model.json'
    cached=marker.exists() and json.loads(marker.read_text()).get('sha256')==cfg['archiveSha256']
    persistent=Path(cfg['persistentCacheRoot']) if cfg.get('persistentCacheRoot') else None
    if persistent and not cached:
        state.update(stage='cache-checking',cache=True);publish()
        assert persistent.parent.is_mount(),'Le cache Azure Files n’est pas monté.'
        manifest=json.loads((persistent/'manifest.json').read_text())
        assert manifest['archiveSha256']==cfg['archiveSha256'],'Version du cache incorrecte.'
        for item in manifest['modelFiles']:
            relative=Path(item['path'])
            assert not relative.is_absolute() and '..' not in relative.parts
            assert (persistent/relative).stat().st_size==item['bytes'],'Fichier du cache incomplet.'
        # Parallel, verified local copies avoid slow sequential mmap reads over SMB.
        state.update(stage='cache-model',totalBytes=sum(item['bytes'] for item in manifest['modelFiles']),downloadedBytes=0);publish()
        root.mkdir(parents=True,exist_ok=True)
        def copy_model(item):
            source=persistent/item['path'];target=root/item['path'];target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_name(target.name+'.part');h=hashlib.sha256()
            with source.open('rb') as src,temporary.open('wb') as dst:
                while chunk:=src.read(8*1024**2):
                    dst.write(chunk);h.update(chunk)
                    with lock:state['downloadedBytes']+=len(chunk)
            assert h.hexdigest()==item['sha256'],'Poids du cache incorrects.'
            temporary.replace(target)
        with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(copy_model,manifest['modelFiles']))
        environments=manifest.get('environmentShards') or [manifest['environment']]
        state.update(stage='cache-environment',totalBytes=sum(env['bytes'] for env in environments),downloadedBytes=0);publish()
        def copy_environment(env):
            source=persistent/env['file'];archive=work/env['file']
            assert source.parent==persistent and source.stat().st_size==env['bytes']
            h=hashlib.sha256()
            with source.open('rb') as src,archive.open('wb') as dst:
                while chunk:=src.read(8*1024**2):
                    dst.write(chunk);h.update(chunk)
                    with lock:state['downloadedBytes']+=len(chunk)
            assert h.hexdigest()==env['sha256'],'Environnement du cache incorrect.'
            return archive
        with ThreadPoolExecutor(max_workers=8) as pool:archives=list(pool.map(copy_environment,environments))
        state['stage']='cache-extracting';publish()
        def extract_environment(archive):
            with tarfile.open(archive) as tar:
                assert all(not m.name.startswith('/') and '..' not in Path(m.name).parts for m in tar.getmembers())
                for member in tar:
                    target=root/member.name;target.parent.mkdir(parents=True,exist_ok=True)
                    if member.isdir():target.mkdir(parents=True,exist_ok=True)
                    else:tar.extract(member,root)
            archive.unlink()
        with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(extract_environment,archives))
        marker.write_text(json.dumps({'sha256':cfg['archiveSha256'],'cache':str(persistent),'preparedAt':time.time()}))
        cached=True
    if not cached:
        assert shutil.disk_usage('/tmp').free>90*1024**3,'Espace disque insuffisant pour le modèle.'
        state['stage']='restoring';archive=work/'environment.tar';size=cfg['archiveBytes']
        with archive.open('wb') as f:f.truncate(size)
        block=64*1024*1024
        def part(start):
            end=min(start+block,size)-1
            for attempt in range(3):
                received=0
                try:
                    request=urllib.request.Request(cfg['archiveRead'],headers={'Range':f'bytes={start}-{end}'})
                    with urllib.request.urlopen(request,timeout=120) as src,archive.open('r+b',buffering=0) as target:
                        assert src.status==206
                        target.seek(start)
                        while chunk:=src.read(8*1024*1024):target.write(chunk);received+=len(chunk)
                    assert received==end-start+1
                    with lock:state['downloadedBytes']+=received
                    return
                except Exception:
                    if attempt==2:raise
                    time.sleep(2)
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(part,range(0,size,block)))
        state['stage']='checking';h=hashlib.sha256()
        with archive.open('rb') as f:
            while chunk:=f.read(16*1024*1024):h.update(chunk)
        assert h.hexdigest()==cfg['archiveSha256'],'Empreinte du modèle incorrecte.'
        state['stage']='extracting';root.mkdir(parents=True,exist_ok=True)
        with tarfile.open(archive) as tar:
            assert all(not m.name.startswith('/') and '..' not in Path(m.name).parts for m in tar.getmembers())
            tar.extractall(root)
        marker.write_text(json.dumps({'sha256':cfg['archiveSha256'],'preparedAt':time.time()}))
    python=root/'venv/bin/python';assert python.exists()
    subprocess.run([str(python),'-c',"import torch;from diffusers import QwenImage21Pipeline;assert torch.cuda.is_available();assert 'A100' in torch.cuda.get_device_name(0)"],check=True)
    state.update(stage='ready',complete=True)
except Exception as e:state.update(stage='failed',error=type(e).__name__+': '+str(e).split('?')[0][:400])
finally:
    done.set();publish()
