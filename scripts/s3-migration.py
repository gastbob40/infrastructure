#!/usr/bin/env python3
"""Migration manuelle MinIO -> RustFS via deux port-forwards locaux.

Prérequis : kubectl, rclone, Python 3. Garder les tunnels locaux ouverts :
MinIO service/minio-community (namespace minio-community), 19000:9000 ;
RustFS service/rustfs-svc (namespace rustfs), 19010:9000.
Effectuer plan, copy puis check avant toute bascule. Pour la copie finale,
arrêter tous les producteurs, relire sync-plan puis lancer sync --writers-stopped.
Les secrets restent en mémoire ; aucun appel ne modifie Kubernetes ou MinIO.
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys


BUCKETS = ("meal-planner", "postgres-backups")
CONTEXT = "k8s.gastbob40.com"


def credentials(namespace, secret):
    result = subprocess.run(
        ["kubectl", "--context", CONTEXT, "--request-timeout=15s", "-n",
         namespace, "get", "secret", secret, "-o", "json"],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(result.stdout)["data"]
    result = {
        "ACCESS_KEY_ID": base64.b64decode(data["rootUser"], validate=True).decode(),
        "SECRET_ACCESS_KEY": base64.b64decode(data["rootPassword"], validate=True).decode(),
    }
    if not all(result.values()):
        raise ValueError("identifiants S3 vides")
    return result


def environment():
    # Écarter toute configuration rclone/AWS héritée (filtres, dry-run,
    # authentification, proxy, etc.) qui pourrait fausser la vérification.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("RCLONE_", "AWS_"))
           and key.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    for name, namespace, secret, endpoint in (
        ("MINIO", "minio-community", "minio-community-secrets", "http://127.0.0.1:19000"),
        ("RUSTFS", "rustfs", "rustfs-credentials", "http://127.0.0.1:19010"),
    ):
        settings = {
            "TYPE": "s3", "PROVIDER": "Other", "REGION": "us-east-1",
            "ENDPOINT": endpoint, "FORCE_PATH_STYLE": "true", "ENV_AUTH": "false",
            **credentials(namespace, secret),
        }
        env.update({f"RCLONE_CONFIG_{name}_{key}": value
                    for key, value in settings.items()})
    return env


def commands(action, bucket):
    source, destination = f"minio:{bucket}", f"rustfs:{bucket}"
    common = ["rclone", "--config", os.devnull, "--checkers", "4", "--transfers", "2"]
    if action == "check":
        return [common + ["check", source, destination, "--download"]]
    operation = "sync" if action in ("sync-plan", "sync") else "copy"
    args = [operation, source, destination, "--metadata", "--ignore-times"]
    # Refaire la copie intégrale est peu coûteux ici (~1,3 Gio) et reprend
    # aussi une métadonnée modifiée sans changement du contenu.
    if action in ("plan", "sync-plan"):
        return [common + args + ["--dry-run"]]
    result = [common + ["mkdir", destination]]
    if operation == "sync":
        # Supprimer les objets surnuméraires seulement après les transferts.
        args.append("--delete-after")
    return result + [common + args]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "copy", "check", "sync-plan", "sync"))
    parser.add_argument("--writers-stopped", action="store_true",
                        help="confirme l'arrêt effectif de tous les producteurs avant sync")
    args = parser.parse_args()
    if args.action == "sync" and not args.writers_stopped:
        parser.error("sync exige --writers-stopped après arrêt réel des écritures et revue de sync-plan")
    for binary in ("kubectl", "rclone"):
        if not shutil.which(binary):
            parser.error(f"outil manquant : {binary}")
    env = environment()
    for bucket in BUCKETS:
        print(f"{args.action}: MinIO -> RustFS / {bucket}", flush=True)
        for command in commands(args.action, bucket):
            subprocess.run(command, env=env, check=True)


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, KeyError, ValueError) as exc:
        # Ne pas imprimer de réponse de Secret ou l'environnement du processus.
        print(f"Migration interrompue ({type(exc).__name__}). Aucune bascule effectuée.", file=sys.stderr)
        sys.exit(1)
