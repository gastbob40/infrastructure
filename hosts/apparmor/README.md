# Compatibilité des profils AppArmor empilés

Ce répertoire contient un profil containerd et un installateur SSH pour
des systèmes utilisant des profils AppArmor empilés. Il ne constitue pas
un inventaire des machines ou des applications déployées.

Le profil est dérivé du modèle amont référencé dans son en-tête. Il conserve
les restrictions d'origine et ajoute des permissions pour deux labels
empilés précis. Pour Chromium, les permissions `ptrace` supplémentaires
sont limitées à `read/readby`. Aucun joker n'élargit ces exceptions.

Références amont : [incident containerd #14485](https://github.com/containerd/containerd/issues/14485)
et [proposition #14487](https://github.com/containerd/containerd/pull/14487).

## Utilisation

Vérifier la version du runtime et relire les différences avec son profil
d'origine avant installation. L'installateur refuse une version différente
de celle qu'il prend en charge ; ne pas simplement modifier ce contrôle.

```sh
# Connexion SSH disposant déjà des droits root
python3 hosts/apparmor/install.py SSH_HOST

# Connexion SSH utilisant sudo sans interaction
python3 hosts/apparmor/install.py SSH_HOST --sudo
```

L'installateur compile le profil sans le charger, valide l'unité systemd,
sauvegarde les fichiers précédents sur l'hôte cible et applique la configuration.
Il refuse les fichiers existants non gérés par ce répertoire. Une réapplication
identique ne crée pas de sauvegarde supplémentaire.

Le profil containerd reste en mode `enforce`. Le chargeur systemd est requis
avant K3s ou K3s Agent et applique le profil à chaque démarrage. L'installation
ne redémarre pas ces services.

Utiliser `systemctl reload k3s-apparmor-profile` pour recharger le profil.
Ne pas utiliser `restart` ou `stop` : arrêter cette dépendance peut arrêter
K3s. Des processus existants peuvent nécessiter une relance contrôlée de
leur application après validation du nouveau profil.

## Validation et maintenance

Valider les services, le mode `enforce`, les nouveaux refus du journal noyau
et les opérations réelles des applications concernées. Pour les navigateurs,
vérifier également la fermeture effective des processus après succès et erreur ;
une réponse HTTP réussie ou une liste de sessions vide ne suffit pas.

Avant une mise à jour du runtime, comparer à nouveau le profil amont et préparer
le retrait du contournement lorsque le correctif officiel est intégré. Le
profil local déjà chargé peut empêcher le runtime d'installer ses nouvelles
règles. Planifier le remplacement du profil pendant une maintenance ; ne pas
décharger le profil de conteneurs actifs sans procédure vérifiée.

Les observations propres à un environnement restent dans les notes privées
locales, hors du dépôt public.
