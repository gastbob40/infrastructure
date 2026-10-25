"""Exercise the native provider using loopback connections and synthetic data."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import time
import urllib.request

HTTP = os.environ['LAB_PORT_HTTP']
HTTPS = os.environ['LAB_PORT_HTTPS']
API = os.environ['LAB_PORT_API']
MOCK = os.environ['LAB_PORT_MOCK']
CERT = Path(os.environ['LAB_CERT_DIR']) / 'frontend.crt'


def request(host='plain.test', path='/', scheme='https', headers=None):
    port = HTTPS if scheme == 'https' else HTTP
    arguments = ['curl', '--silent', '--show-error', '--noproxy', '*', '--max-time', '10',
                 '--connect-to', f'{host}:{443 if scheme == "https" else 80}:127.0.0.1:{port}',
                 '--dump-header', '-', '--write-out', '\n%{http_code}']
    if scheme == 'https':
        arguments += ['--cacert', str(CERT)]
    for key, value in (headers or {}).items():
        arguments += ['-H', key + ': ' + value]
    arguments += [f'{scheme}://{host}{path}']
    result = subprocess.run(arguments, text=True, capture_output=True)
    text, status = result.stdout.rsplit('\n', 1)
    head, _, body = text.partition('\n\n')
    response_headers = {}
    for line in head.splitlines()[1:]:
        if ':' in line:
            key, value = line.split(':', 1)
            response_headers[key.lower()] = value.strip()
    try:
        body = json.loads(body)
    except ValueError:
        body = body[:200]
    return {'status': int(status or 0), 'curl_exit': result.returncode,
            'headers': response_headers, 'body': body,
            'error': result.stderr.strip() if result.returncode else None}


def stats():
    return json.load(urllib.request.urlopen(f'http://127.0.0.1:{MOCK}/stats', timeout=3))


def tls_socket(host):
    context = ssl.create_default_context(cafile=str(CERT))
    connection = socket.create_connection(('127.0.0.1', int(HTTPS)), timeout=5)
    return context.wrap_socket(connection, server_hostname=host)


def websocket():
    with tls_socket('plain.test') as connection:
        connection.sendall(b'GET /api/websocket HTTP/1.1\r\nHost: plain.test\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n')
        data = b''
        while b'auth_required' not in data and len(data) < 4096:
            chunk = connection.recv(4096)
            if not chunk:
                break
            data += chunk
    return {'handshake_101': data.startswith(b'HTTP/1.1 101'), 'frame_received': b'auth_required' in data}


def streaming_upload():
    path = '/bucket/object?partNumber=1&uploadId=synthetic'
    first = b'A' * 65536
    remainder = b'B' * (2 * 1024 * 1024 - len(first))
    with tls_socket('upload.test') as connection:
        headers = (f'PUT {path} HTTP/1.1\r\nHost: upload.test\r\nContent-Length: {len(first) + len(remainder)}\r\n'
                   'Authorization: AWS4-HMAC-SHA256 synthetic-not-a-real-signature\r\nConnection: close\r\n\r\n')
        connection.sendall(headers.encode() + first)
        observed = None
        for _ in range(20):
            observed = stats()['uploads'].get(path)
            if observed and observed['bytes'] == len(first):
                break
            time.sleep(0.05)
        streamed = bool(observed and observed['bytes'] == len(first) and not observed['complete'])
        connection.sendall(remainder)
        response = http.client.HTTPResponse(connection)
        response.begin()
        status = response.status
        body = json.loads(response.read())
    return {'status': status, 'backend_received_before_upload_completed': streamed,
            'body': body, 'expected_sha256': hashlib.sha256(first + remainder).hexdigest()}


for attempt in range(40):
    plain = request()
    if plain['status'] == 200:
        break
    time.sleep(0.5)

result = {'plain': plain, 'redirect': request(scheme='http'),
          'vault_validated_tls': request('vault.test'),
          'vault_wrong_server_name': request('vault-bad-name.test'),
          'vault_untrusted_certificate': request('vault-untrusted.test'),
          'argo_selfsigned_backend': request('argo.test'),
          'ignored_old_class': request('ignored.test'),
          'http01_same_tls_host': request(path='/.well-known/acme-challenge/probe', scheme='http')}

auth_path = '/private/item?x=1&y=2'
before = stats()['backend']
result['auth_anonymous'] = request('auth.test', auth_path)
result['anonymous_backend_calls'] = stats()['backend'] - before

before = stats()['backend']
result['auth_forged_identity'] = request('auth.test', auth_path, headers={
    'X-Auth-Request-User': 'attacker', 'X-Auth-Request-Email': 'attacker@example.invalid',
    'X-Forwarded-Host': 'attacker.invalid', 'X-Forwarded-Proto': 'http', 'X-Forwarded-Uri': '/wrong',
})
result['forged_identity_backend_calls'] = stats()['backend'] - before

before = stats()['backend']
result['auth_forbidden'] = request('auth.test', auth_path, headers={'X-Lab-Forbidden': 'yes'})
result['forbidden_backend_calls'] = stats()['backend'] - before
result['auth_authenticated'] = request('auth.test', auth_path, headers={
    'X-Lab-Authenticated': 'yes', 'X-Auth-Request-User': 'attacker', 'X-Auth-Request-Email': 'attacker@example.invalid',
})
try:
    result['websocket'] = websocket()
except Exception as error:
    result['websocket'] = {'error': str(error)}
try:
    result['streaming_upload'] = streaming_upload()
except Exception as error:
    result['streaming_upload'] = {'error': str(error)}

raw = json.load(urllib.request.urlopen(f'http://127.0.0.1:{API}/api/rawdata', timeout=3))
result['native_router_count'] = len([name for name in raw.get('routers', {}) if name.endswith('@kubernetes')])
result['nginx_router_count'] = len([name for name in raw.get('routers', {}) if name.endswith('@kubernetesingressnginx')])
result['configuration_errors'] = []
for group, entries in raw.items():
    if isinstance(entries, dict):
        for name, resource in entries.items():
            # The Errors middleware also has a configuration object named
            # "errors"; diagnostics are an array, not that middleware object.
            if isinstance(resource, dict) and isinstance(resource.get('errors'), list) and resource['errors']:
                result['configuration_errors'].append({'group': group, 'name': name, 'errors': resource['errors']})
print(json.dumps(result, indent=2))
