"""Azure REST using a scoped service identity in production, Azure CLI locally."""
import os, threading, time
import requests
from azure.identity import ClientSecretCredential, AzureCliCredential

_credential=None
_lock=threading.Lock()
def credential():
    global _credential
    with _lock:
        if _credential is None:
            if os.environ.get('QWEN_AZURE_CLIENT_ID'):
                _credential=ClientSecretCredential(os.environ['QWEN_AZURE_TENANT_ID'],os.environ['QWEN_AZURE_CLIENT_ID'],os.environ['QWEN_AZURE_CLIENT_SECRET'])
            else:_credential=AzureCliCredential(process_timeout=60)
    return _credential

def request(method,path,body=None):
    url=path if path.startswith('https://management.azure.com/') else 'https://management.azure.com'+path
    if not url.startswith('https://management.azure.com/'):raise ValueError('Hôte Azure incorrect.')
    response=None
    for attempt in range(3):
        token=credential().get_token('https://management.azure.com/.default').token
        response=requests.request(method,url,json=body,headers={'Authorization':'Bearer '+token},timeout=(15,75))
        if response.status_code not in [429,502,503,504]:break
        time.sleep(min(15,int(response.headers.get('Retry-After','3'))))
    if not response.ok:
        try:error=response.json().get('error',{});code=error.get('code','AzureError')
        except ValueError:code='AzureError'
        raise RuntimeError(f'Azure {response.status_code} ({code}). Vérifie la connexion et les droits du service Qwen.')
    return response.json() if response.content else None

def command(args,config):
    def value(*names):
        for name in names:
            if name in args:return args[args.index(name)+1]
        return None
    base=f"/subscriptions/{config['subscription']}/resourceGroups/{config['resource_group']}/providers/Microsoft.App/containerApps/{config['app']}"
    version='api-version=2025-01-01'
    if args[:2]==('rest','--method'):
        return request(value('--method'),value('--url'))
    if args[:2]==('containerapp','show'):return request('GET',base+'?'+version)
    if args[:3]==('containerapp','revision','list'):return request('GET',base+'/revisions?'+version)['value']
    revision=value('--revision')
    if args[:3]==('containerapp','revision','show'):return request('GET',base+'/revisions/'+revision+'?'+version)
    if args[:3] in [('containerapp','revision','activate'),('containerapp','revision','deactivate')]:
        return request('POST',base+'/revisions/'+revision+'/'+args[2]+'?'+version)
    if args[:3]==('containerapp','replica','list'):return request('GET',base+'/revisions/'+revision+'/replicas?'+version)['value']
    raise ValueError('Opération Azure non prise en charge par Image Lab.')

def a10_state(subscription):
    path=f'/subscriptions/{subscription}/resourceGroups/RG-COMFYUI-POL/providers/Microsoft.Compute/virtualMachines/comfyui-a10/instanceView?api-version=2024-07-01'
    data=request('GET',path)
    state=next((s['code'].split('/')[-1] for s in data.get('statuses',[]) if s['code'].startswith('PowerState/')),'unknown')
    return {'id':'a10','power':state,'running':state=='running','supported':False,'memoryGB':24,
        'reason':'Qwen 2.1 BF16 utilise plus de 32 Go de mémoire GPU. L’A10 a 24 Go ; le déport en RAM n’est pas validé sur cette installation.','checkedAt':time.time()}
