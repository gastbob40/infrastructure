# Migration MinIO vers RustFS

## État et périmètre

La phase 1 ajoute `k8s/cluster/apps/rustfs.yml` : chart et image RustFS
`1.0.4`, mode standalone, un PVC `rustfs-data` de 50 Gio en `local-path`
sur `k8s-master`. MinIO, ses volumes, ses Ingress et ses clients restent en place.
Le PVC RustFS porte `Prune=false,Delete=false` pour sa conservation par ArgoCD.
Cela ne remplace pas une sauvegarde ; `local-path` ne fournit pas de réplication.

Le nouveau VaultSecret `rustfs/rustfs-credentials` lit `k8s/minio`
(`rootUser`, `rootPassword`). Les identifiants des clients existants ont été
comparés à ceux du serveur, sans afficher leurs valeurs : ils correspondent.
Leur rotation et la séparation des comptes pourront se faire après la migration.

Inventaire en lecture seule du 26 octobre 2026, à refaire avant la copie :

| Bucket | Objets | Octets |
| --- | ---: | ---: |
| meal-planner | 2069 | 548923520 |
| postgres-backups | 175 | 829465590 |

Ces buckets n'ont pas de versioning, bucket policy, CORS, lifecycle,
Object Lock, chiffrement par défaut ou réplication configurés. Les configurations
de buckets, comptes IAM, ACL particulières et tags ne sont pas couverts par les
commandes de copie ci-dessous : les inventorier séparément et arrêter la
migration si de tels usages apparaissent. Ne pas copier directement le répertoire
de données MinIO dans le volume RustFS.

Bien qu'aucun CORS ne soit configuré sur les buckets, MinIO répond aux preflights
du navigateur avec son CORS global. Dans RustFS 1.0.4, la variable globale
`RUSTFS_CORS_ALLOWED_ORIGINS` accepte des origines exactes ou `*`, mais pas les
wildcards de sous-domaines. Le CORS des buckets les accepte : le manifest ArgoCD
déclare donc `AllowedOrigins: ["https://*.gastbob40.com"]` dans un ConfigMap.
Un Job `PostSync` crée les deux buckets si nécessaire et applique cette règle
à chaque synchronisation complète de RustFS, avec les identifiants du Secret
Vault existant. Il ne copie et ne supprime aucun objet. Les nouveaux buckets
doivent être ajoutés explicitement à la liste du Job pour recevoir cette règle.

Cela autorise les sous-domaines HTTPS, y compris imbriqués, mais pas le domaine
nu, HTTP ou les origines externes. Le CORS n'accorde aucun droit S3 : les requêtes
restent authentifiées ou présignées. Tester le preflight `OPTIONS` puis un `PUT`
et un `GET` signés depuis plusieurs sous-domaines, ainsi que le refus d'une
origine externe et d'un domaine ressemblant, avant la bascule publique.

## 1. Déployer par Git et ArgoCD

1. Valider les manifests puis pousser le changement directement sur `main`.
2. Synchroniser `cluster-app` dans ArgoCD : il découvre `rustfs.yml`.
3. Synchroniser ensuite l'application `rustfs` et attendre `Synced / Healthy`.
   Sa synchronisation est manuelle, comme celle de plusieurs composants du dépôt.
4. Vérifier le PVC, le Secret matérialisé par Vault Secrets Operator et les sondes.
5. Vérifier que le hook `rustfs-bucket-cors` a réussi. Une synchronisation
   sélective des seules ressources de l'application RustFS ne lance pas ce hook.

```sh
kubectl --context k8s.gastbob40.com -n rustfs get deployment,pods,pvc,svc,vaultsecret
kubectl --context k8s.gastbob40.com -n rustfs rollout status deployment/rustfs
```

Aucun `kubectl apply`, `helm install`, patch de Deployment ou changement DNS
n'est nécessaire. Toute modification du déploiement passe par Git puis ArgoCD.

## 2. Tester sans DNS public

Dans deux terminaux, ouvrir uniquement des tunnels locaux vers les services
distincts. Garder ces tunnels pendant la copie et la vérification, y compris
après une future bascule des domaines publics.

```sh
kubectl --context k8s.gastbob40.com -n minio-community port-forward --address 127.0.0.1 service/minio-community 19000:9000
kubectl --context k8s.gastbob40.com -n rustfs port-forward --address 127.0.0.1 service/rustfs-svc 19010:9000 19011:9001
```

API RustFS : `http://127.0.0.1:19010`.
Console RustFS 1.0.4 : `http://127.0.0.1:19011/rustfs/console/` (la racine `/`
ne sert pas la console dans l'image testée).

```sh
curl --fail http://127.0.0.1:19010/health/ready
```

Tester un upload, un téléchargement, une URL présignée, un upload multipart et
une suppression dans un bucket de test distinct, puis retirer ce bucket de test.
Tester aussi Meal Planner sur cette destination avant de modifier son endpoint
de production ; son code applicatif est dans un autre dépôt.

## 3. Configurer rclone sans écrire de secret sur disque

Prérequis : Python 3, kubectl et rclone (commandes validées avec rclone 1.75.2).
Le script `scripts/s3-migration.py` lit les deux Secrets Kubernetes en mémoire
et configure rclone par variables d'environnement. Il n'affiche pas les clés.
Les opérations ciblent exclusivement `meal-planner` et `postgres-backups`.
Il ne change ni Kubernetes, ni les DNS, ni la source MinIO.

```sh
python3 scripts/s3-migration.py plan
python3 scripts/s3-migration.py copy
python3 scripts/s3-migration.py check
```

`plan` est une simulation sans écriture. `copy` crée les buckets nécessaires et
copie les objets avec leurs métadonnées HTTP et utilisateur. Il ne supprime rien
dans la destination. `check` relit le contenu des deux côtés avec
`rclone check --download`, et échoue aussi sur des objets supplémentaires.
Un échec de vérification interdit la bascule.

Le champ S3 `LastModified` devient la date de copie. La rétention de 14 jours
dans `dockers/postgres-backup/backup.sh` repart donc de cette date pour les objets
migrés, même si rclone conserve une date d'origine en métadonnée. Les dumps
copiés seront temporairement conservés plus longtemps.

## 4. DNS : aucun changement requis pour la phase 1

Les observations DNS du 26 octobre 2026 sont :

- `api.s3.gastbob40.com` et `s3.gastbob40.com` pointent vers `23.88.98.223`.
- Les serveurs DNS autoritaires sont chez Cloudflare.
- `api.s3-next.gastbob40.com` et `s3-next.gastbob40.com` ne résolvent pas.

Les deux noms temporaires sont préparés dans les valeurs Helm, avec
`ingress.enabled: false`. Aucun certificat ni Ingress public n'est créé à cette
étape. Ne pas modifier les enregistrements actuels et ne pas ajouter de wildcard.

Si un accès public temporaire est souhaité, préparer séparément les deux nouveaux
enregistrements vers le même point d'entrée Traefik et vérifier leur résolution
avant d'activer l'Ingress par Git/ArgoCD. Vérifier explicitement le mode proxy
Cloudflare et ses limites d'upload ; une API S3 doit conserver son `Host` signé.
Le fournisseur DNS n'est pas géré par ce dépôt : ne pas introduire d'écriture DNS
implicite dans la migration.

## 5. Bascule finale, à préparer dans un second changement Git

Cette étape n'est pas incluse dans le manifest initial. Prévoir une fenêtre
d'interruption des écritures. ArgoCD synchronise plusieurs applications
séparément : la bascule de deux Ingress n'est pas atomique.

1. Via Git/ArgoCD, suspendre `pg-backup` et `pg-glacier` (`spec.suspend: true`)
   et mettre Meal Planner en maintenance/arrêter ses producteurs d'écritures.
   Attendre la fin des Jobs déjà lancés : suspendre un CronJob ne les arrête pas.
   Vérifier également l'absence d'uploads directs ou via URL présignée encore
   utilisable ; si nécessaire bloquer temporairement l'accès S3 public par
   l'Ingress, tout en gardant les tunnels privés pour la migration.
2. Refaire l'inventaire des buckets et des fonctionnalités utilisées.
3. Exécuter `copy`, puis `sync-plan`. Relire toutes les suppressions proposées
   dans RustFS : elles correspondent aux objets supprimés de MinIO depuis la
   première copie. RustFS ne doit pas avoir reçu d'écritures applicatives.
4. Une fois les écritures effectivement arrêtées, exécuter
   `sync --writers-stopped`, puis `check`. La synchronisation finale peut
   supprimer des objets **dans RustFS uniquement**.
5. Tester la restauration d'un dump PostgreSQL téléchargé depuis RustFS dans une
   base isolée, ainsi que les lectures et uploads de Meal Planner.
6. Par Git/ArgoCD, désactiver les Ingress API et console de MinIO, puis exposer
   RustFS sur les noms historiques. Ne jamais laisser deux Ingress actifs avec
   le même couple hôte/chemin. Conserver les noms temporaires s'ils ont servi
   aux tests et garder les données MinIO pour le retour arrière.
7. Attendre le certificat `Ready` dans le namespace RustFS et vérifier les
   réponses TLS/S3 sur `api.s3.gastbob40.com`. Le certificat de MinIO est dans
   un autre namespace et n'est pas automatiquement réutilisé. Les DNS
   historiques restent inchangés : c'est Traefik qui change de backend.
8. Réactiver les producteurs et les CronJobs via Git/ArgoCD. Contrôler les
   premiers backups et l'export Glacier. Ne supprimer MinIO qu'après validation
   de la restauration et une période d'observation.

## Retour arrière

Avant la reprise des écritures, rétablir les Ingress MinIO via Git/ArgoCD suffit
après avoir désactivé ceux de RustFS sur les mêmes hôtes. Revalider le certificat
et les clients avant de rouvrir les écritures.

Après la reprise des écritures sur RustFS, **ne pas simplement remettre les
Ingress sur MinIO** : celui-ci est devenu obsolète. Suspendre à nouveau les
écritures, préserver les deux copies, réconcilier les ajouts/modifications et
les suppressions de RustFS vers MinIO, vérifier les données, puis basculer les
Ingress. Le script de migration fourni est volontairement à sens unique.
