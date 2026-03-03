# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

A home Kubernetes cluster managed via GitOps. It uses **Talos Linux** as the OS, **FluxCD** for GitOps reconciliation, **SOPS + age** for secret encryption, **talhelper** for Talos config generation, and **Cloudflare** for external DNS and tunneling.

## Dev Environment

All CLI tools are managed via [mise](https://mise.jdx.dev/). Install with:

```sh
mise trust && pip install pipx && mise install
```

Key tools installed: `task`, `talhelper`, `talosctl`, `flux`, `kubectl`, `helm`, `sops`, `age`, `yq`, `jq`, `cilium`, `kustomize`.

Environment variables are automatically set by mise (`KUBECONFIG`, `SOPS_AGE_KEY_FILE`, `TALOSCONFIG`).

## Task Commands

```sh
task                              # List all available tasks
task reconcile                    # Force Flux to sync from git
task bootstrap:talos              # Bootstrap the Talos cluster (initial setup)
task bootstrap:apps               # Bootstrap core apps into cluster (initial setup)
task talos:generate-config        # Regenerate Talos node configs from talconfig.yaml
task talos:apply-node IP=<ip>     # Apply updated Talos config to a specific node
task talos:upgrade-node IP=<ip>   # Upgrade Talos version on a specific node
task talos:upgrade-k8s            # Upgrade Kubernetes version cluster-wide
task talos:reset                  # Reset all nodes back to maintenance mode (DESTRUCTIVE)
```

## Cluster Architecture

**3 control plane nodes** (192.168.1.11-13, VIP at 192.168.1.75) + **5 worker nodes** (192.168.1.14-18):
- Workers 1-4 are ARM (Raspberry Pi-class, eMMC storage)
- Worker 5 is x86 with NVIDIA GPU and Mellanox NIC
- Pod network: `10.42.0.0/16`, Service network: `10.43.0.0/16`

**Network layout**: Control plane and worker-5 nodes use Mellanox NICs (`mlx5`) with jumbo frames (MTU 9000) and two VLANs (VLAN 10 @ 192.168.2.x, VLAN 100 @ 192.168.100.x). Layer2 VIPs provide HA for gateways.

**CNI**: Cilium with eBPF (kernel lockdown disabled to support this). Multus provides secondary network interfaces using the aliased `net0` interface name.

## GitOps Structure (Flux)

Flux watches this repo and reconciles the `kubernetes/` directory. The root entry point is `kubernetes/flux/cluster/ks.yaml` which creates a top-level Kustomization pointing to `kubernetes/apps/`.

**Per-app pattern** (two-level structure):
```
kubernetes/apps/<namespace>/<app>/
  ks.yaml          # Flux Kustomization resource (defines path, dependencies, substitutions)
  app/
    helmrelease.yaml    # HelmRelease or other k8s manifests
    kustomization.yaml  # Kustomize config
    *.sops.yaml         # Encrypted secrets (if needed)
```

**Namespace `kustomization.yaml`** files reference all app `ks.yaml` files and include the `sops` component to make `cluster-secrets` available for variable substitution.

## Reusable Kustomize Components

Located in `kubernetes/components/`, these are referenced in app `ks.yaml` files:

- **`sops`** — Deploys `cluster-secrets` Secret (encrypted); needed by any app using `substituteFrom: cluster-secrets`
- **`volsync`** — Adds PVC + VolSync replication resources; used by stateful apps that need backup/restore. Requires `APP` and `VOLSYNC_CAPACITY` substitution vars.
- **`nfs-scaler`** — KEDA ScaledObject for NFS-backed workloads
- **`vpn`** — Cilium EgressGatewayPolicy for VPN routing
- **`alerts`** — Flux Alertmanager/GitHub status alert configs

## Secrets (SOPS)

Files matching `*.sops.yaml` are SOPS-encrypted with an age key stored at `age.key` (root of repo).

- **`talos/*.sops.yaml`**: Entire file is MAC-only encrypted (full file encryption)
- **`kubernetes/*.sops.yaml` and `bootstrap/*.sops.yaml`**: Only `data` and `stringData` fields are encrypted (the rest of the YAML is plaintext)

To encrypt a new secret file:
```sh
sops --encrypt --in-place path/to/file.sops.yaml
```

The `cluster-secrets` Secret (in `kubernetes/components/sops/cluster-secrets.sops.yaml`) is the primary source for cluster-wide variable substitution in Flux Kustomizations.

## Talos Configuration

| File | Purpose |
|------|---------|
| `talos/talconfig.yaml` | Node definitions (IPs, disks, patches, image URLs) |
| `talos/talenv.yaml` | Talos and Kubernetes versions (annotated for Renovate) |
| `talos/schematic.yaml` | Image customization (kernel args, system extensions) for x86 nodes |
| `talos/schematic-spark.yaml` | Image customization for ARM worker nodes |
| `talos/patches/global/` | Applied to all nodes |
| `talos/patches/controller/` | Applied only to control plane nodes |
| `talos/clusterconfig/` | Generated output — do not edit directly |

After changing `talconfig.yaml` or patches, regenerate configs with `task talos:generate-config`, then apply with `task talos:apply-node IP=<ip>`.

## Namespaces / App Organization

| Namespace | Contents |
|-----------|---------|
| `kube-system` | cilium, coredns, csi-driver-nfs, metrics-server, multus, node-feature-discovery, reloader, spegel, intel/nvidia device plugins |
| `network` | envoy-gateway (HTTP routing), cloudflare-tunnel, cloudflare-dns, k8s-gateway, unifi |
| `cert-manager` | TLS certificate management |
| `rook-ceph` | Distributed block storage |
| `openebs-system` | OpenEBS local storage |
| `volsync-system` | Volume backup/restore |
| `observability` | kube-prometheus-stack, grafana, victoria-logs, gatus, fluent-bit, keda, etc. |
| `security` | Security tooling |
| `database` | Database workloads |
| `default` | User apps: forgejo, freshrss, immich, paperless, pihole, wg-easy, etc. |

## HTTP Routing

- **Internal** apps: use `envoy-internal` gateway on `HTTPRoutes`
- **External/public** apps: use `envoy-external` gateway on `HTTPRoutes`
- External-DNS manages public DNS records automatically for external routes
- k8s-gateway provides split-horizon DNS for internal resolution

## CI / Renovate

- **flux-local** GitHub Action (`flux-local test` + `flux-local diff`) runs on every PR touching `kubernetes/**` to validate Helm and Kustomize configs and post diffs as PR comments.
- **Renovate** runs on weekends and opens PRs for container images, Helm charts, GitHub Actions, and mise tools. Semantic commit types are enforced (feat/fix/chore). SOPS-encrypted files are excluded from Renovate scanning.
