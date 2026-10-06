"""Private short-lived exchange storage. Account keys never leave this process."""
from datetime import datetime,timedelta,timezone
from azure.storage.blob import BlobServiceClient,BlobSasPermissions,generate_blob_sas
from azure.core.exceptions import ResourceExistsError
ACCOUNT='stastraa1003db80e7b'
CONTAINER='baking-jobs'
_service=None
_key=None
_key_expiry=None


def service():
    global _service,_key
    if _service is None:
        from .arm import credential
        _service=BlobServiceClient('https://'+ACCOUNT+'.blob.core.windows.net',credential=credential())
    return _service


def signed(name,write=False):
    global _key,_key_expiry
    client=service();now=datetime.now(timezone.utc)
    if _key is None or _key_expiry<now+timedelta(hours=25):
        _key_expiry=now+timedelta(days=3)
        _key=client.get_user_delegation_key(now-timedelta(minutes=5),_key_expiry)
    token=generate_blob_sas(ACCOUNT,CONTAINER,name,user_delegation_key=_key,
        permission=BlobSasPermissions(read=not write,create=write,write=write),
        start=datetime.now(timezone.utc)-timedelta(minutes=5),expiry=datetime.now(timezone.utc)+timedelta(hours=24),protocol='https')
    return f'https://{ACCOUNT}.blob.core.windows.net/{CONTAINER}/{name}?{token}'


def upload(path,name):
    with open(path,'rb') as stream:service().get_blob_client(CONTAINER,name).upload_blob(stream,overwrite=True,max_concurrency=4)
    return signed(name)


def download(name,path):
    # Bounded sequential ranges avoid leaving sparse, incomplete local files
    # when a concurrent range request stalls on a large render archive.
    blob=service().get_blob_client(CONTAINER,name)
    options=dict(connection_timeout=20,read_timeout=45,retry_total=2)
    size=blob.get_blob_properties(**options).size
    block_size=4*1024*1024
    with open(path,'wb') as stream:
        for offset in range(0,size,block_size):
            length=min(block_size,size-offset)
            data=blob.download_blob(offset=offset,length=length,max_concurrency=1,**options).readall()
            if len(data)!=length:raise IOError('Incomplete private transfer range')
            stream.write(data)
