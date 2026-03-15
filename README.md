# home-ops

My personal Kubernetes homelab cluster managed entirely through GitOps. Built on [Talos Linux](https://github.com/siderolabs/talos) with [FluxCD](https://github.com/fluxcd/flux2) for reconciliation, [SOPS + age](https://github.com/getsops/sops) for secrets, and [Cloudflare](https://cloudflare.com) for external access.

## Cluster Overview

| Component | Details |
|-----------|---------|
| OS | Talos Linux v1.12.5 |
| Kubernetes | v1.35.2 |
| CNI | Cilium (eBPF, WireGuard encryption) |
| GitOps | FluxCD |
| Secrets | SOPS + age |
| Storage | Rook-Ceph (block), OpenEBS (local), NFS |
| Ingress | Envoy Gateway |
| DNS | k8s-gateway (internal), Cloudflare (external) |
| Certificates | cert-manager |
| Backups | VolSync + Kopia |

## Hardware

**3 Control Plane Nodes** — VIP: `192.168.1.75`

| Node | IP | Disk | Notes |
|------|----|------|-------|
| talos-cp-1 | 192.168.1.11 | NVMe | Mellanox NIC, MTU 9000 |
| talos-cp-2 | 192.168.1.12 | NVMe | Mellanox NIC, MTU 9000 |
| talos-cp-3 | 192.168.1.13 | NVMe | Mellanox NIC, MTU 9000 |

**5 Worker Nodes**

| Node | IP | Disk | Notes |
|------|----|------|-------|
| talos-worker-1 | 192.168.1.14 | eMMC | ARM (Raspberry Pi-class) |
| talos-worker-2 | 192.168.1.15 | eMMC | ARM (Raspberry Pi-class) |
| talos-worker-3 | 192.168.1.16 | eMMC | ARM (Raspberry Pi-class) |
| talos-worker-4 | 192.168.1.17 | eMMC | ARM (Raspberry Pi-class) |
| talos-worker-5 | 192.168.1.18 | NVMe | x86, NVIDIA GPU, Mellanox NIC |

**Networks**: Pod CIDR `10.42.0.0/16`, Service CIDR `10.43.0.0/16`. Control plane and worker-5 use VLANs 10 (`192.168.2.x`) and 100 (`192.168.100.x`) with jumbo frames (MTU 9000).

## Repository Structure

```
home-ops/
├── bootstrap/          # One-time cluster bootstrap configs (helmfile, SOPS keys)
├── kubernetes/
│   ├── apps/           # Per-namespace application deployments
│   ├── components/     # Reusable Kustomize components (sops, volsync, vpn, etc.)
│   └── flux/           # Flux root Kustomization entry point
└── talos/              # Talos node configuration (talhelper)
```

See sub-directory READMEs for more detail:
- [`kubernetes/`](kubernetes/README.md)
- [`talos/`](talos/README.md)
- [`bootstrap/`](bootstrap/README.md)

## Dev Environment

All tools are managed via [mise](https://mise.jdx.dev/):

```sh
mise trust && pip install pipx && mise install
```

This installs: `task`, `talhelper`, `talosctl`, `flux`, `kubectl`, `helm`, `sops`, `age`, `yq`, `jq`, `cilium`, `kustomize`.

Environment variables (`KUBECONFIG`, `SOPS_AGE_KEY_FILE`, `TALOSCONFIG`) are set automatically by mise.

## Common Tasks

```sh
task                              # List all available tasks
task reconcile                    # Force Flux to sync from git

# Talos management
task talos:generate-config        # Regenerate node configs from talconfig.yaml
task talos:apply-node IP=<ip>     # Apply config to a specific node
task talos:upgrade-node IP=<ip>   # Upgrade Talos on a node
task talos:upgrade-k8s            # Upgrade Kubernetes cluster-wide

# Initial setup only
task bootstrap:talos              # Bootstrap the Talos cluster
task bootstrap:apps               # Bootstrap core apps (Cilium, Flux, etc.)
```

## GitOps Flow

1. Changes are pushed to this repository
2. FluxCD detects changes and reconciles the cluster state
3. `kubernetes/flux/cluster/ks.yaml` is the root entry point
4. It points to `kubernetes/apps/` where all namespaces and apps live
5. SOPS-encrypted secrets are decrypted at reconcile time using the age key

Force a sync at any time:
```sh
task reconcile
# or
flux reconcile kustomization cluster-apps --with-source
```

## Debugging

```sh
# Check Flux health
flux check
flux get ks -A
flux get hr -A

# Check a specific namespace
kubectl -n <namespace> get pods -o wide
kubectl -n <namespace> logs <pod-name> -f
kubectl -n <namespace> describe <resource> <name>
kubectl -n <namespace> get events --sort-by='.metadata.creationTimestamp'
```

## Maintenance

### Update Talos node configuration

```sh
task talos:generate-config
task talos:apply-node IP=<node-ip>
```

### Upgrade Talos or Kubernetes

Update versions in `talos/talenv.yaml`, then:

```sh
task talos:upgrade-node IP=<node-ip>   # Per-node Talos upgrade
task talos:upgrade-k8s                  # Cluster-wide Kubernetes upgrade
```

### Reset the cluster

```sh
task talos:reset   # DESTRUCTIVE: resets all nodes to maintenance mode
```

## Secrets

Secrets are encrypted with SOPS using an age key at `age.key`. Files matching `*.sops.yaml` are encrypted.

```sh
# Encrypt a new secret file in-place
sops --encrypt --in-place path/to/file.sops.yaml
```

See [CLAUDE.md](CLAUDE.md) for detailed architecture notes used by Claude Code.
