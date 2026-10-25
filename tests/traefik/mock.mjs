import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs';
import os from 'node:os';
import crypto from 'node:crypto';

const ip = Object.values(os.networkInterfaces()).flat().find(x=>x.family==='IPv4'&&!x.internal).address;
const meta = name => ({name,namespace:'lab',uid:name,resourceVersion:'1'});
const httpSvc = {apiVersion:'v1',kind:'Service',metadata:meta('http'),spec:{type:'ClusterIP',clusterIP:'10.43.0.10',ports:[{name:'http',port:80,targetPort:9000,protocol:'TCP'}]}};
const httpsSvc = {apiVersion:'v1',kind:'Service',metadata:meta('https'),spec:{type:'ClusterIP',clusterIP:'10.43.0.11',ports:[{name:'https',port:443,targetPort:9443,protocol:'TCP'}]}};
const hosts = ['plain.test','auth.test','auth-fixed.test','auth-auto.test','secure.test','argo-bad.test','argo-good.test','upload.test','challenge.test'];
const annotations = {
 'plain.test': {},
 'auth.test': {
  'nginx.ingress.kubernetes.io/auth-url':`http://${process.env.MOCK_HOST||'traefik-provider-lab-mock'}:9000/oauth2/auth`,
  'nginx.ingress.kubernetes.io/auth-signin':'https://login.test/start?rd=$scheme://$http_host$request_uri',
  'nginx.ingress.kubernetes.io/auth-response-headers':'X-Auth-Request-User, X-Auth-Request-Email',
 },
 'secure.test':{'nginx.ingress.kubernetes.io/backend-protocol':'HTTPS'},
 'argo-bad.test':{'nginx.ingress.kubernetes.io/backend-protocol':'HTTPS','nginx.ingress.kubernetes.io/ssl-passthrough':'true'},
 'argo-good.test':{'nginx.ingress.kubernetes.io/backend-protocol':'HTTPS'},
 'upload.test':{'nginx.ingress.kubernetes.io/proxy-body-size':'1k'},
 'challenge.test':{},
};
annotations['auth-fixed.test']={...annotations['auth.test'],'nginx.ingress.kubernetes.io/auth-signin':'https://login.test/start?rd=$scheme://$host$request_uri'};
annotations['auth-auto.test']={...annotations['auth.test'],'nginx.ingress.kubernetes.io/auth-signin':'https://login.test/start'};
const ingresses=hosts.map(host=>({apiVersion:'networking.k8s.io/v1',kind:'Ingress',metadata:{...meta(host.split('.')[0]),annotations:annotations[host]},spec:{ingressClassName:'public',tls:[{hosts:[host],secretName:'frontend-tls'}],rules:[{host,http:{paths:[{path:'/',pathType:'Prefix',backend:{service:{name:host.startsWith('argo-')||host==='secure.test'?'https':'http',port:{number:host.startsWith('argo-')||host==='secure.test'?443:80}}}}]}}]}}));
const challenge=ingresses.find(x=>x.metadata.name==='challenge');
delete challenge.spec.tls;
challenge.spec.rules[0].host='plain.test';
challenge.spec.rules[0].http.paths[0].path='/.well-known/acme-challenge/probe';
challenge.spec.rules[0].http.paths[0].pathType='Exact';
const endpointslices=[['http',9000],['https',9443]].map(([name,port])=>({apiVersion:'discovery.k8s.io/v1',kind:'EndpointSlice',metadata:{...meta(name+'-slice'),labels:{'kubernetes.io/service-name':name}},addressType:'IPv4',ports:[{name,port,protocol:'TCP'}],endpoints:[{addresses:[ip],conditions:{ready:true},targetRef:{kind:'Pod',namespace:'lab',name:'backend'}}]}));
const endpoints=[['http',9000],['https',9443]].map(([name,port])=>({apiVersion:'v1',kind:'Endpoints',metadata:meta(name),subsets:[{addresses:[{ip,targetRef:{kind:'Pod',namespace:'lab',name:'backend'}}],ports:[{name,port,protocol:'TCP'}]}]}));
const resources={
 ingresses,services:[httpSvc,httpsSvc],endpointslices,endpoints,
 ingressclasses:[{apiVersion:'networking.k8s.io/v1',kind:'IngressClass',metadata:{name:'public',resourceVersion:'1'},spec:{controller:'k8s.io/ingress-nginx'}}],
 namespaces:[{apiVersion:'v1',kind:'Namespace',metadata:{name:'lab',resourceVersion:'1'}}],
 secrets:[{apiVersion:'v1',kind:'Secret',metadata:meta('frontend-tls'),type:'kubernetes.io/tls',data:{'tls.crt':fs.readFileSync('/lab/frontend.crt').toString('base64'),'tls.key':fs.readFileSync('/lab/frontend.key').toString('base64')}}],
 pods:[{apiVersion:'v1',kind:'Pod',metadata:meta('backend'),spec:{containers:[{name:'backend',ports:[{containerPort:9000},{containerPort:9443}]}]},status:{phase:'Running',podIP:ip,conditions:[{type:'Ready',status:'True'}]}}],
 nodes:[],configmaps:[],
};
const counters={backend:0,auth:0,requests:[]};
http.createServer((req,res)=>{
 const u=new URL(req.url,'http://mock');
 const segments=u.pathname.split('/').filter(Boolean);
 console.log(JSON.stringify({api:req.method,path:u.pathname,watch:u.searchParams.get('watch')}));
 res.setHeader('content-type','application/json');
 if(u.pathname==='/version')return res.end(JSON.stringify({major:'1',minor:'35',gitVersion:'v1.35.9',platform:'linux/arm64'}));
 if(u.pathname==='/stats')return res.end(JSON.stringify(counters));
 const kind=resources[segments.at(-1)]?segments.at(-1):resources[segments.at(-2)]?segments.at(-2):null;
 if(u.searchParams.get('watch')==='true'){
  res.writeHead(200);res.flushHeaders();
  for(const object of resources[kind]||[])res.write(JSON.stringify({type:'ADDED',object})+'\n');
  res.write(JSON.stringify({type:'BOOKMARK',object:{apiVersion:resources[kind]?.[0]?.apiVersion||'v1',kind:resources[kind]?.[0]?.kind||'ConfigMap',metadata:{resourceVersion:'1',annotations:{'k8s.io/initial-events-end':'true'}}}})+'\n');
  req.on('close',()=>res.end());return;
 }
 if(kind){
  if(segments.at(-1)!==kind){const item=resources[kind].find(x=>x.metadata.name===segments.at(-1));if(item)return res.end(JSON.stringify(item));}
  else return res.end(JSON.stringify({apiVersion:resources[kind][0]?.apiVersion||'v1',kind:(resources[kind][0]?.kind||kind[0].toUpperCase()+kind.slice(1,-1))+'List',metadata:{resourceVersion:'1'},items:resources[kind]}));
 }
 if(req.method!=='GET')return res.end('{}');
 res.statusCode=404;res.end(JSON.stringify({apiVersion:'v1',kind:'Status',status:'Failure',message:'unknown resource',reason:'NotFound',code:404}));
}).listen(8000,'0.0.0.0');
async function backend(req,res){
 if(req.url==='/.well-known/acme-challenge/probe'){res.end('probe.challenge.synthetic');return;}
 if(req.url.startsWith('/oauth2/auth')){
  counters.auth++;if(req.headers['x-lab-authenticated']!=='yes'){res.statusCode=401;res.end('unauthorized');return;}
  res.statusCode=202;res.setHeader('X-Auth-Request-User','test-user');res.setHeader('X-Auth-Request-Email','test@example.invalid');res.end();return;
 }
 let bytes=0;for await(const b of req)bytes+=b.length;
 counters.backend++;counters.requests.push({host:req.headers.host,path:req.url,bytes});
 res.setHeader('content-type','application/json');
 res.end(JSON.stringify({status:'synthetic-backend-ok',host:req.headers.host,path:req.url,bytes,user:req.headers['x-auth-request-user']||null,tls:Boolean(req.socket.encrypted)}));
}
function upgrade(req,socket){
 const accept=crypto.createHash('sha1').update(req.headers['sec-websocket-key']+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
 socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+'\r\n\r\n');
 const payload=Buffer.from(JSON.stringify({type:'auth_required',synthetic:true}));
 socket.write(Buffer.concat([Buffer.from([0x81,payload.length]),payload]));
 socket.on('error',()=>{});socket.on('data',()=>{});
}
http.createServer(backend).on('upgrade',upgrade).listen(9000,'0.0.0.0');
https.createServer({key:fs.readFileSync('/lab/backend.key'),cert:fs.readFileSync('/lab/backend.crt')},backend).on('upgrade',upgrade).listen(9443,'0.0.0.0');
console.log(JSON.stringify({started:true,ip}));
