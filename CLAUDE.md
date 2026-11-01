# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

A home Kubernetes cluster managed via GitOps. It uses **Talos Linux** as the OS, **FluxCD** for GitOps reconciliation, **SOPS + age** for secret encryption, **TOPF** for Talos config and lifecycle management, and **Cloudflare** for external DNS and tunneling.

## Dev Environment

All CLI tools are managed via [mise](https://mise.jdx.dev/). Install with:

```sh
mise trust && pip install pipx && mise install
```

Key tools installed: `task`, `topf`, `talosctl`, `flux`, `kubectl`, `helm`, `sops`, `age`, `yq`, `jq`, `cilium`, `kustomize`.

Environment variables are automatically set by mise (`KUBECONFIG`, `SOPS_AGE_KEY_FILE`, `TALOSCONFIG`).

## Task Commands

```sh
task                              # List all available tasks
task reconcile                    # Force Flux to sync from git
task bootstrap:talos              # Bootstrap the Talos cluster (initial setup)
task bootstrap:apps               # Bootstrap core apps into cluster (initial setup)
task talos:generate-config        # Render Talos node configs and generate talosconfig
task talos:apply                  # Apply updated Talos config to all nodes
task talos:apply-node IP=<ip>     # Apply updated Talos config to a specific node
task talos:upgrade                # Upgrade Talos version across all nodes
task talos:upgrade-node IP=<ip>   # Upgrade Talos version on a specific node
task talos:upgrade-k8s            # Upgrade Kubernetes version cluster-wide
task talos:reset                  # Reset all nodes back to maintenance mode (DESTRUCTIVE)
```

## Cluster Architecture

**3 control plane nodes** (192.168.1.11-13, External LB at 192.168.1.75, Internal VIP at 192.168.1.5) + **2 worker nodes** (192.168.1.18-19):
- Worker 5 (`talos-worker-5`, 192.168.1.18) is ARM64 (DGX Spark) with NVIDIA GPU and Mellanox NIC
- Worker 6 (`talos-worker-6`, 192.168.1.19) is x86_64 general purpose worker
- Pod network: `10.42.0.0/16` (`/22` per-node CIDR mask, `maxPods: 500`), Service network: `10.43.0.0/16`

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
- **`intel-gpu`** — Adds a DRA `ResourceClaimTemplate` for Intel GPU access
- **`nfs-scaler`** — KEDA ScaledObject for NFS-backed workloads
- **`vpn`** — Cilium EgressGatewayPolicy for VPN routing
- **`deny-all` / `egress`** — Baseline CiliumNetworkPolicies for default-deny and common DNS/API egress
- **`alerts`** — Flux Alertmanager/GitHub status alert configs

## Secrets (SOPS)

Files matching `*.sops.yaml` and `talos/secrets.yaml` are SOPS-encrypted with an age key stored at `age.key` (root of repo).

- **`talos/secrets.yaml` and `talos/*.sops.yaml`**: Entire file is MAC-only encrypted (full file encryption)
- **`kubernetes/*.sops.yaml` and `bootstrap/*.sops.yaml`**: Only `data` and `stringData` fields are encrypted (the rest of the YAML is plaintext)

To encrypt a new secret file:
```sh
sops --encrypt --in-place path/to/file.sops.yaml
```

The `cluster-secrets` Secret (in `kubernetes/components/sops/cluster-secrets.sops.yaml`) is the primary source for cluster-wide variable substitution in Flux Kustomizations.

## Talos Configuration

| File / Directory | Purpose |
|------------------|---------|
| `talos/topf.yaml` | Cluster & node definitions, Talos and Kubernetes versions (annotated for Renovate), schematic references |
| `talos/secrets.yaml` | SOPS-encrypted Talos secrets bundle |
| `talos/schematic.yaml` | Image customization (kernel args, system extensions) for x86 nodes |
| `talos/schematic-qemu.yaml` | Image customization for QEMU/VM nodes |
| `talos/schematic-spark.yaml` | Image customization for ARM64 DGX Spark worker nodes |
| `talos/all/` | Patches applied to all nodes |
| `talos/control-plane/` | Patches applied only to control plane nodes |
| `talos/node/<host>/` | Node-specific patches (install disks, kernel modules, network interfaces) |

After changing `topf.yaml` or patches, apply with `task talos:apply` or `task talos:apply-node IP=<ip>`.

## Namespaces / App Organization

| Namespace | Contents |
|-----------|---------|
| `kube-system` | cilium, coredns, csi-driver-nfs, descheduler, etcd-defrag, metrics-server, multus, node-feature-discovery, reloader, snapshot-controller, spegel, intel/nvidia device plugins & DRA drivers |
| `network` | envoy-gateway (HTTP routing), cloudflare-tunnel, cloudflare-dns, k8s-gateway, openwrt-dns, unifi-dns |
| `cert-manager` | cert-manager, trust-manager, cert-manager-csi-driver |
| `external-secrets` | External Secrets Operator |
| `rook-ceph` | Distributed block, filesystem, and object storage |
| `openebs-system` | OpenEBS local storage |
| `volsync-system` | Volume backup/restore (VolSync + Kopia) |
| `system-upgrade` | Automated Talos and Kubernetes upgrades via Tuppr |
| `observability` | kube-prometheus-stack, grafana, victoria-logs, gatus, fluent-bit, keda, kromgo, blackbox-exporter, smartctl-exporter, unpoller, silence-operator |
| `security` | authentik, oauth2proxy, trivy |
| `database` | cloudnative-pg, dragonfly, opensearch-operator |
| `ai` | toolhive (MCP operator/registry/UI), MCP servers (forgejo, github, grafana, kubectl, kubesearch, talos, victorialogs, comfyui, context7, rea, searxng, yardstick), hermes, llmkube, litellm, open-webui, comfyui, agentmemory, searxng |
| `communication` | tuwunel (Matrix), signal-cli, smtp-relay |
| `development` | atuin, backstage, coder |
| `rustfs` | RustFS object storage |
| `actions-runner-system` | GitHub Actions Runner Controller and self-hosted runner scale sets |
| `default` | User apps: blog, forgejo, forgejo-runner, freshrss, home-assistant, immich, mosquitto, paperless, pihole, renovate, wg-easy, whoami, zigbee, zwave |

## HTTP Routing

- **Internal** apps: use `envoy-internal` gateway on `HTTPRoutes`
- **External/public** apps: use `envoy-external` gateway on `HTTPRoutes`
- External-DNS manages public DNS records automatically for external routes
- k8s-gateway provides split-horizon DNS for internal resolution

## CI / Renovate

- **flux-local** GitHub Action (`flux-local test` + `flux-local diff`) runs on every PR touching `kubernetes/**` to validate Helm and Kustomize configs and post diffs as PR comments.
- **Renovate** runs on weekends and opens PRs for container images, Helm charts, GitHub Actions, and mise tools. Semantic commit types are enforced (feat/fix/chore). SOPS-encrypted files are excluded from Renovate scanning.

## Cilium NetworkPolicy Auditing & Diagnostics

The cluster is migrating toward strict default-deny Cilium NetworkPolicies. All policies currently run in **audit mode** (`enableDefaultDeny: { ingress: false, egress: false }`, annotated with `policy.cilium.io/audit-mode: "true"`).

### Policy Verdicts
In Hubble and the Grafana **Cilium NetworkPolicy Audit** dashboard (`uid: cilium-netpol-audit`):
- **Accounted (`match=~"l3-l4|l3-only|l4-only"`)**: Flows explicitly permitted by an installed policy rule.
- **Unaccounted (`match="all"`)**: Flows that fell through to the default audit allow rule. **These will be dropped once default-deny enforcement is turned on.**

### Diagnostic Scripts (`scripts/`)
Two zero-dependency Python tools automate metric queries and node-level BPF inspection:

1. **`scripts/cilium-netpol-audit.py`** (Prometheus & Hubble metric auditor; automatically sets up `kubectl port-forward`):
   ```sh
   # Show top unaccounted flows (match="all") cluster-wide, per-namespace, or per-workload
   ./scripts/cilium-netpol-audit.py unaccounted
   ./scripts/cilium-netpol-audit.py unaccounted -n observability -d egress
   ./scripts/cilium-netpol-audit.py unaccounted -W wg-easy

   # View active traffic verdict rates broken down by match type (l3-l4, l3-only, l4-only, all)
   ./scripts/cilium-netpol-audit.py verdicts -W wg-easy

   # Summary of accounted vs unaccounted rate and coverage percentage
   ./scripts/cilium-netpol-audit.py summary -n observability
   ./scripts/cilium-netpol-audit.py summary -W wg-easy

   # Discover all active Prometheus scrape target ports partitioned by entity (cluster vs node)
   ./scripts/cilium-netpol-audit.py targets -v
   ```

2. **`scripts/cilium-endpoint-inspect.py`** (Maps K8s pods to node-level Cilium endpoints, BPF tables, and Hubble flows):
   ```sh
   # Inspect BPF rules by pod name or label selector
   ./scripts/cilium-endpoint-inspect.py prometheus-kube-prometheus-stack-0 -n observability
   ./scripts/cilium-endpoint-inspect.py -n observability -l app=grafana -p 3000

   # Show only BPF rules with active traffic (matched packets > 0)
   ./scripts/cilium-endpoint-inspect.py -n observability -l app=grafana -p 3000 --active-only

   # View live Hubble flows directly for the pod without knowing which node it runs on
   ./scripts/cilium-endpoint-inspect.py -n default -l app.kubernetes.io/name=wg-easy -F --last 20

   # Filter Hubble flows for policy fallthrough (match='all' or dropped)
   ./scripts/cilium-endpoint-inspect.py -n default -l app.kubernetes.io/name=wg-easy -F --unmatched-only
   ```

### Critical Rules & Gotchas
- **`kubectl apply` 3-Way Map Merging**: When changing `matchLabels` keys in a CNP (e.g. from `app.kubernetes.io/name: foo` to `app: foo`), `kubectl apply` merges the maps and retains both labels, preventing pods from matching. **Always use `kubectl replace -f <manifest>`** or delete/recreate when modifying label keys.
- **Cilium Port Range Syntax**: The v2 CRD requires `port: "<start>"` as a string and `endPort: <end>` as an integer:
  ```yaml
  toPorts:
    - ports:
        - port: "8080"
          endPort: 8082
          protocol: TCP
  ```
  *(Do NOT use `port: "8080-8082"`, which fails with `strconv.ParseUint: invalid syntax`.)*
- **Scrape Egress Requires Node Entities**: Node-level metrics (kubelet `10250`, node-exporter `9100`, ceph `9926/9283`, cilium `9962-9965`, etcd `2381`) bind to node IPs (`192.168.1.x`), which are classified as `host` or `remote-node`, **not** `cluster`. Scrape egress rules must specify `toEntities: [cluster, host, remote-node]`.
- **`toEntities` Without Ports Causes `match="all"`**: Specifying `toEntities: [cluster]` without `toPorts` places the rule in wildcard `port: 0, protocol: ANY`, causing Hubble to evaluate flows against the default fallback (`match="all"`). Explicit `toPorts` are required for L4 accounting.
- **DSR & Return Traffic**: In Cilium DSR mode, reply packets from Service ClusterIPs or UDP DNS arrive on client ephemeral ports. Ingress policies for servers receiving replies should permit L3 ingress without restrictive `toPorts`.
