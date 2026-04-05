# Scripts

Utility scripts for cluster management and bootstrapping.

## Inventory

| Script | Purpose |
|--------|---------|
| `bootstrap-apps.sh` | Orchestrates the initial deployment of core cluster components (Cilium, Flux, etc.) |
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

### `lib/common.sh`

A library of reusable functions used by other scripts.

**Key Functions:**
- `log`: A colorful, leveled logger (DEBUG, INFO, WARN, ERROR). ERROR logs will exit the script.
- `check_env`: Validates that required environment variables are set.
- `check_cli`: Validates that required CLI tools are installed and available in `PATH`.
