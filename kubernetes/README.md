# kubernetes/

This directory contains all Kubernetes manifests managed by FluxCD.

## Structure

```
kubernetes/
├── apps/           # Application deployments, grouped by namespace
├── components/     # Reusable Kustomize components shared across apps
└── flux/
    └── cluster/
        └── ks.yaml # Root Flux Kustomization — entry point for the entire cluster
```

## How Flux Works Here

The root entry point is `flux/cluster/ks.yaml`. It creates a `Kustomization` resource pointing at `./kubernetes/apps/`, which in turn discovers all per-namespace `kustomization.yaml` files. Each namespace's kustomization lists individual app `ks.yaml` files.

Every `ks.yaml` is a Flux `Kustomization` that:
- Points to an `app/` subdirectory containing Helm releases and other manifests
- Declares dependencies on other Kustomizations (e.g., storage must be ready before stateful apps)
- Configures SOPS decryption for any encrypted secrets in scope
- Uses `substituteFrom: cluster-secrets` for variable interpolation

## Per-App Directory Pattern

```
apps/<namespace>/<app-name>/
  ks.yaml              # Flux Kustomization
  app/
    helmrelease.yaml   # HelmRelease (or raw manifests)
    kustomization.yaml # Kustomize config
    *.sops.yaml        # Encrypted secrets (optional)
```

## Namespaces

| Namespace | Purpose |
|-----------|---------|
| `kube-system` | Core cluster infrastructure (CNI, DNS, storage drivers, GPU plugins & DRA drivers) |
| `network` | Ingress, DNS, Cloudflare tunnel |
| `cert-manager` | TLS certificate management & trust distribution |
| `external-secrets` | External Secrets Operator |
| `rook-ceph` | Distributed block, filesystem, and object storage |
| `openebs-system` | Local persistent storage |
| `volsync-system` | Volume backup and restore |
| `system-upgrade` | Automated Talos and Kubernetes upgrades (Tuppr) |
| `database` | Database operators (CloudNative-PG, Dragonfly, OpenSearch) |
| `observability` | Monitoring, logging, alerting |
| `security` | Authentication & security scanning (Authentik, OAuth2 proxy, Trivy) |
| `ai` | AI workloads, LLM inference (LLMKube, LiteLLM), agents (Hermes), and ToolHive MCP servers |
| `communication` | Matrix homeserver (Tuwunel), Signal CLI, and SMTP relay |
| `development` | Developer tools (Atuin, Backstage, Coder) |
| `rustfs` | S3-compatible object storage (RustFS) |
| `actions-runner-system` | GitHub Actions self-hosted runners |
| `flux-system` | Flux operator and instance |
| `default` | User applications |

See [`apps/README.md`](apps/README.md) and [`apps/default/README.md`](apps/default/README.md) for the full app inventory.

## Reusable Components

See [`components/README.md`](components/README.md) for details on the shared components available to all apps.

## HTTP Routing

- **Internal apps**: use `envoy-internal` gateway on `HTTPRoutes`
- **External/public apps**: use `envoy-external` gateway on `HTTPRoutes`

External-DNS automatically creates public Cloudflare DNS records for routes using the external gateway. k8s-gateway provides split-horizon DNS for internal resolution.
