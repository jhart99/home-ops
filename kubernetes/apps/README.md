# kubernetes/apps/

All application deployments, grouped by Kubernetes namespace. Each namespace directory contains a `kustomization.yaml` listing all apps, and a `namespace.yaml` creating the namespace resource.

## Namespace Inventory

### `kube-system` — Core Infrastructure

| App | Purpose |
|-----|---------|
| cilium | CNI (eBPF, WireGuard encryption, egress gateway, BGP) |
| coredns | Cluster DNS |
| csi-driver-nfs | NFS storage driver |
| descheduler | Pod rescheduling for balance |
| generic-device-plugin | Generic device plugin framework |
| intel-device-plugins | Intel GPU device plugin (i915 iGPU) |
| intel-gpu-resource-driver | Intel GPU resource driver |
| metrics-server | Resource metrics API |
| multus | Secondary network interface support |
| node-feature-discovery | Hardware feature detection |
| nvidia-device-plugin | NVIDIA GPU device plugin |
| nvidia-operator | NVIDIA GPU operator |
| reloader | Auto-restart deployments on ConfigMap/Secret changes |
| snapshot-controller | CSI volume snapshots |
| spegel | Peer-to-peer container image distribution |

### `network` — Networking & Ingress

| App | Purpose |
|-----|---------|
| cloudflare-dns | External DNS via Cloudflare API |
| cloudflare-tunnel | Cloudflared tunnel for external access |
| envoy-gateway | HTTP/HTTPS ingress (internal + external gateways) |
| k8s-gateway | Split-horizon DNS for internal apps |
| openwrt-dns | OpenWrt DNS integration |
| unifi | UniFi controller |

### `cert-manager` — TLS Certificates

| App | Purpose |
|-----|---------|
| cert-manager | Automated TLS certificate provisioning |

### `rook-ceph` — Distributed Block Storage

| App | Purpose |
|-----|---------|
| rook-ceph | Rook operator + Ceph cluster (block, object storage) |

### `openebs-system` — Local Storage

| App | Purpose |
|-----|---------|
| openebs | Local PV provisioner |

### `volsync-system` — Volume Backup & Restore

| App | Purpose |
|-----|---------|
| kopia | Backup client (used by VolSync) |
| volsync | PVC replication and backup/restore |

### `database` — Database Operators

| App | Purpose |
|-----|---------|
| cloudnative-pg | PostgreSQL operator |
| dragonfly | Redis-compatible cache (DragonflyDB cluster) |

### `observability` — Monitoring, Logging & Alerting

| App | Purpose |
|-----|---------|
| blackbox-exporter | External endpoint probing |
| fluent-bit | Log collection and forwarding |
| gatus | Uptime and health monitoring |
| grafana | Dashboards and visualization |
| keda | Event-driven autoscaling |
| kromgo | Metrics badge/visualization endpoint |
| kube-prometheus-stack | Prometheus + Alertmanager + node-exporter |
| openwrt-exporter | OpenWrt metrics exporter |
| silence-operator | Alertmanager silence management |
| smartctl-exporter | Disk health metrics (S.M.A.R.T.) |
| unpoller | UniFi metrics exporter |
| victoria-logs | Log aggregation backend |

### `security` — Authentication & Authorization

| App | Purpose |
|-----|---------|
| authentik | Identity provider / SSO platform |
| oauth2proxy | OAuth2 reverse proxy for app authentication |

### `actions-runner-system` — CI/CD Runners

| App | Purpose |
|-----|---------|
| actions-runner-controller | GitHub Actions runner controller |
| runners/home-ops | Self-hosted runner for this repository |

### `jupyterhub` — Data Science

| App | Purpose |
|-----|---------|
| jupyterhub | Multi-user Jupyter notebook server |

### `flux-system` — GitOps

| App | Purpose |
|-----|---------|
| flux-operator | Flux operator deployment |
| flux-instance | Flux instance configuration |
| flux-sensitive | Flux secrets (GitHub token, webhook) |

### `default` — User Applications

| App | Purpose |
|-----|---------|
| devshell | Interactive dev shell pod |
| echo / echo-basicauth / echo-oidcauth | Echo test services (HTTP routing demos) |
| forgejo | Self-hosted Git service |
| forgejo-runner | Forgejo CI runner |
| freshrss | RSS/Atom feed aggregator |
| home-assistant | Home automation platform |
| immich | Photo and video library |
| mosquitto | MQTT broker (IoT) |
| ollama | Self-hosted LLM runner |
| open-webui | Web UI for Ollama |
| paperless | Document management system |
| pihole | DNS-based ad blocker |
| renovate | Self-hosted Renovate dependency bot |
| smtp-relay | SMTP relay for cluster email |
| wg-easy | WireGuard VPN management UI |
| whoami | Simple HTTP info service |
| zigbee | Zigbee2MQTT (Zigbee IoT bridge) |
| zwave | Z-Wave JS UI (Z-Wave IoT bridge) |

## App Structure

Each app follows this pattern:

```
<namespace>/<app>/
  ks.yaml              # Flux Kustomization — declares path, deps, substitutions
  app/
    helmrelease.yaml   # HelmRelease or raw k8s manifests
    kustomization.yaml # Kustomize config (references components as needed)
    *.sops.yaml        # SOPS-encrypted secrets (if needed)
```

The namespace-level `kustomization.yaml` includes the `sops` component so that `cluster-secrets` is available for variable substitution in all apps.
