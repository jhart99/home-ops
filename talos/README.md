# talos/

Talos Linux node configuration managed with [talhelper](https://budimanjojo.github.io/talhelper/latest/). All node configs are generated from `talconfig.yaml` — never edit the generated output in `clusterconfig/` directly.

## Files

| File | Purpose |
|------|---------|
| `talconfig.yaml` | Node definitions: IPs, install disks, extensions, patches |
| `talenv.yaml` | Talos and Kubernetes version pins (Renovate-managed) |
| `talsecret.sops.yaml` | Encrypted cluster secrets (machine tokens, CA certs) |
| `schematic.yaml` | Image factory schematic for x86 nodes |
| `schematic-spark.yaml` | Image factory schematic for ARM (Raspberry Pi) nodes |
| `clusterconfig/` | Generated output — do not edit directly |
| `patches/` | Patch files applied to node configs (see `patches/README.md`) |

## Versions

Current versions are pinned in `talenv.yaml` and automatically updated by Renovate:
- **Talos**: v1.12.5
- **Kubernetes**: v1.35.2

## Node Summary

| Node | Role | IP | Arch | Install Disk | Notes |
|------|------|----|------|--------------|-------|
| talos-cp-1 | controlplane | 192.168.1.11 | x86 | /dev/nvme1n1 | Mellanox NIC, VLAN 10/100 |
| talos-cp-2 | controlplane | 192.168.1.12 | x86 | /dev/nvme1n1 | Mellanox NIC, VLAN 10/100 |
| talos-cp-3 | controlplane | 192.168.1.13 | x86 | /dev/nvme0n1 | Mellanox NIC, VLAN 10/100 |
| talos-worker-1 | worker | 192.168.1.14 | ARM | /dev/mmcblk0 | Raspberry Pi-class, eMMC |
| talos-worker-2 | worker | 192.168.1.15 | ARM | /dev/mmcblk0 | Raspberry Pi-class, eMMC |
| talos-worker-3 | worker | 192.168.1.16 | ARM | /dev/mmcblk0 | Raspberry Pi-class, eMMC |
| talos-worker-4 | worker | 192.168.1.17 | ARM | /dev/mmcblk0 | Raspberry Pi-class, eMMC |
| talos-worker-5 | worker | 192.168.1.18 | ARM64 | /dev/nvme0n1 | NVIDIA GPU (DGX Spark), Mellanox NIC |

**External LB**: `192.168.1.75`, **Internal VIP**: `192.168.1.5` (control plane HA)

## Schematics

Node OS images are built via the [Talos Image Factory](https://factory.talos.dev):

- **x86 nodes** (`schematic.yaml`): Intel-specific fixes (i915, Meteor Lake), Mellanox tools.
- **ARM64 DGX Spark** (`schematic-spark.yaml`): Mellanox and NVIDIA driver extensions.
- **ARM Raspberry Pi**: Standard ARM image with minimal extensions.

Schematic IDs from the factory are referenced in `talconfig.yaml` per node.

## Patches

Patches are applied in layers:

| Directory | Applied To |
|-----------|-----------|
| `patches/global/` | All nodes |
| `patches/controller/` | Control plane nodes only |

Global patches include:
- `cluster.yaml` — cluster-level settings
- `machine-files.yaml` — extra files pushed to nodes
- `machine-kubelet.yaml` — kubelet configuration
- `machine-network.yaml` — network configuration
- `machine-sysctls.yaml` — kernel parameters
- `machine-time.yaml` — NTP configuration

Controller patches include:
- `cluster.yaml` — etcd, API server settings
- `machine-features.yaml` — control plane feature flags
- `machine-diskencryption.yaml` — disk encryption config

## Common Operations

```sh
# Regenerate all node configs after editing talconfig.yaml or patches
task talos:generate-config

# Apply updated config to a single node (non-disruptive if possible)
task talos:apply-node IP=192.168.1.11

# Upgrade Talos on a single node (update talenv.yaml first)
task talos:upgrade-node IP=192.168.1.11

# Upgrade Kubernetes cluster-wide (update talenv.yaml first)
task talos:upgrade-k8s

# Reset all nodes to maintenance mode (DESTRUCTIVE)
task talos:reset
```

## Adding a New Node

1. Boot the node with the appropriate Talos image (see schematic files)
2. Retrieve disk and MAC info while in maintenance mode:
   ```sh
   talosctl get disks -n <ip> --insecure
   talosctl get links -n <ip> --insecure
   ```
3. Add the node definition to `talconfig.yaml`
4. Regenerate and apply:
   ```sh
   task talos:generate-config
   task talos:apply-node IP=<new-node-ip>
   ```
