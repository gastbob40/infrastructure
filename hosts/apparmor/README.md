# Compatibilité AppArmor / containerd sur Ubuntu 26.04

## Incident et périmètre

Le 30 octobre 2026, après les mises à jour Ubuntu et noyau des trois nœuds,
AppArmor refusait des signaux et des accès `ptrace` entre processus portant
le label empilé `cri-containerd.apparmor.d//&unconfined`. PostgreSQL 17.9
restait en démarrage, Meal Planner échouait avec SQLSTATE `57P03` et
Authentik avec `PermissionError` lors de l'envoi de `SIGUSR1`.

Versions lors de l'intervention : Ubuntu 26.04.1, K3s `v1.36.5+k3s1`,
containerd `v2.3.4-k3s1.36`, AppArmor 5.0.2 ; noyau `7.0.0-42-generic`
sur le master et `7.0.0-1015-oracle` sur les deux slaves.

Le profil reprend celui de la [version exacte de containerd installée](https://github.com/k3s-io/containerd/blob/v2.3.4-k3s1.36/contrib/apparmor/template.go).
Deux autorisations sont ajoutées pour ce label empilé précis :
les signaux et `ptrace`. Depuis le 1er novembre, deux autres autorisations
ciblent le label exact `chromium//&cri-containerd.apparmor.d` : les signaux
et `ptrace (read,readby)` uniquement, pour la fermeture des navigateurs et
la lecture des informations de processus. L'accès `ptrace trace/tracedby`
n'est pas ajouté pour ce label Chromium. Toutes les restrictions du profil
d'origine restent présentes. Le profil containerd reste en mode `enforce` et
`kernel.apparmor_restrict_unprivileged_unconfined` reste à `1`.

Suivi amont : [incident #14485](https://github.com/containerd/containerd/issues/14485)
et [proposition de correctif #14487](https://github.com/containerd/containerd/pull/14487).
Il s'agit d'un contournement local temporaire, à réexaminer avant toute
mise à jour K3s/containerd. Aucun joker n'élargit ces exceptions à d'autres
labels empilés.

## Installation

Depuis la racine du dépôt, avec les alias SSH habituels :

```sh
python3 hosts/apparmor/install.py k8s-s2
python3 hosts/apparmor/install.py k8s-s1
python3 hosts/apparmor/install.py k8s
```

L'installateur utilise root sur le master et `sudo -n` sur les slaves.
Il vérifie la version exacte de containerd, les profils existants et
l'état des services ; compile le profil sans le charger ; valide l'unité
systemd ; sauvegarde les fichiers remplacés puis charge le profil.
Il refuse de remplacer des fichiers existants non gérés par ce dossier.
Une réapplication identique ne crée pas de sauvegarde supplémentaire.

Fichiers installés :

- `/etc/apparmor.d/cri-containerd.apparmor.d`
- `/etc/systemd/system/k3s-apparmor-profile.service`
- `/etc/systemd/system/k3s.service.d/25-apparmor-profile.conf` sur le master,
  ou le même fichier sous `k3s-agent.service.d` sur les slaves.

Le service charge le profil avant K3s à chaque démarrage. Une application
à chaud ne redémarre ni le nœud ni K3s. Pour recharger le profil, utiliser
`systemctl reload k3s-apparmor-profile`, **pas `restart` ni `stop`** : K3s
dépend de cette unité et pourrait être arrêté avec elle.

Les sauvegardes restent sur chaque serveur dans
`/root/backups/apparmor-stacked-peers-<horodatage UTC>/` (dossier `0700`).
Elles ne contiennent que quelques fichiers de configuration ; aucune
copie de base de données ou de volume n'est téléchargée sur le Mac.
En cas d'échec d'installation, le script tente de restaurer le profil et
les fichiers précédents ; vérifier son erreur et l'état des services.

## Vérifications et récupération

Contrôler les unités `apparmor`, `k3s-apparmor-profile` et `k3s` ou
`k3s-agent`, le profil en mode `enforce`, ainsi que les nouveaux refus
AppArmor dans le journal du noyau. Un processus non privilégié lancé sous
ce profil doit pouvoir s'envoyer `SIGUSR1` et `SIGURG`.

Un PostgreSQL déjà bloqué peut nécessiter une relance contrôlée après le
chargement du correctif :

```sh
kubectl exec -n postgres infra-main-cluster-0 -- \
  patronictl -c /home/postgres/postgres.yml \
  restart infra-main-cluster infra-main-cluster-0 --force
kubectl exec -n postgres infra-main-cluster-0 -- pg_isready
```

Ne pas supprimer le volume, réinitialiser la base ou restaurer un dump
pour ce problème. Le statut `Running` du pod PostgreSQL ne suffit pas à
prouver que SQL répond : ce pod n'avait pas de sonde de disponibilité.
Valider aussi SQL, le rôle primaire, les migrations et la disponibilité
HTTP de Meal Planner, puis les serveurs et workers Authentik.

Lors de l'intervention, les tests `SIGUSR1`/`SIGURG` sont passés de refusés
à autorisés ; le contrôle des règles a confirmé les deux seuls ajouts
par rapport au profil amont. Après la relance Patroni, SQL répondait,
le serveur était primaire et les 11 bases hors templates étaient présentes.
Meal Planner a validé ses 11 migrations et son endpoint
`https://api.meal-planner.gastbob40.com/q/health/ready` répondait HTTP 200.
Les trois serveurs et trois workers Authentik sont ensuite redevenus
disponibles ; `https://auth.gastbob40.com/-/health/ready/` répondait HTTP 200.

## FlareSolverr : fermeture des navigateurs corrigée le 1er novembre

FlareSolverr 3.4.6 / Chromium 142 pouvait charger une page et répondre
`status=ok` malgré les refus AppArmor. Les tests ont montré que les signaux
vers `chromium//&cri-containerd.apparmor.d` étaient également refusés :
les navigateurs restaient actifs après une requête temporaire ou après
`sessions.destroy`, même lorsque `sessions.list` renvoyait une liste vide.
Les erreurs d'arrêt du navigateur sont masquées par `Chrome.quit()` dans
la version installée d'undetected-chromedriver.

Les deux règles Chromium ont été ajoutées et chargées sur les trois nœuds.
Le rechargement à chaud seul n'a pas suffi pour les processus existants.
Après vérification de l'absence de session active, le seul pod FlareSolverr
a été recréé ; les nœuds, K3s et les autres applications n'ont pas été
redémarrés. Le profil système `/etc/apparmor.d/chromium` reste inchangé.

Vérifications sur le nouveau pod :

- Création d'une session, navigation vers `https://example.com/`, puis
  vers l'endpoint de disponibilité Meal Planner : HTTP 200.
- Destruction de la session et liste des sessions vide.
- Zéro processus Chromium actif avant et après le test, au lieu d'une
  accumulation de navigateurs avant correction.
- Aucun nouveau refus AppArmor Chromium pendant le test.
- Le nettoyage est aussi vérifié lorsque la navigation échoue.

Le test direct vers `https://www.hellofresh.fr/` renvoie une erreur
FlareSolverr indiquant un blocage Cloudflare. Il est distinct de l'incident
AppArmor. Meal Planner continue d'utiliser son client HelloFresh via
`chronodrive-proxy`, sans FlareSolverr ; l'utilisateur confirme que ce
chemin fonctionne. Cette intervention ne modifie pas cette intégration.
Pour un futur usage FlareSolverr / HelloFresh, prévoir le proxy résidentiel
et une autorisation réseau limitée au pod FlareSolverr : actuellement,
la NetworkPolicy du proxy autorise uniquement le backend Meal Planner.

## Retrait lors d'une prochaine mise à jour

Vérifier dans la version réellement embarquée par K3s que le correctif
officiel est intégré, puis revoir les différences de profil. Ne pas
simplement changer la version attendue dans l'installateur : le profil
persistant empêcherait containerd de charger de nouvelles règles.

Prévoir le retrait du profil et des unités locales pendant une maintenance,
avec rechargement du profil officiel et validation de chaque nœud. Un
redémarrage planifié peut être nécessaire pour éliminer le profil chargé
avant que le nouveau runtime crée son profil. Ne pas décharger le profil
AppArmor de conteneurs actifs sans procédure de maintenance vérifiée.
