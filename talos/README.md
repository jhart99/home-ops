# talos/

Talos Linux node configuration managed with [TOPF](https://github.com/postfinance/topf). Configuration is composed from `topf.yaml` and layered patch directories (`all/`, `control-plane/`, `node/<host>/`).

## Files

| File / Directory | Purpose |
|------------------|---------|
| `topf.yaml` | Cluster & node definitions, Talos and Kubernetes version pins (Renovate-managed), and schematic references |
| `secrets.yaml` | SOPS-encrypted cluster secrets bundle (machine tokens, CA certs) |
| `schematic.yaml` | Image factory schematic for x86 nodes |
| `schematic-qemu.yaml` | Image factory schematic for QEMU/VM nodes |
| `schematic-spark.yaml` | Image factory schematic for ARM64 DGX Spark nodes |
| `all/` | Patches applied to all nodes |
| `control-plane/` | Patches applied to control-plane nodes |
| `node/<host>/` | Node-specific patches (install disks, kernel modules, network interfaces) |
| `output/` | Optional rendered output from `topf render` (gitignored) |

## Versions

Current versions are pinned in `topf.yaml` and automatically updated by Renovate:
- **Talos**: v1.14.5
- **Kubernetes**: v1.37.2

## Node Summary

| Node | Role | IP | Arch | Pod CIDR | Max Pods | Install Disk | Notes |
|------|------|----|------|----------|----------|--------------|-------|
| talos-cp-1 | control-plane | 192.168.1.11 | x86_64 | `10.42.20.0/22` | 500 | `/dev/disk/by-id/nvme-eui.0025388c91511427` | Mellanox NIC, VLAN 10/100 |
| talos-cp-2 | control-plane | 192.168.1.12 | x86_64 | `10.42.16.0/22` | 500 | `/dev/disk/by-id/nvme-eui.002538d921a1a1e3` | Mellanox NIC, VLAN 10/100 |
| talos-cp-3 | control-plane | 192.168.1.13 | x86_64 | `10.42.12.0/22` | 500 | `/dev/disk/by-id/nvme-eui.001b448b4679b087` | Mellanox NIC, VLAN 10/100 |
| talos-worker-5 | worker | 192.168.1.18 | ARM64 | `10.42.8.0/22` | 500 | `/dev/nvme0n1` | NVIDIA GPU (DGX Spark), Mellanox NIC |
| talos-worker-6 | worker | 192.168.1.19 | x86_64 | `10.42.0.0/22` | 500 | `/dev/sda` | General Purpose SSD |

**External LB**: `192.168.1.75`, **Internal VIP**: `192.168.1.5` (control plane HA)
**Networks**: Pod CIDR `10.42.0.0/16` (`/22` per-node CIDR mask), Service CIDR `10.43.0.0/16`

## Schematics

Node OS images are built via the [Talos Image Factory](https://factory.talos.dev):

- **x86 nodes** (`schematic.yaml`): Intel-specific fixes (i915, Meteor Lake), Mellanox tools.
- **ARM64 DGX Spark** (`schematic-spark.yaml`): Mellanox and NVIDIA driver extensions.
- **QEMU/VM** (`schematic-qemu.yaml`): Intel Xe + QEMU guest agent extensions.

Schematics are referenced directly in `topf.yaml` via `@schematic.yaml` and `@schematic-spark.yaml` and resolved by TOPF.

## Patches

Patches are applied in layers (lexicographical order within each directory):

| Directory | Applied To |
|-----------|-----------|
| `all/` | All nodes |
| `control-plane/` | Control plane nodes only |
| `node/<host>/` | Specific host only |

Global patches (`all/`):
- `01-cluster.yaml` — machine certSANs, pod/service subnets (`KubeNetworkConfig`), disable built-in Flannel CNI (`KubeFlannelCNIConfig`)
- `02-hostname.yaml.tpl` — static `HostnameConfig` from `nodes[].host`
- `03-machine-files.yaml` — containerd CRI customization (`CRICustomizationConfig`)
- `04-machine-kubelet.yaml` — kubelet configuration (`KubeletConfig` with `maxPods: 500`, `kubeAPIQPS: 100`, `kubeAPIBurst: 200`, and `KubeNodeConfig`)
- `05-machine-network.yaml` — DNS resolver settings (`ResolverConfig`)
- `06-machine-sysctls.yaml` — kernel parameters (`SysctlConfig`, including `fs.file-max: 2097152`)
- `07-machine-time.yaml` — NTP configuration (`TimeSyncConfig`)

Control plane patches (`control-plane/`):
- `01-cluster.yaml` — etcd, API server, controller manager (`node-cidr-mask-size-ipv4: "22"`), scheduler, CoreDNS, kube-proxy, and control-plane scheduling settings
- `02-machine-features.yaml` — Kubernetes Talos API access (`KubeTalosAPIAccessConfig`)
- `03-machine-diskencryption.yaml` — LUKS2 + TPM disk encryption config (`VolumeConfig`, currently commented out)

## Common Operations

```sh
# Render all node configs to talos/output/ and generate talosconfig
task talos:generate-config

# Apply updated config to all nodes (or a single node)
task talos:apply
task talos:apply-node IP=192.168.1.11

# Upgrade Talos across all nodes or on a single node (update topf.yaml first)
task talos:upgrade
task talos:upgrade-node IP=192.168.1.11

# Upgrade Kubernetes cluster-wide (update topf.yaml first)
task talos:upgrade-k8s

# Reset all nodes to maintenance mode (DESTRUCTIVE)
task talos:reset
```

## Adding a New Node

1. Boot the node with the appropriate Talos image (see schematic files and `topf schematic-ids`)
2. Retrieve disk and MAC info while in maintenance mode:
   ```sh
   talosctl get disks -n <ip> --insecure
   talosctl get links -n <ip> --insecure
   ```
3. Add the node definition to `topf.yaml` and create its patches under `talos/node/<hostname>/`
4. Apply the configuration:
   ```sh
   task talos:apply-node IP=<new-node-ip>
   ```
