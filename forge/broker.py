"""Short-lived, fixed-upstream Codex inference broker.

Workers receive a random per-attempt capability, never the provider's OAuth
access/refresh grant. No arbitrary URL, redirect, model or credential forwarding.
"""
from __future__ import annotations
import base64

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import socketserver
import threading
import time
import urllib.error
import urllib.request

UPSTREAM='https://chatgpt.com/backend-api/codex'
PATHS={'/responses','/responses/compact'}

class UnixHTTPServer(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads=True

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

class Broker:
    def __init__(self, auth: Path, bind: str | Path, budget: float, model: str='gpt-6.1-sol'):
        store=json.loads(auth.read_text())
        entries=store.get('credential_pool',{}).get('openai-codex',[])
        if len(entries)!=1 or entries[0].get('base_url')!=UPSTREAM:
            raise ValueError('Expected one fixed-upstream access-only inference grant')
        self.access=entries[0]['access_token']
        if entries[0].get('refresh_token'):
            raise ValueError('Broker must not receive a refresh grant')
        self.capability=secrets.token_urlsafe(32)
        self.model=model
        self.deadline=time.monotonic()+budget
        self.requests=0
        self.lock=threading.Lock()
        self.active=threading.BoundedSemaphore(2)
        payload=self.access.split('.')[1]
        claims=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
        if claims.get('exp',0)<=time.time()+budget+30:
            raise RuntimeError('Inference grant expires within the attempt budget; refresh snapshot securely')
        self.account=claims.get('https://api.openai.com/auth',{}).get('chatgpt_account_id','')
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        broker=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.0'
            def log_message(self,format,*args):
                pass
            def do_POST(self):
                expected='Bearer '+broker.capability
                if not hmac.compare_digest(self.headers.get('Authorization',''),expected):
                    self.send_error(401,'Invalid attempt capability'); return
                if self.path not in PATHS:
                    self.send_error(404,'Endpoint not allowed'); return
                if time.monotonic()>=broker.deadline:
                    self.send_error(410,'Attempt expired'); return
                if self.headers.get('Transfer-Encoding'):
                    self.send_error(400,'Chunked requests not supported'); return
                try:
                    length=int(self.headers.get('Content-Length','0'))
                except ValueError:
                    self.send_error(400); return
                if not 0<length<=4*1024*1024:
                    self.send_error(413); return
                self.connection.settimeout(min(30,max(1,broker.deadline-time.monotonic())))
                try:
                    body=self.rfile.read(length)
                    payload=json.loads(body)
                    if not isinstance(payload,dict) or payload.get('model')!=broker.model:
                        self.send_error(403,'Model not allowed'); return
                except (ValueError, OSError):
                    self.send_error(400,'Invalid JSON request'); return
                with broker.lock:
                    broker.requests+=1
                    if broker.requests>120:
                        self.send_error(429,'Attempt request cap'); return
                if not broker.active.acquire(blocking=False):
                    self.send_error(429,'Attempt concurrency cap'); return
                try:
                    headers={'Authorization':'Bearer '+broker.access,'Content-Type':'application/json',
                             'Accept':self.headers.get('Accept','text/event-stream'),
                             'User-Agent':'Agentic-Forge/0.1','OpenAI-Beta':'responses=experimental'}
                    if broker.account:
                        headers['ChatGPT-Account-Id']=broker.account
                    request=urllib.request.Request(UPSTREAM+self.path,data=body,headers=headers,method='POST')
                    with broker.opener.open(request,timeout=min(300,max(1,broker.deadline-time.monotonic()))) as upstream:
                        self.send_response(upstream.status)
                        self.send_header('Content-Type',upstream.headers.get('Content-Type','application/json'))
                        self.send_header('Connection','close')
                        self.end_headers()
                        size=0
                        while time.monotonic()<broker.deadline:
                            chunk=upstream.read1(65536)
                            if not chunk:
                                break
                            size+=len(chunk)
                            if size>16*1024*1024:
                                self.close_connection=True
                                break
                            self.wfile.write(chunk)
                            self.wfile.flush()
                except urllib.error.HTTPError as error:
                    self.send_error(error.code,'Inference upstream rejected request')
                except (OSError, urllib.error.URLError):
                    # Never emit provider header/body/credentials to the worker.
                    self.close_connection=True
                finally:
                    broker.active.release()
        self.socket_path=bind if isinstance(bind,Path) else None
        if self.socket_path:
            self.server=UnixHTTPServer(str(self.socket_path),Handler)
            self.socket_path.chmod(0o600)
            self.url='http://127.0.0.1:8765'
        else:
            # Loopback TCP exists solely for isolated broker policy unit tests.
            if bind!='127.0.0.1':
                raise ValueError('TCP broker binding is restricted to loopback')
            self.server=ThreadingHTTPServer((bind,0),Handler)
            self.server.daemon_threads=True
            self.url=f'http://{bind}:{self.server.server_port}'
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self,*args):
        self.deadline=0
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.socket_path:
            self.socket_path.unlink(missing_ok=True)

    def worker_auth(self):
        return {'version':1,'active_provider':'openai-codex','providers':{},
                'credential_pool':{'openai-codex':[{'id':'forge-attempt','label':'Ephemeral inference capability',
                'source':'manual','auth_type':'oauth','access_token':self.capability,
                'base_url':self.url,'priority':0,'request_count':0}]}}
