# Mémoire du projet

## AppArmor après la mise à jour Ubuntu/noyau — 30 octobre 2026

Un contournement temporaire est installé sur les trois nœuds (`k8s`,
`k8s-s1`, `k8s-s2`) pour une incompatibilité entre AppArmor et containerd
après les mises à jour Ubuntu 26.04/noyau 7. PostgreSQL restait en démarrage,
Meal Planner échouait à se connecter à la base et Authentik ne pouvait plus
envoyer certains signaux entre processus.

**Avant toute prochaine mise à jour K3s/containerd, lire
[hosts/apparmor/README.md](hosts/apparmor/README.md).** Le profil local est
basé sur containerd `v2.3.4-k3s1.36` (K3s `v1.36.5+k3s1`) et peut masquer
les évolutions du profil fourni par une nouvelle version. Réévaluer le
contournement à chaque montée de version et prévoir son retrait une fois
le correctif officiel intégré et validé. Ne pas désactiver AppArmor pour
contourner ce problème.

Références : [incident containerd #14485](https://github.com/containerd/containerd/issues/14485),
[correctif proposé #14487](https://github.com/containerd/containerd/pull/14487).
