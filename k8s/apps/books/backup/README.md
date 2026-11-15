# Sauvegarde Calibre

Le CronJob prépare une copie complète des volumes de bibliothèque, configuration
et import, puis la dépose dans un bucket du S3 privé. Il réutilise le secret
MinIO fourni par Vault et l'image du job de sauvegarde PostgreSQL. Le job Glacier
existant archive ensuite ce bucket avec les autres ; aucune connexion AWS ni
seconde politique de rétention AWS n'est ajoutée ici.

La copie privée est remplacée chaque semaine, avant le passage du job Glacier.
Les archives datées et leur conservation d'un an restent gérées par ce dernier.
Les deux horaires doivent rester ordonnés si leur configuration change.

Les volumes sources sont montés en lecture seule. Les bases SQLite sont copiées
avec l'API de sauvegarde, qui inclut les transactions validées présentes dans le
WAL. Une modification des bases ou fichiers pendant la copie provoque une
nouvelle tentative ; après trois essais, le job échoue sans envoyer la copie.
Les anciennes sauvegardes de travail, miniatures, caches et fichiers temporaires
SQLite sont exclus. Les références aux miniatures sont effacées dans la copie
de la base, afin qu'elles puissent être recréées après restauration.

Chaque archive contient un manifeste SHA-256 de ses fichiers. Avant l'envoi,
le programme relit l'archive entière, restaure ses bases dans un dossier
temporaire, vérifie leur intégrité et la présence des formats et couvertures
référencés par Calibre. Après l'envoi, il relit l'objet S3 et compare son SHA-256.
Une exécution n'est réussie que si toutes ces vérifications passent.

## Vérification et restauration

Télécharger `calibre.tar.gz` depuis le bucket privé, ou restaurer et télécharger
l'archive datée depuis Glacier. L'archive produite par le job Glacier est un ZIP
contenant les objets du bucket : en extraire d'abord `calibre.tar.gz`.

```sh
calibre-debug -e verify.py /chemin/vers/calibre.tar.gz
```

Arrêter Calibre avant de restaurer. Extraire l'archive vérifiée dans un dossier
vide, puis recopier `library/`, `config/` et, s'il existe, `ingest/` dans leurs
volumes respectifs. Ne pas fusionner les bases restaurées avec d'anciens fichiers
`-wal` ou `-shm`. Rétablir les droits selon les UID/GID de l'application, puis
redémarrer et vérifier la bibliothèque ainsi que les paramètres du compte.
Le manifeste décrit la date et le nombre de livres de la sauvegarde. La
vérification utilise le runtime Calibre afin de charger le tokenizer SQLite de
ses bases de notes et de recherche, si celles-ci sont présentes.

Les secrets contenus dans la configuration font partie de l'archive : conserver
le bucket privé et protéger les copies téléchargées. Le chiffrement et la
rétention du stockage distant restent gérés par l'infrastructure S3 existante.

## Tests

```sh
python3 -m unittest discover -s k8s/apps/books/backup -p 'test_*.py' -v
bash -n k8s/apps/books/backup/upload.sh
kubectl kustomize k8s/apps/books
```
