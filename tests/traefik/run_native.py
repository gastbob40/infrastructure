#!/usr/bin/env python3
"""Test Traefik's native Kubernetes Ingress + file providers without a real cluster.

Requires Docker, curl, OpenSSL, and Python 3. Uses only generated certificates,
synthetic Kubernetes objects, and loopback ports. Resources and images created by
this run are removed; pre-existing images are preserved.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
from urllib.parse import parse_qs, urlsplit

from run import TRAEFIK, NODE, command, docker, published_port

SOURCE = Path(__file__).resolve().parent


def configuration(mock):
    return {'http': {
        'middlewares': {
            'hsts': {'headers': {'stsSeconds': 31536000}},
            'oauth-errors': {'errors': {'status': ['401'], 'statusRewrites': {'401': 302},
                                        'service': 'oauth@file', 'query': '/oauth2/start?rd={url}'}},
            'oauth-auth': {'forwardAuth': {'address': f'http://{mock}:9000/oauth2/auth',
                                           'trustForwardHeader': True,
                                           'authResponseHeaders': ['X-Auth-Request-User', 'X-Auth-Request-Email']}},
        },
        'services': {'oauth': {'loadBalancer': {'servers': [{'url': f'http://{mock}:9000'}]}}},
        'serversTransports': {
            'vault': {'serverName': 'backend.test', 'rootCAs': ['/lab/backend.crt']},
            'vault-bad-name': {'serverName': 'wrong.test', 'rootCAs': ['/lab/backend.crt']},
            'vault-untrusted': {'serverName': 'backend.test'},
            'argo': {'insecureSkipVerify': True},
        },
    }}


def return_url(response):
    return parse_qs(urlsplit(response['headers'].get('location', '')).query).get('rd')


def validate(result):
    upload = result['streaming_upload']
    auth = result['auth_authenticated']
    return {
        'frontend_tls_verified': result['plain']['status'] == 200 and result['plain']['curl_exit'] == 0,
        'hsts_present': result['plain']['headers'].get('strict-transport-security') == 'max-age=31536000',
        'http_redirect_https': result['redirect']['status'] == 301 and result['redirect']['headers'].get('location') == 'https://plain.test/',
        'vault_tls_verified': result['vault_validated_tls']['status'] == 200 and result['vault_validated_tls']['body']['tls'],
        'vault_correct_sni': result['vault_validated_tls']['body']['servername'] == 'backend.test',
        'vault_wrong_name_rejected': result['vault_wrong_server_name']['status'] == 500,
        'vault_untrusted_ca_rejected': result['vault_untrusted_certificate']['status'] == 500,
        'argo_tls_backend_with_frontend_certificate': result['argo_selfsigned_backend']['status'] == 200 and result['argo_selfsigned_backend']['body']['tls'],
        'old_ingress_class_ignored': result['ignored_old_class']['status'] == 404,
        'http01_same_tls_host_200': result['http01_same_tls_host']['status'] == 200,
        'http01_has_no_redirect': 'location' not in result['http01_same_tls_host']['headers'],
        'http01_exact_body': result['http01_same_tls_host']['body'] == 'probe.challenge.synthetic',
        'anonymous_redirected_302': result['auth_anonymous']['status'] == 302,
        'anonymous_no_backend_call': result['anonymous_backend_calls'] == 0,
        'anonymous_return_url_preserved': return_url(result['auth_anonymous']) == ['https://auth.test/private/item?x=1&y=2'],
        'forged_identity_redirected': result['auth_forged_identity']['status'] == 302,
        'forged_identity_no_backend_call': result['forged_identity_backend_calls'] == 0,
        'forwarded_host_spoof_does_not_change_return_url': return_url(result['auth_forged_identity']) == ['https://auth.test/private/item?x=1&y=2'],
        'forbidden_stays_403': result['auth_forbidden']['status'] == 403,
        'forbidden_no_backend_call': result['forbidden_backend_calls'] == 0,
        'authorized_request_200': auth['status'] == 200,
        'forged_user_replaced': auth['body']['user'] == 'test-user',
        'forged_email_replaced': auth['body']['email'] == 'test@example.invalid',
        'websocket_101': result['websocket']['handshake_101'],
        'websocket_frame': result['websocket']['frame_received'],
        'upload_streamed_before_complete': upload['backend_received_before_upload_completed'],
        'upload_two_mib_200': upload['status'] == 200 and upload['body']['bytes'] == 2 * 1024 * 1024,
        'upload_hash_intact': upload['body']['sha256'] == upload['expected_sha256'],
        'upload_path_and_query_preserved': upload['body']['path'] == '/bucket/object?partNumber=1&uploadId=synthetic',
        'upload_authorization_preserved': upload['body']['authorization'] == 'AWS4-HMAC-SHA256 synthetic-not-a-real-signature',
        'native_provider_loaded_eight_routes': result['native_router_count'] == 8,
        'nginx_provider_absent': result['nginx_router_count'] == 0,
        'no_configuration_errors': not result['configuration_errors'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, help='Optional report; contains no private certificates or keys.')
    args = parser.parse_args()
    for executable in ['docker', 'openssl', 'curl']:
        if not shutil.which(executable):
            raise RuntimeError(f'Missing executable: {executable}')
    prefix = 'traefik-native-lab-' + uuid.uuid4().hex[:10]
    mock, proxy, network = prefix + '-mock', prefix + '-proxy', prefix + '-net'
    containers, added_images = [], []
    network_created = False
    report = {'traefik_image': TRAEFIK, 'mock_image': NODE, 'checks': {}, 'cleanup_errors': []}
    try:
        for image in [NODE, TRAEFIK]:
            if docker('image', 'inspect', image, check=False).returncode:
                docker('pull', image, timeout=300)
                added_images.append(image)
        with tempfile.TemporaryDirectory(prefix=prefix + '-') as temporary:
            lab = Path(temporary)
            shutil.copyfile(SOURCE / 'native_mock.mjs', lab / 'native_mock.mjs')
            hosts = ['plain', 'auth', 'vault', 'argo', 'vault-bad-name', 'vault-untrusted', 'upload', 'ignored']
            for name, san in [('frontend', ','.join('DNS:' + host + '.test' for host in hosts)), ('backend', 'DNS:backend.test')]:
                command(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                         '-keyout', str(lab / (name + '.key')), '-out', str(lab / (name + '.crt')),
                         '-subj', f'/CN={name}.test', '-addext', 'subjectAltName=' + san])
                (lab / (name + '.key')).chmod(0o600)
            # JSON is a YAML subset; the file provider accepts .yaml or .toml.
            (lab / 'dynamic.yaml').write_text(json.dumps(configuration(mock)))
            docker('network', 'create', network)
            network_created = True
            docker('run', '-d', '--name', mock, '--network', network,
                   '-v', str(lab) + ':/lab:ro', '-p', '127.0.0.1::8000', NODE, 'node', '/lab/native_mock.mjs')
            containers.append(mock)
            docker('run', '-d', '--name', proxy, '--network', network,
                   '-v', str(lab) + ':/lab:ro', '-p', '127.0.0.1::80', '-p', '127.0.0.1::443', '-p', '127.0.0.1::8080', TRAEFIK,
                   '--entrypoints.web.address=:80', '--entrypoints.websecure.address=:443', '--api.insecure=true',
                   '--entrypoints.web.http.redirections.entrypoint.to=websecure',
                   '--entrypoints.web.http.redirections.entrypoint.scheme=https',
                   '--entrypoints.web.http.redirections.entrypoint.priority=1',
                   '--providers.kubernetesingress=true',
                   '--providers.kubernetesingress.endpoint=http://' + mock + ':8000',
                   '--providers.kubernetesingress.ingressclass=traefik',
                   '--providers.file.filename=/lab/dynamic.yaml',
                   '--global.checknewversion=false', '--log.level=ERROR')
            containers.append(proxy)
            env = dict(os.environ, LAB_PORT_HTTP=published_port(proxy, 80), LAB_PORT_HTTPS=published_port(proxy, 443),
                       LAB_PORT_API=published_port(proxy, 8080), LAB_PORT_MOCK=published_port(mock, 8000), LAB_CERT_DIR=str(lab))
            result = command([sys.executable, str(SOURCE / 'native_probe.py')], env=env)
            report['results'] = json.loads(result.stdout)
            report['checks'] = validate(report['results'])
    except Exception as error:
        report['execution_error'] = str(error)
        if proxy in containers:
            logs = docker('logs', '--tail', '80', proxy, check=False)
            report['traefik_logs'] = logs.stdout + logs.stderr
    finally:
        for container in reversed(containers):
            result = docker('rm', '-f', container, check=False)
            if result.returncode:
                report['cleanup_errors'].append(result.stderr.strip())
        if network_created:
            result = docker('network', 'rm', network, check=False)
            if result.returncode:
                report['cleanup_errors'].append(result.stderr.strip())
        for image in reversed(added_images):
            result = docker('image', 'rm', image, check=False)
            if result.returncode:
                report['cleanup_errors'].append(result.stderr.strip())
    report['passed'] = bool(report['checks']) and all(report['checks'].values()) and not report['cleanup_errors']
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'passed': report['passed'], 'checks': report['checks'], 'cleanup_errors': report['cleanup_errors'],
                      'execution_error': report.get('execution_error'), 'traefik_logs': report.get('traefik_logs')}, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
