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
| etcd-defrag | Automated etcd defragmentation job |
| generic-device-plugin | Generic device plugin framework |
| intel-device-plugins | Intel GPU device plugin (i915 iGPU) |
| intel-gpu-resource-driver | Intel GPU DRA resource driver |
| metrics-server | Resource metrics API |
| multus | Secondary network interface support |
| node-feature-discovery | Hardware feature detection |
| nvidia-device-plugin | NVIDIA GPU device plugin |
| nvidia-dra-driver | NVIDIA GPU DRA driver |
| nvidia-gpu-operator | NVIDIA GPU operator |
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
| unifi-dns | UniFi DNS integration |

### `cert-manager` — TLS Certificates

| App | Purpose |
|-----|---------|
| cert-manager | Automated TLS certificate provisioning |
| cert-manager-csi-driver | CSI driver for mounting TLS certificates into pods |
| trust-manager | TLS trust bundle distribution across namespaces |

### `external-secrets` — Secret Management

| App | Purpose |
|-----|---------|
| external-secrets | External Secrets Operator for secret generation and synchronization |

### `rook-ceph` — Distributed Block Storage

| App | Purpose |
|-----|---------|
| rook-ceph | Rook operator + Ceph cluster (block, filesystem, object storage) |

### `openebs-system` — Local Storage

| App | Purpose |
|-----|---------|
| openebs | Local PV provisioner |

### `volsync-system` — Volume Backup & Restore

| App | Purpose |
|-----|---------|
| kopia | Backup client (used by VolSync) |
| volsync | PVC replication and backup/restore |

### `system-upgrade` — Cluster Upgrades

| App | Purpose |
|-----|---------|
| tuppr | Automated Talos OS and Kubernetes upgrade controller |

### `database` — Database Operators

| App | Purpose |
|-----|---------|
| cloudnative-pg | PostgreSQL operator (with Barman Cloud plugin) |
| dragonfly | Redis-compatible cache (DragonflyDB operator & cluster) |
| opensearch-operator | OpenSearch cluster operator |

### `observability` — Monitoring, Logging & Alerting

| App | Purpose |
|-----|---------|
| blackbox-exporter | External and VPN endpoint probing |
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

### `security` — Authentication, Authorization & Scanning

| App | Purpose |
|-----|---------|
| authentik | Identity provider / SSO platform |
| oauth2proxy | OAuth2 reverse proxy for app authentication |
| trivy | Vulnerability and configuration scanning operator & dashboard |

### `ai` — AI, LLM Inference & MCP Servers

| App | Purpose |
|-----|---------|
| agentmemory | Persistent memory service for AI agents |
| comfyui | Node-based image generation (DGX Spark) |
| dgx-resource-claim | DRA GPU resource claims for DGX Spark workloads |
| hermes | Self-hosted AI coding agent and dashboard |
| litellm | LLM API gateway and proxy |
| llmkube | Kubernetes-native LLM inference operator & models (Qwen, llama.cpp, vLLM) |
| open-webui | Web UI for LLM interaction |
| searxng | Privacy-respecting metasearch engine |
| toolhive | Stacklok ToolHive MCP operator, registry, and UI |
| *-mcp | Managed MCP servers (`comfyui-mcp`, `context7-mcp`, `flux-mcp`, `forgejo-mcp`, `github-mcp`, `grafana-mcp`, `kubectl-mcp`, `kubesearch-mcp`, `rea-mcp`, `talos-mcp`, `victorialogs-mcp`, `yardstick`) |

### `communication` — Messaging & Email

| App | Purpose |
|-----|---------|
| signal-cli | Signal messenger REST API bridge |
| smtp-relay | Internal SMTP relay (Maddy) for cluster services |
| tuwunel | Matrix homeserver |

### `development` — Developer Tools

| App | Purpose |
|-----|---------|
| atuin | Shell history sync server |
| backstage | Developer portal and service catalog |
| coder | Self-hosted cloud development environments |

### `rustfs` — Object Storage

| App | Purpose |
|-----|---------|
| rustfs | S3-compatible object storage service |

### `actions-runner-system` — CI/CD Runners

| App | Purpose |
|-----|---------|
| actions-runner-controller | GitHub Actions runner controller |
| runners/home-ops | Self-hosted runner for this repository |

### `flux-system` — GitOps

| App | Purpose |
|-----|---------|
| erecords | External DNS records for cluster services |
| flux-operator | Flux operator deployment |
| flux-instance | Flux instance configuration |
| flux-sensitive | Flux secrets and secondary repository sync |

### `default` — User Applications

| App | Purpose |
|-----|---------|
| blog | Personal blog site |
| forgejo | Self-hosted Git service |
| forgejo-runner | Forgejo Actions CI runner |
| freshrss | RSS/Atom feed aggregator |
| home-assistant | Home automation platform |
| immich | Photo and video library |
| mosquitto | MQTT broker (IoT) |
| paperless | Document management system |
| pihole | DNS-based ad blocker |
| renovate | Self-hosted Renovate operator and jobs (GitHub & Forgejo) |
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