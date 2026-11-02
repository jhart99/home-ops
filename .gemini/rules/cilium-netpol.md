# Cilium NetworkPolicy Auditing & Diagnostics Guidelines

When working with CiliumNetworkPolicies (CNPs), diagnosing network flows, or auditing default-deny readiness in this cluster:

## 1. Audit Mode Semantics
- Every namespace has CNPs deployed in **audit mode** (`enableDefaultDeny: { ingress: false, egress: false }`, with annotation `policy.cilium.io/audit-mode: "true"`).
- In Hubble and the Grafana **Cilium NetworkPolicy Audit** dashboard (`uid: cilium-netpol-audit`):
  - **Accounted (`match=~"l3-l4|l3-only|l4-only"`)**: Flows explicitly matched and allowed by installed policies.
  - **Unaccounted (`match="all"`)**: Flows that fell through to the default audit allow rule. **These will be dropped once default-deny enforcement is turned on.**

## 2. Reusable Scripts (`scripts/`)
Always prefer using the checked-in scripts rather than rewriting ad-hoc python scripts:

- **Audit Prometheus & Hubble Metrics**:
  ```bash
  # Check top unaccounted flows (match="all") cluster-wide, by namespace, or by workload
  ./scripts/cilium-netpol-audit.py unaccounted [-n <namespace>] [-W <workload>] [-d ingress|egress]

  # View live flow verdict rates broken down by match type (l3-l4, l3-only, l4-only, all)
  ./scripts/cilium-netpol-audit.py verdicts [-W <workload>] [-n <namespace>] [-m <match_type>]

  # Audit non-workload flows (CloudNativePG databases, CRDs, host/world) using -W unknown
  ./scripts/cilium-netpol-audit.py verdicts -W unknown -n security

  # Get accounted vs unaccounted summary and coverage percentage
  ./scripts/cilium-netpol-audit.py summary [-n <namespace>] [-W <workload>]

  # Inspect required Prometheus scrape ports (partitioned by cluster pods vs host/node daemons)
  ./scripts/cilium-netpol-audit.py targets [-v]
  ```

- **Inspect Node-Level BPF Policy Table, Security Identities, and Live Hubble Flows**:
  ```bash
  # Maps pod to node, Cilium agent, endpoint ID, and realized BPF rules
  # (Supports exact pod name, name prefix, or CNPG cluster/label selector)
  ./scripts/cilium-endpoint-inspect.py <pod-name-or-selector> [-n <namespace>] [-p <port>] [--active-only]

  # Inspect numeric Cilium Security Identity labels (e.g. 1417) without kubectl exec
  ./scripts/cilium-endpoint-inspect.py --identity <identity-id>

  # Inspect live Hubble flows across an entire namespace without specifying a pod
  ./scripts/cilium-endpoint-inspect.py -n <namespace> -F [-p <port>] [--syn-only] [--last 30]

  # View live Hubble flows directly for a pod, filtering by port, SYN initiation, or fallthrough
  # (Automatically resolves non-workload identities like CNPG instances and marks [RevNAT return] flows)
  ./scripts/cilium-endpoint-inspect.py <pod-name-or-selector> [-n <namespace>] -F [--syn-only] [--unmatched-only]
  ```

## 3. Critical Cilium Implementation Rules
1. **`kubectl apply` Map Merging Trap**:
   - Modifying `matchLabels` keys in a CNP (e.g. from `app.kubernetes.io/name` to `app`) will cause `kubectl apply` to merge the maps, keeping BOTH keys on the cluster.
   - **Always use `kubectl replace -f <manifest>`** or delete/recreate when changing label selector keys.

2. **Port Range Syntax**:
   - The CiliumNetworkPolicy v2 CRD rejects hyphenated ranges (`port: "6800-7300"`).
   - Use `port: "<start>"` (string) and `endPort: <end>` (integer):
     ```yaml
     toPorts:
       - ports:
           - port: "8080"
             endPort: 8082
             protocol: TCP
     ```

3. **Node/Host Scrape Egress**:
   - Pods scraping node-level daemons (kubelet `10250`, node-exporter `9100`, ceph `9926/9283`, cilium `9962-9965`, etcd `2381`) connect to node IPs (`192.168.1.x`).
   - Node IPs are classified under `host` and `remote-node`, **never** `cluster`. Scrape rules must include `toEntities: [cluster, host, remote-node]`.

4. **Always Specify `toPorts` on Scrape/Egress Rules**:
   - Writing `toEntities: [cluster]` without `toPorts` sets the rule to `port: 0, protocol: ANY`, which Hubble evaluates as fallback audit traffic (`match="all"`).
   - Explicit `toPorts` are required for L4 policy accounting (`match="l4-only"` or `match="l3-l4"`).

5. **DSR & Client Ephemeral Return Ports**:
   - With Cilium Direct Server Return (DSR), incoming replies from Service ClusterIPs or UDP DNS arrive on ephemeral client ports. Ingress policies receiving return traffic must allow L3 ingress without pinning destination ports.

6. **Same-Node Service RevNAT & Return Traffic on Ingress**:
   - When pod A connects to a Kubernetes Service whose backend pod B resides on the same node, tc-level RevNAT translates the return packet (`SYN, ACK`) to the Service IP on the local veth pair before conntrack state marks it as reply.
   - Cilium evaluates the packet against pod A's ingress policy with pod B's security identity. Without an ingress rule, it hits `match="all"` (audit fallthrough).
   - **Resolution in Pod A's `CiliumNetworkPolicy`**:
     1. Add `toServices` for the Service in `egress`.
     2. Add `fromEndpoints` matching pod B's labels (e.g. `cnpg.io/cluster: <cluster-name>`) in `ingress` to explicitly allow the return path without port restrictions.
