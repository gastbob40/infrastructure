// Synthetic Kubernetes API and application backends for the native provider test.
// No cluster credentials, production data, or external service calls are used.
import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs';
import os from 'node:os';
import crypto from 'node:crypto';

const ip = Object.values(os.networkInterfaces()).flat().find(x => x.family === 'IPv4' && !x.internal).address;
const meta = name => ({ name, namespace: 'lab', uid: name, resourceVersion: '1' });
const annotation = 'traefik.ingress.kubernetes.io/';
const definitions = [
  ['http', 80, 9000],
  ['vault', 443, 9443, 'vault'],
  ['argo', 443, 9443, 'argo'],
  ['vault-bad-name', 443, 9443, 'vault-bad-name'],
  ['vault-untrusted', 443, 9443, 'vault-untrusted'],
];
const services = definitions.map(([name, port, targetPort, transport], index) => ({
  apiVersion: 'v1', kind: 'Service',
  metadata: {
    ...meta(name),
    annotations: transport ? {
      [annotation + 'service.serversscheme']: 'https',
      [annotation + 'service.serverstransport']: transport + '@file',
    } : {},
  },
  // Deliberately selectorless, like the external Home Assistant Service.
  spec: { type: 'ClusterIP', clusterIP: '10.43.0.' + (10 + index), ports: [{ name: port === 443 ? 'https' : 'http', port, targetPort, protocol: 'TCP' }] },
}));
const hosts = ['plain', 'auth', 'vault', 'argo', 'vault-bad-name', 'vault-untrusted', 'upload'];
const ingresses = hosts.map(name => {
  const secureBackend = name.startsWith('vault') || name === 'argo';
  return {
    apiVersion: 'networking.k8s.io/v1', kind: 'Ingress',
    metadata: {
      ...meta(name),
      annotations: {
        [annotation + 'router.entrypoints']: 'websecure',
        [annotation + 'router.tls']: 'true',
        [annotation + 'router.middlewares']: name === 'auth' ? 'hsts@file,oauth-errors@file,oauth-auth@file' : 'hsts@file',
      },
    },
    spec: {
      ingressClassName: 'traefik', tls: [{ hosts: [name + '.test'], secretName: 'frontend-tls' }],
      rules: [{ host: name + '.test', http: { paths: [{ path: '/', pathType: 'Prefix', backend: { service: { name: secureBackend ? name : 'http', port: { number: secureBackend ? 443 : 80 } } } }] } }],
    },
  };
});
ingresses.push({
  apiVersion: 'networking.k8s.io/v1', kind: 'Ingress',
  metadata: { ...meta('challenge'), annotations: { [annotation + 'router.entrypoints']: 'web' } },
  spec: {
    ingressClassName: 'traefik',
    rules: [{ host: 'plain.test', http: { paths: [{ path: '/.well-known/acme-challenge/probe', pathType: 'Exact', backend: { service: { name: 'http', port: { number: 80 } } } }] } }],
  },
});
ingresses.push({
  ...ingresses[0], metadata: { ...ingresses[0].metadata, ...meta('ignored') },
  spec: { ...ingresses[0].spec, ingressClassName: 'public', rules: [{ host: 'ignored.test', http: ingresses[0].spec.rules[0].http }] },
});
const endpointslices = definitions.map(([name, port, targetPort]) => ({
  apiVersion: 'discovery.k8s.io/v1', kind: 'EndpointSlice',
  metadata: { ...meta(name + '-slice'), labels: { 'kubernetes.io/service-name': name } },
  addressType: 'IPv4', ports: [{ name: port === 443 ? 'https' : 'http', port: targetPort, protocol: 'TCP' }],
  endpoints: [{ addresses: [ip], conditions: { ready: true } }],
}));
const resources = {
  ingresses, services, endpointslices,
  ingressclasses: [
    { apiVersion: 'networking.k8s.io/v1', kind: 'IngressClass', metadata: { name: 'traefik', resourceVersion: '1' }, spec: { controller: 'traefik.io/ingress-controller' } },
    { apiVersion: 'networking.k8s.io/v1', kind: 'IngressClass', metadata: { name: 'public', resourceVersion: '1' }, spec: { controller: 'k8s.io/ingress-nginx' } },
  ],
  namespaces: [{ apiVersion: 'v1', kind: 'Namespace', metadata: { name: 'lab', resourceVersion: '1' } }],
  secrets: [{ apiVersion: 'v1', kind: 'Secret', metadata: meta('frontend-tls'), type: 'kubernetes.io/tls', data: {
    'tls.crt': fs.readFileSync('/lab/frontend.crt').toString('base64'),
    'tls.key': fs.readFileSync('/lab/frontend.key').toString('base64'),
  } }],
  nodes: [], configmaps: [], pods: [], endpoints: [],
};
const counters = { backend: 0, auth: 0, signin: 0, requests: [], uploads: {} };
const emptyKinds = { nodes: 'Node', configmaps: 'ConfigMap', pods: 'Pod', endpoints: 'Endpoints' };

http.createServer((req, res) => {
  const url = new URL(req.url, 'http://mock');
  const segments = url.pathname.split('/').filter(Boolean);
  res.setHeader('content-type', 'application/json');
  if (url.pathname === '/stats') return res.end(JSON.stringify(counters));
  if (url.pathname === '/version') return res.end(JSON.stringify({ major: '1', minor: '35', gitVersion: 'v1.35.9', platform: 'linux/arm64' }));
  const kind = resources[segments.at(-1)] ? segments.at(-1) : resources[segments.at(-2)] ? segments.at(-2) : null;
  if (url.searchParams.get('watch') === 'true') {
    res.writeHead(200); res.flushHeaders();
    for (const object of resources[kind] || []) res.write(JSON.stringify({ type: 'ADDED', object }) + '\n');
    res.write(JSON.stringify({ type: 'BOOKMARK', object: {
      apiVersion: resources[kind]?.[0]?.apiVersion || 'v1', kind: resources[kind]?.[0]?.kind || emptyKinds[kind] || 'ConfigMap',
      metadata: { resourceVersion: '1', annotations: { 'k8s.io/initial-events-end': 'true' } },
    } }) + '\n');
    req.on('close', () => res.end()); return;
  }
  if (kind) {
    if (segments.at(-1) !== kind) {
      const item = resources[kind].find(x => x.metadata.name === segments.at(-1));
      if (item) return res.end(JSON.stringify(item));
    } else return res.end(JSON.stringify({
      apiVersion: resources[kind][0]?.apiVersion || 'v1',
      kind: (resources[kind][0]?.kind || emptyKinds[kind] || kind[0].toUpperCase() + kind.slice(1, -1)) + 'List',
      metadata: { resourceVersion: '1' }, items: resources[kind],
    }));
  }
  if (req.method !== 'GET') return res.end('{}');
  res.statusCode = 404;
  res.end(JSON.stringify({ apiVersion: 'v1', kind: 'Status', status: 'Failure', reason: 'NotFound', code: 404 }));
}).listen(8000, '0.0.0.0');

async function backend(req, res) {
  const url = new URL(req.url, 'http://backend');
  if (url.pathname === '/.well-known/acme-challenge/probe') return res.end('probe.challenge.synthetic');
  if (url.pathname === '/oauth2/auth') {
    counters.auth++;
    if (req.headers['x-lab-forbidden'] === 'yes') { res.statusCode = 403; return res.end('forbidden'); }
    if (req.headers['x-lab-authenticated'] !== 'yes') { res.statusCode = 401; return res.end('unauthorized'); }
    res.statusCode = 202;
    res.setHeader('X-Auth-Request-User', 'test-user');
    res.setHeader('X-Auth-Request-Email', 'test@example.invalid');
    return res.end();
  }
  if (url.pathname === '/oauth2/start') {
    counters.signin++;
    res.statusCode = 302;
    res.setHeader('Location', 'https://login.test/authorize?rd=' + encodeURIComponent(url.searchParams.get('rd') || ''));
    return res.end();
  }
  const upload = req.method === 'PUT';
  if (upload) counters.uploads[req.url] = { started: true, complete: false, bytes: 0 };
  let bytes = 0;
  const digest = crypto.createHash('sha256');
  for await (const chunk of req) {
    bytes += chunk.length; digest.update(chunk);
    if (upload) counters.uploads[req.url].bytes = bytes;
  }
  if (upload) counters.uploads[req.url].complete = true;
  counters.backend++;
  counters.requests.push({ host: req.headers.host, path: req.url, bytes });
  res.setHeader('content-type', 'application/json');
  res.end(JSON.stringify({
    status: 'synthetic-backend-ok', host: req.headers.host, path: req.url, bytes,
    user: req.headers['x-auth-request-user'] || null, email: req.headers['x-auth-request-email'] || null,
    tls: Boolean(req.socket.encrypted), servername: req.socket.servername || null,
    sha256: digest.digest('hex'), authorization: req.headers.authorization || null,
  }));
}
function upgrade(req, socket) {
  const accept = crypto.createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
  socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n');
  const payload = Buffer.from(JSON.stringify({ type: 'auth_required', synthetic: true }));
  socket.write(Buffer.concat([Buffer.from([0x81, payload.length]), payload]));
  socket.on('error', () => {}); socket.on('data', () => {});
}
http.createServer(backend).on('upgrade', upgrade).listen(9000, '0.0.0.0');
https.createServer({ key: fs.readFileSync('/lab/backend.key'), cert: fs.readFileSync('/lab/backend.crt') }, backend).on('upgrade', upgrade).listen(9443, '0.0.0.0');
