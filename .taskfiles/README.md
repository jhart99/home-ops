# Taskfiles

Modular [Go Task](https://taskfile.dev/) definitions for cluster management. Tasks are split into functional domains for better organization.

## Organization

The root `Taskfile.yaml` includes modular taskfiles from this directory:

| Directory | Scope | Prefix |
|-----------|-------|--------|
| `bootstrap/` | Initial cluster setup (Talos & Apps) | `bootstrap:` |
| `talos/` | Talos node and cluster configuration management | `talos:` |

## Inventory

### Bootstrap (`bootstrap/`)

| Task | Description |
|------|-------------|
| `bootstrap:talos` | Generates secrets, creates Talos configurations, and bootstraps the control plane. |
| `bootstrap:apps` | Deploys core cluster components (Cilium, Flux) into the cluster via `bootstrap-apps.sh`. |

### Talos (`talos/`)

| Task | Description |
|------|-------------|
| `talos:generate-config` | Regenerates Talos node configurations from `talconfig.yaml` and patches. |
| `talos:apply-node` | Applies current configuration to a specific node by IP. |
| `talos:upgrade-node` | Performs a rolling Talos OS upgrade for a single node. |
| `talos:upgrade-k8s` | Performs a cluster-wide Kubernetes control plane upgrade. |
| `talos:remove-node` | Safely cordons, drains, and resets a node to remove it from the cluster. |
| `talos:reset` | Resets all nodes back to maintenance mode (DESTRUCTIVE). |

## Usage Examples

```sh
# Apply configuration to a worker node
task talos:apply-node IP=192.168.1.14

# Upgrade Kubernetes to the version in talenv.yaml
task talos:upgrade-k8s

# Reconcile Flux manually
task reconcile
```
