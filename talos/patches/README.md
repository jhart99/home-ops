# Talos Patches

Kustomization patches applied to node configs via [talhelper](https://budimanjojo.github.io/talhelper/latest/). Patches are layered on top of the base config generated from `talconfig.yaml`.

Reference: https://www.talos.dev/v1.7/talos-guides/configuration/patching/

## Patch Directories

| Directory | Applied To |
|-----------|-----------|
| `global/` | All nodes (control plane + workers) |
| `controller/` | Control plane nodes only |
| `worker/` | Worker nodes only (optional, not currently used) |
| `${node-hostname}/` | A specific named node (optional) |

## Global Patches (`global/`)

### `cluster.yaml`
Enables the [aggregation layer](https://kubernetes.io/docs/tasks/extend-kubernetes/configure-aggregation-layer/) for the API server and turns on the `MutatingAdmissionPolicy` feature gate (required for validating/mutating webhooks using CEL expressions).

### `machine-files.yaml`
Injects a containerd config fragment at `/etc/cri/conf.d/20-customization.part`:
- `discard_unpacked_layers = false` — keeps unpacked image layers on disk, improving [Spegel](https://github.com/spegel-org/spegel) peer-to-peer image distribution performance
- `device_ownership_from_security_context = true` — allows containers to own device files based on their security context (needed for GPU and device plugin workloads)

### `machine-kubelet.yaml`
- `serializeImagePulls: false` — allows parallel image pulls instead of sequential, speeding up pod startup
- `nodeIP.validSubnets: [192.168.1.0/24]` — pins kubelet to the primary LAN subnet, preventing it from advertising cluster or VLAN IPs as the node address

### `machine-network.yaml`
- `disableSearchDomain: true` — suppresses the default Talos search domain injection
- `nameservers: [192.168.1.1]` — uses the local router as DNS (Pi-hole/OpenWrt provides split-horizon resolution for in-cluster services)

### `machine-sysctls.yaml`
Kernel parameters tuned for a high-throughput homelab cluster:

| Parameter | Value | Reason |
|-----------|-------|--------|
| `fs.inotify.max_user_watches` | 1048576 | Prevent inotify exhaustion in file-watching apps |
| `fs.inotify.max_user_instances` | 8192 | Same |
| `net.ipv4.neigh.default.gc_thresh*` | 4096/8192/16384 | Prevent ARP cache overflow on large flat network |
| `net.ipv4.tcp_slow_start_after_idle` | 0 | Keep congestion window across idle periods |
| `net.core.rmem_max` / `wmem_max` | 67108864 | 64 MB socket buffers for high-throughput NFS/Ceph |
| `net.ipv4.tcp_congestion_control` | bbr | Google BBR for better throughput over jumbo-frame links |
| `net.ipv4.tcp_fastopen` | 3 | Enable TCP Fast Open on both client and server |
| `net.ipv4.tcp_mtu_probing` | 1 | Enable PMTU discovery (required for jumbo frames) |
| `net.core.default_qdisc` | fq | Fair queuing, pairs with BBR |
| `sunrpc.tcp_slot_table_entries` | 128 | Increase NFS RPC slot count for high parallelism |
| `vm.nr_hugepages` | 1024 | Pre-allocate 2 MB hugepages for Ceph/DPDK workloads |
| `user.max_user_namespaces` | 11255 | Support rootless containers |

### `machine-time.yaml`
NTP servers set to Cloudflare's time service (`162.159.200.1`, `162.159.200.123`) for accurate, low-latency time synchronization.

---

## Controller Patches (`controller/`)

### `cluster.yaml`
Control-plane-specific cluster settings:

- `allowSchedulingOnControlPlanes: true` — runs workloads on control plane nodes (single-operator homelab, so no dedicated workers reserved)
- `admissionControl: $$patch: delete` — removes default Talos admission plugins (e.g., PodSecurity) to allow more flexible workload policies
- `coreDNS: disabled: true` — disables Talos-managed CoreDNS in favour of the Flux-managed deployment in `kube-system`
- `proxy: disabled: true` — disables kube-proxy; Cilium handles all service routing via eBPF
- `controllerManager.bind-address: 0.0.0.0` — exposes controller manager metrics on all interfaces for Prometheus scraping
- `scheduler.bind-address: 0.0.0.0` — same for the scheduler
- **etcd tuning**:
  - `listen-metrics-urls: http://0.0.0.0:2381` — expose etcd metrics for Prometheus
  - `election-timeout: 5000` / `heartbeat-interval: 500` — relaxed timings to tolerate occasional storage latency spikes
  - `advertisedSubnets: [192.168.1.0/24]` — ensures etcd peers over the primary LAN, not VLANs
- **Scheduler topology**: default `PodTopologySpread` constraint spreads pods across nodes (`maxSkew: 1`, `ScheduleAnyway`); `ImageLocality` scoring disabled so image pre-caching doesn't bias scheduling

### `machine-features.yaml`
Enables the **Talos API access from within Kubernetes** (`kubernetesTalosAPIAccess`):
- Allowed namespaces: `actions-runner-system`, `system-upgrade`
- Allowed roles: `os:admin`

This lets the GitHub Actions runner and system-upgrade controller call `talosctl` APIs directly from inside the cluster (e.g., to apply configs or trigger upgrades without external access).

### `machine-diskencryption.yaml`
Encrypts both the `state` and `ephemeral` partitions on control plane nodes using **LUKS2 + TPM2**:
- No passphrase required — the TPM seals the encryption key to the machine's secure boot state
- Disk contents are unreadable if the drive is removed from the machine
- Encryption is transparent to Talos and Kubernetes — no performance impact at runtime
