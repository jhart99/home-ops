# Scripts

Utility scripts for cluster management and bootstrapping.

## Inventory

| Script | Purpose |
|--------|---------|
| `bootstrap-apps.sh` | Orchestrates the initial deployment of core cluster components (Cilium, Flux, etc.) |
| `cilium-netpol-audit.py` | Audits Cilium NetworkPolicies against live Prometheus & Hubble metrics (finds unaccounted `match=all` flows, coverage %, scrape targets) |
| `cilium-endpoint-inspect.py` | Inspects a pod's Cilium Endpoint ID, Security Identity, and realized BPF policy rules/counters |
| `lib/common.sh` | Shared shell functions for logging, environment checking, and dependency validation |

## Usage

### `bootstrap-apps.sh`

This script is typically called via `task bootstrap:apps`. It performs the following steps:
1. **Wait for Nodes**: Ensures Talos nodes are reachable via `kubectl`.
2. **Apply Namespaces**: Creates all namespaces defined in `kubernetes/apps/`.
3. **Apply SOPS Secrets**: Injects critical bootstrap secrets (GitHub deploy key, age key, cluster secrets) into the cluster.
4. **Apply CRDs**: Renders and applies Custom Resource Definitions from `bootstrap/helmfile.d/00-crds.yaml`.
5. **Sync Helm Releases**: Installs core apps (Cilium, CoreDNS, Spegel) using `helmfile`.

**Environment Variables:**
- `KUBECONFIG`: Path to the cluster kubeconfig (required).
- `TALOSCONFIG`: Path to the Talos config (required).
- `LOG_LEVEL`: Set to `debug`, `info` (default), `warn`, or `error`.

### `cilium-netpol-audit.py`

Audits Cilium NetworkPolicies against live Prometheus & Hubble metrics. Automatically manages `kubectl port-forward` to Prometheus if not already answering locally.

**Commands:**
- `unaccounted`: Lists flows matching `match="all"` (which would be dropped under default-deny).
  ```bash
  # Check unaccounted flows cluster-wide
  ./scripts/cilium-netpol-audit.py unaccounted

  # Filter by namespace and direction
  ./scripts/cilium-netpol-audit.py unaccounted -n observability -d egress
  ```
- `summary`: Prints accounted (`l3-l4`, `l3-only`, `l4-only`) vs unaccounted (`all`) flow rates and coverage percentage.
  ```bash
  ./scripts/cilium-netpol-audit.py summary -n observability
  ```
- `targets`: Queries Prometheus active scrape targets to extract required scrape ports partitioned by cluster pods vs host/node daemons.
  ```bash
  ./scripts/cilium-netpol-audit.py targets
  ```

### `cilium-endpoint-inspect.py`

Maps a Kubernetes Pod to its Cilium Endpoint ID, Security Identity, and node-level BPF policy table.

**Usage:**
```bash
# Inspect by pod name
./scripts/cilium-endpoint-inspect.py prometheus-kube-prometheus-stack-0 -n observability

# Inspect by label selector and filter for a specific port
./scripts/cilium-endpoint-inspect.py -n observability -l app=grafana -p 3000

# Show only rules with active traffic (matched packets > 0)
./scripts/cilium-endpoint-inspect.py -n observability -l app=grafana -p 3000 --active-only
```

### `lib/common.sh`

A library of reusable functions used by other scripts.

**Key Functions:**
- `log`: A colorful, leveled logger (DEBUG, INFO, WARN, ERROR). ERROR logs will exit the script.
- `check_env`: Validates that required environment variables are set.
- `check_cli`: Validates that required CLI tools are installed and available in `PATH`.
