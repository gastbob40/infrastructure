# Infrastructure

## Setup K3S Nodes

### Setup Master Node

```bash
curl -sfL https://get.k3s.io | sh -s - --disable traefik --cluster-cidr=10.0.0.0/8 --node-external-ip=<MASTER-IP> --flannel-backend=wireguard-native --flannel-external-ip
```

The kubeconfig file will be located at `/etc/rancher/k3s/k3s.yaml`. You can copy it to your local machine for easier access:

```bash
cat /etc/rancher/k3s/k3s.yaml
```

### Setup Worker Node

1. Get the master token from the master node:

```bash
cat /var/lib/rancher/k3s/server/token
```

```bash
curl -sfL https://get.k3s.io | K3S_URL=https://<MASTER-IP>:6443/ K3S_NODE_NAME="k8s-slave-<INDEX>" K3S_TOKEN=<MASTER-TOKEN> sh -s - --node-external-ip=<NODE-IP>
```
