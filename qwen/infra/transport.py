"""Authenticated Azure Container Apps exec and checked file transfers.

Uses the same WebSocket protocol as Azure CLI. No public ingress, SSH server,
stored access token, resource update, scale change or shutdown command.
"""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import quote_plus

import websocket

for stream in (sys.stdout,sys.stderr):
    if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace')

CONFIG = json.loads(Path(__file__).with_name('a100_config.json').read_text())
EXIT = '__ASTRA_EXIT__'


def azure(*args):
    from .arm import command
    return command(args,CONFIG)


class Container:
    def __init__(self):
        self.config = CONFIG
        self.app = azure('containerapp', 'show', '-g', CONFIG['resource_group'], '-n', CONFIG['app'])
        self.revision = self.app['properties']['latestReadyRevisionName']
        replicas = azure('containerapp', 'replica', 'list', '-g', CONFIG['resource_group'], '-n', CONFIG['app'], '--revision', self.revision)
        ready = [r for r in replicas if any(c.get('ready') for c in r['properties']['containers'])]
        if not ready:
            raise RuntimeError('Aucune réplique A100 prête. Aucun démarrage ni arrêt automatique effectué.')
        replica = ready[0]
        self.replica = replica['name']
        container = next(c for c in replica['properties']['containers'] if c['name']==CONFIG['container'])
        endpoint = container['logStreamEndpoint'].split('/subscriptions/')[0].replace('https://', 'wss://')
        self.endpoint = (f"{endpoint}/subscriptions/{CONFIG['subscription']}/resourceGroups/{CONFIG['resource_group']}"
                         f"/containerApps/{CONFIG['app']}/revisions/{self.revision}/replicas/{self.replica}"
                         f"/containers/{CONFIG['container']}/exec")
        self._token = None
        self._token_time = 0
        self._connection = None
        self._closed = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self._closed.set()
        if self._connection:
            try:
                self._connection.send_binary(b'\x00\x00exit\n')
            except (OSError, websocket.WebSocketException):
                pass
            self._connection.close()
            self._connection = None

    def _keepalive(self, connection):
        while not self._closed.wait(20):
            try:
                connection.ping()
            except (OSError, websocket.WebSocketException):
                return

    def socket(self, script):
        if self._connection:
            connection = self._connection
        else:
            self._closed.clear()
            connection = self._open_shell()
            self._connection = connection
            threading.Thread(target=self._keepalive,args=(connection,),daemon=True).start()
        wrapped = "stty sane -echo -onlcr; (\n"+script+"\n); rc=$?; stty sane -echo -onlcr; printf '\\n"+EXIT+"%s\\n' \"$rc\""
        code = base64.b64encode(wrapped.encode()).decode()
        line = "eval \"$(printf '%s' "+shlex.quote(code)+" | base64 -d)\"\n"
        connection.send_binary(b'\x00\x00'+line.encode())
        return connection

    def _open_shell(self):
        if time.monotonic()-self._token_time>240:
            self._token = azure('rest', '--method', 'post', '--url',
                self.app['id']+'/getAuthToken?api-version=2025-01-01')['properties']['token']
            self._token_time = time.monotonic()
        # ACA splits the startup command on spaces without shell quote parsing.
        # Start a shell, disable echo, then feed the script on its terminal.
        command = '/bin/bash --noprofile --norc'
        for attempt in range(4):
            try:
                connection = websocket.create_connection(self.endpoint+'?command='+quote_plus(command),
                    header=['Authorization: Bearer '+self._token], timeout=60, enable_multithread=True)
                break
            except websocket.WebSocketBadStatusException as error:
                if attempt==3 or error.status_code not in [429,500,502,503]:raise
                delay=int((error.resp_headers or {}).get('retry-after',30))
                print(f'Console Azure temporairement indisponible ({error.status_code}), reprise dans {delay}s.',flush=True)
                deadline=time.monotonic()+delay
                while time.monotonic()<deadline:time.sleep(min(20,deadline-time.monotonic()))
        connection.send_binary(b'\x00\x04'+b'{"Width":160,"Height":40}')
        connection.send_binary(b"\x00\x00stty sane -echo -onlcr; PS1=; PS2=; bind 'set enable-bracketed-paste off'; printf '\\n__ASTRA_SHELL_READY__\\n'\n")
        output = ''
        while not re.search(r'\n__ASTRA_SHELL_READY__\r?\n',output):
            part = self.receive(connection)
            if part is None:
                connection.close()
                raise RuntimeError('Initialisation du shell Azure interrompue')
            output += part
        return connection

    @staticmethod
    def receive(connection):
        message = connection.recv()
        if not message:
            return None
        if isinstance(message, str):
            message = message.encode()
        if len(message)>1 and message[0]==0 and message[1] in (1,2):
            return message[2:].decode('utf-8', errors='replace')
        if message[0]==2:
            raise RuntimeError('Canal Azure : '+message[1:].decode(errors='replace'))
        return ''

    @staticmethod
    def finish(output, check=True):
        matches = list(re.finditer(EXIT+r'(\d+)', output))
        if not matches:
            raise RuntimeError('Connexion interrompue avant le résultat de la commande distante.\n'+output[-2000:])
        match = matches[-1]
        data = output[:match.start()].strip('\r\n')
        if check and int(match[1]):
            raise RuntimeError(f'Commande distante en échec ({match[1]}) :\n{data[-5000:]}')
        return data

    def run(self, script, stream=False, timeout=1800, check=True):
        connection = self.socket(script)
        output = ''
        deadline = time.monotonic()+timeout
        try:
            while time.monotonic()<deadline:
                try:
                    part = self.receive(connection)
                except websocket.WebSocketTimeoutException:
                    continue
                if part is None:
                    break
                output += part
                if stream:
                    print(part, end='', flush=True)
                if re.search(EXIT+r'\d+\r?\n', output):
                    break
            return self.finish(output, check)
        except BaseException:
            if not re.search(EXIT+r'\d+\r?\n', output):self.close()
            raise

    def checked_path(self, path):
        p = PurePosixPath(path)
        root = PurePosixPath(CONFIG['remote_root'])
        if not p.is_absolute() or '..' in p.parts or not p.is_relative_to(root) or p==root:
            raise ValueError('Le fichier distant doit rester dans '+str(root))
        return shlex.quote(str(p))

    def put(self, local, remote):
        if Path(local).stat().st_size>=200000:
            from .blob import upload
            digest=hashlib.sha256(Path(local).read_bytes()).hexdigest()
            target=self.checked_path(remote)
            url=upload(local,'transfers/'+digest+'/'+Path(local).name)
            code="import urllib.request,pathlib; p=pathlib.Path("+repr(remote)+"); p.parent.mkdir(parents=True,exist_ok=True); urllib.request.urlretrieve("+repr(url)+",str(p)+'.part')"
            python=CONFIG['remote_root']+'/blender-4.5.1-linux-x64/4.5/python/bin/python3.11'
            self.run('SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt '+shlex.join([python,'-c',code])+f" && printf '%s  %s\\n' {digest} {target}.part | sha256sum -c - && mv -- {target}.part {target}")
            return {'bytes':Path(local).stat().st_size,'sha256':digest}
        # Short control files travel as acknowledged shell commands. A PTY is
        # unsuitable for bulk binary stdin: its terminal discipline can eat it.
        if Path(local).stat().st_size<200000:
            data=Path(local).read_bytes();target=self.checked_path(remote)
            parent=shlex.quote(str(PurePosixPath(remote).parent))
            self.run(f'mkdir -p {parent}; : > {target}.part')
            for offset in range(0,len(data),1200):
                chunk=base64.b64encode(data[offset:offset+1200]).decode()
                self.run(f"printf '%s' {chunk} | base64 -d >> {target}.part")
            digest=hashlib.sha256(data).hexdigest()
            self.run(f"printf '%s  %s\\n' {digest} {target}.part | sha256sum -c - && mv -- {target}.part {target}")
            return {'bytes':len(data),'sha256':digest}
    def get(self, remote, local):
        self.checked_path(remote)
        from .blob import signed,download
        import uuid
        blob='results/'+uuid.uuid4().hex+'/'+Path(local).name
        url=signed(blob,write=True)
        python=CONFIG['remote_root']+'/blender-4.5.1-linux-x64/4.5/python/bin/python3.11'
        code="import urllib.request,pathlib,hashlib; data=pathlib.Path("+repr(remote)+").read_bytes(); request=urllib.request.Request("+repr(url)+",data=data,method='PUT',headers={'x-ms-blob-type':'BlockBlob'}); urllib.request.urlopen(request).close(); print(hashlib.sha256(data).hexdigest())"
        digest=self.run('SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt '+shlex.join([python,'-c',code])).strip()
        local=Path(local);local.parent.mkdir(parents=True,exist_ok=True);temporary=local.with_suffix(local.suffix+'.part')
        download(blob,temporary)
        if hashlib.sha256(temporary.read_bytes()).hexdigest()!=digest:raise RuntimeError('Somme SHA-256 incorrecte après téléchargement')
        temporary.replace(local)
        return {'bytes':local.stat().st_size,'sha256':digest}
