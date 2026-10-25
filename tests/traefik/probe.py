import json,subprocess,time,urllib.request,sys,socket,ssl,os
from pathlib import Path
LAB=Path(__file__).parent
PORT_HTTP=os.environ.get('LAB_PORT_HTTP','19880')
PORT_HTTPS=os.environ.get('LAB_PORT_HTTPS','19843')
PORT_API=os.environ.get('LAB_PORT_API','19808')
PORT_MOCK=os.environ.get('LAB_PORT_MOCK','19800')
CERT_DIR=Path(os.environ.get('LAB_CERT_DIR',str(LAB)))
RESULT_DIR=Path(os.environ.get('LAB_RESULT_DIR',str(LAB)))
mode=sys.argv[1] if len(sys.argv)>1 else 'unbuffered'
def curl(host='plain.test',path='/',scheme='https',data=None,headers=None,verify=True):
    args=['curl','--silent','--show-error','--noproxy','*','--max-time','10','--connect-to',f'{host}:{443 if scheme=="https" else 80}:127.0.0.1:{PORT_HTTPS if scheme=="https" else PORT_HTTP}','--dump-header','-','--write-out','\n%{http_code}']
    if scheme=='https': args+=['--cacert',str(CERT_DIR/'frontend.crt')] if verify else ['--insecure']
    for k,v in (headers or {}).items():args+=['-H',k+': '+v]
    if data is not None:args+=['--data-binary','@-']
    args+=[f'{scheme}://{host}{path}']
    r=subprocess.run(args,input=data,text=True,capture_output=True)
    chunks=r.stdout.rsplit('\n',1);status=int(chunks[-1] or '0') if chunks else 0
    text=chunks[0]; head,_,body=text.partition('\r\n\r\n') if '\r\n\r\n' in text else text.partition('\n\n')
    hs={}
    for line in head.splitlines()[1:]:
        if ':' in line:
            k,v=line.split(':',1);hs[k.lower()]=v.strip()
    try: payload=json.loads(body)
    except: payload=body[:100]
    return {'curl_exit':r.returncode,'status':status,'location':hs.get('location'),'payload':payload,'error':r.stderr.strip() if r.returncode else None}
def stats():
    return json.load(urllib.request.urlopen('http://127.0.0.1:'+PORT_MOCK+'/stats',timeout=3))
def websocket():
    try:
        ctx=ssl.create_default_context(cafile=str(CERT_DIR/'frontend.crt'))
        with socket.create_connection(('127.0.0.1',int(PORT_HTTPS)),timeout=5) as sock:
            with ctx.wrap_socket(sock,server_hostname='plain.test') as conn:
                conn.sendall(b'GET /api/websocket HTTP/1.1\r\nHost: plain.test\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n')
                response=b''
                while b'auth_required' not in response and len(response)<4096:
                    chunk=conn.recv(4096)
                    if not chunk:break
                    response+=chunk
                return {'status_101':response.startswith(b'HTTP/1.1 101'),'received_auth_required_frame':b'auth_required' in response}
    except Exception as e:return {'error':str(e)}
for n in range(40):
    r=curl()
    if r['status']==200:break
    time.sleep(.5)
results={'mode':mode,'plain_tls':r,'http_redirect':curl(scheme='http'),'https_self_signed_backend':curl('secure.test'),'argo_passthrough_wrong_cert':curl('argo-bad.test'),'argo_passthrough_insecure_observation':curl('argo-bad.test',verify=False),'argo_corrected_frontend_tls':curl('argo-good.test')}
before=stats()['backend']
results['auth_anonymous']=curl('auth.test','/private/item?x=1')
after=stats()['backend']
results['auth_backend_called_anonymous']=after-before
results['auth_authenticated']=curl('auth.test','/private/item?x=1',headers={'X-Lab-Authenticated':'yes'})
results['auth_host_variable_fixed']=curl('auth-fixed.test','/private/item?x=1&y=2')
results['auth_auto_redirect']=curl('auth-auto.test','/private/item?x=1&y=2')
results['http01_plain_http']=curl('plain.test','/.well-known/acme-challenge/probe',scheme='http')
results['websocket']=websocket()
results['upload_512_bytes']=curl('upload.test',data='x'*512)
results['upload_2048_bytes']=curl('upload.test',data='x'*2048)
try:
    raw=json.load(urllib.request.urlopen('http://127.0.0.1:'+PORT_API+'/api/rawdata',timeout=3))
    results['provider_router_count']=len([k for k in raw.get('routers',{}) if k.endswith('@kubernetesingressnginx')])
    results['provider_tcp_router_count']=len([k for k in raw.get('tcpRouters',{}) if k.endswith('@kubernetesingressnginx')])
    results['configuration_errors']=raw.get('errors',[])
except Exception as e:results['configuration_inspection_error']=str(e)
(RESULT_DIR/f'results-{mode}.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))
