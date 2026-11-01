# Instructions du dépôt

Ce dépôt est public. Ne pas y publier de secrets ni de notes d'exploitation
propres à un environnement : inventaire des hôtes, adresses, journaux,
état des services ou usages de services tiers. Conserver ces informations
dans `.local/operations/`, exclu de Git, et contrôler les fichiers indexés
avant chaque commit.

Avant une modification K3s, containerd ou AppArmor, lire
[hosts/apparmor/README.md](hosts/apparmor/README.md). Consulter également
`.local/operations/apparmor.md` si ce fichier privé existe dans le checkout.

Un profil AppArmor local peut masquer les évolutions du profil du runtime.
Réévaluer sa compatibilité à chaque montée de version et prévoir son retrait
lorsque le correctif amont est intégré et validé. Ne pas désactiver AppArmor
pour contourner un problème de compatibilité.
