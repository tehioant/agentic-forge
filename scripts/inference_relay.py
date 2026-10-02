"""Container-local HTTP relay to a mounted, short-lived Unix inference socket.

No host TCP listener or firewall change. The host broker authenticates requests.
The subprocess retains the same container/user/mount restrictions.
"""
import argparse
import selectors
import socket
import socketserver
import subprocess
import threading

class Relay(socketserver.BaseRequestHandler):
    def handle(self):
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as upstream:
            upstream.settimeout(10)
            upstream.connect(self.server.socket_path)
            self.request.settimeout(10)
            with selectors.DefaultSelector() as selector:
                selector.register(self.request,selectors.EVENT_READ,upstream)
                selector.register(upstream,selectors.EVENT_READ,self.request)
                while True:
                    events=selector.select(timeout=300)
                    if not events:
                        return
                    for key,_ in events:
                        chunk=key.fileobj.recv(65536)
                        if not chunk:
                            return
                        key.data.sendall(chunk)

class Server(socketserver.ThreadingTCPServer):
    daemon_threads=True
    allow_reuse_address=True

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--socket',required=True)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    if not command:
        parser.error('Missing agent command')
    with Server(('127.0.0.1',args.port),Relay) as server:
        server.socket_path=args.socket
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            result=subprocess.run(command,check=False)
        finally:
            server.shutdown()
            thread.join(timeout=5)
        raise SystemExit(result.returncode)
