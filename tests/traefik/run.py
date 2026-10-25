#!/usr/bin/env python3
"""Run the actual Traefik NGINX provider against a disposable Kubernetes API mock.

No kubeconfig, cluster connection, production credentials, or privileged containers
are used. Docker ports bind only to loopback. All generated keys and Docker resources
are removed in finally; images present before this run are preserved.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import parse_qs, urlsplit

TRAEFIK = 'traefik:v3.7.16@sha256:c8a121e4bb1347e8fd52657fab12f2cb70b68f8a018895e2068f92c9e519b9c2'
NODE = 'node:24-alpine@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43'
SOURCE = Path(__file__).resolve().parent

def command(args, *, check=True, timeout=120, env=None):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    if check and result.returncode:
        raise RuntimeError(f'{args[0]} {args[1]} failed: {result.stderr[-3000:]}')
    return result

def docker(*args, **kwargs):
    return command(['docker', *args], **kwargs)

def published_port(container, private_port):
    value = docker('inspect', container, '--format', '{{json .NetworkSettings.Ports}}').stdout
    return json.loads(value)[f'{private_port}/tcp'][0]['HostPort']

def validate(result, buffered):
    """Both known incompatible behaviors and their corrected variants are tested."""
    checks = {
        'frontend_tls_verified': result['plain_tls']['status'] == 200,
        'http_redirect': result['http_redirect']['status'] == 308,
        'https_backend': result['https_self_signed_backend']['payload'].get('tls') is True,
        'passthrough_exposes_backend_certificate': result['argo_passthrough_wrong_cert']['curl_exit'] == 60,
        'passthrough_removed_restores_frontend_tls': result['argo_corrected_frontend_tls']['status'] == 200,
        'anonymous_redirected': result['auth_anonymous']['status'] == 302,
        'no_anonymous_backend_call': result['auth_backend_called_anonymous'] == 0,
        'authenticated_request': result['auth_authenticated']['status'] == 200,
        'authenticated_user_header': result['auth_authenticated']['payload'].get('user') == 'test-user',
        'http_host_incompatibility_reproduced': result['auth_anonymous']['location'].startswith('https://login.test/start?rd=https:///'),
        'host_variable_alone_does_not_escape_query': parse_qs(urlsplit(result['auth_host_variable_fixed']['location']).query).get('y') == ['2'],
        'automatic_rd_preserves_full_url': parse_qs(urlsplit(result['auth_auto_redirect']['location']).query).get('rd') == ['https://auth-auto.test/private/item?x=1&y=2'],
        'http01_on_existing_tls_host': result['http01_plain_http']['status'] == 200 and result['http01_plain_http']['payload'] == 'probe.challenge.synthetic',
        'websocket_handshake': result['websocket'].get('status_101') is True,
        'websocket_frame': result['websocket'].get('received_auth_required_frame') is True,
        'body_below_limit': result['upload_512_bytes']['status'] == 200,
        'body_above_limit': result['upload_2048_bytes']['status'] == (413 if buffered else 200),
        'real_provider_routes_loaded': result['provider_router_count'] >= 17 and result['provider_tcp_router_count'] == 1,
        'no_configuration_errors': not result['configuration_errors'],
    }
    return checks

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, help='Optional JSON report path; no certificates are exported.')
    args = parser.parse_args()
    for executable in ['docker', 'openssl', 'curl']:
        if not shutil.which(executable): raise RuntimeError(f'Missing executable: {executable}')
    prefix = 'traefik-provider-lab-' + uuid.uuid4().hex[:10]
    mock, proxy, network = prefix + '-mock', prefix + '-proxy', prefix + '-net'
    created_containers, added_images = [], []
    network_created = False
    report = {'traefik_image': TRAEFIK, 'mock_image': NODE, 'phases': {}, 'cleanup_errors': []}
    try:
        for image in [NODE, TRAEFIK]:
            if docker('image', 'inspect', image, check=False).returncode:
                docker('pull', image, timeout=300)
                added_images.append(image)
        with tempfile.TemporaryDirectory(prefix=prefix + '-') as temporary:
            lab = Path(temporary)
            shutil.copyfile(SOURCE / 'mock.mjs', lab / 'mock.mjs')
            hosts = ['plain', 'auth', 'auth-fixed', 'auth-auto', 'secure', 'argo-bad', 'argo-good', 'upload']
            for name, san in [('frontend', ','.join('DNS:' + h + '.test' for h in hosts)), ('backend', 'DNS:backend.test')]:
                command(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                         '-keyout', str(lab / (name + '.key')), '-out', str(lab / (name + '.crt')),
                         '-subj', f'/CN={name}.test', '-addext', 'subjectAltName=' + san])
                (lab / (name + '.key')).chmod(0o600)
            docker('network', 'create', network)
            network_created = True
            docker('run', '-d', '--name', mock, '--network', network, '-e', 'MOCK_HOST=' + mock,
                   '-v', str(lab) + ':/lab:ro', '-p', '127.0.0.1::8000', NODE, 'node', '/lab/mock.mjs')
            created_containers.append(mock)
            for buffered in [False, True]:
                mode = 'buffered' if buffered else 'unbuffered'
                docker('run', '-d', '--name', proxy, '--network', network,
                       '-p', '127.0.0.1::80', '-p', '127.0.0.1::443', '-p', '127.0.0.1::8080', TRAEFIK,
                       '--entrypoints.web.address=:80', '--entrypoints.websecure.address=:443', '--api.insecure=true',
                       '--providers.kubernetesingressnginx=true',
                       '--providers.kubernetesingressnginx.endpoint=http://' + mock + ':8000',
                       '--providers.kubernetesingressnginx.controllerclass=k8s.io/ingress-nginx',
                       '--providers.kubernetesingressnginx.ingressclass=public',
                       '--providers.kubernetesingressnginx.proxyrequestbuffering=' + str(buffered).lower(),
                       '--global.checknewversion=false', '--log.level=ERROR')
                created_containers.append(proxy)
                env = dict(os.environ, LAB_PORT_HTTP=published_port(proxy, 80), LAB_PORT_HTTPS=published_port(proxy, 443),
                           LAB_PORT_API=published_port(proxy, 8080), LAB_PORT_MOCK=published_port(mock, 8000),
                           LAB_CERT_DIR=str(lab), LAB_RESULT_DIR=str(lab))
                result = json.loads(command([sys.executable, str(SOURCE / 'probe.py'), mode], env=env).stdout)
                checks = validate(result, buffered)
                report['phases'][mode] = {'checks': checks, 'results': result}
                docker('rm', '-f', proxy)
                created_containers.remove(proxy)
    finally:
        for container in reversed(created_containers):
            result = docker('rm', '-f', container, check=False)
            if result.returncode: report['cleanup_errors'].append(result.stderr.strip())
        if network_created:
            result = docker('network', 'rm', network, check=False)
            if result.returncode: report['cleanup_errors'].append(result.stderr.strip())
        for image in reversed(added_images):
            result = docker('image', 'rm', image, check=False)
            if result.returncode: report['cleanup_errors'].append(result.stderr.strip())
    report['passed'] = len(report['phases']) == 2 and all(all(p['checks'].values()) for p in report['phases'].values()) and not report['cleanup_errors']
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'passed': report['passed'], 'checks': {k: v['checks'] for k, v in report['phases'].items()}, 'cleanup_errors': report['cleanup_errors']}, indent=2))
    return 0 if report['passed'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
