# Cilium NetworkPolicy Auditing & Diagnostics Guidelines

When working with CiliumNetworkPolicies (CNPs), diagnosing network flows, or auditing default-deny readiness in this cluster:

## 1. Enforce & Audit Mode Semantics
- Every namespace includes `components/deny-all` (`default-deny-ingress` and `default-deny-egress`) and per-application `CiliumNetworkPolicy` resources with default-deny enforcement enabled (`enableDefaultDeny` omitted / `true`).
- Cluster-wide baseline `CiliumClusterwideNetworkPolicy` (CCNP) rules in `kubernetes/apps/kube-system/cilium/app/networkpolicies.yaml` provide shared cluster plumbing:
  - `allow-dns`: Pod egress to `kube-system/kube-dns` (`53/UDP`, `53/TCP`) + ingress from `kube-dns` for same-node RevNAT replies.
  - `allow-k8s-api`: Pod egress to `default/kubernetes` Service and `kube-apiserver` / `host` / `remote-node` (`6443/TCP`, `443/TCP`).
  - `allow-prometheus-scrape`: Pod ingress from `observability/prometheus`.
  - `allow-volsync-mover`: VolSync mover job (`app.kubernetes.io/created-by: volsync`) egress to `volsync-system/kopia` (`80/TCP`).
  - `allow-envoy-egress`: Envoy Gateway proxy (`envoy-internal` / `envoy-external`) egress to all cluster pods (`io.kubernetes.pod.namespace: Exists`) and `world:443/TCP` (OIDC).
  - `allow-envoy-ingress`: Pod ingress across all namespaces from `envoy-internal` and `envoy-external` proxies.
- In Hubble and the Grafana **Cilium NetworkPolicy Audit** dashboard (`uid: cilium-netpol-audit`):
  - **Accounted (`match=~"l3-l4|l3-only|l4-only"`)**: Flows explicitly matched and allowed by installed policies.
  - **Enforced Drops (`action="dropped", match="none"`)**: Flows actively blocked by default-deny enforcement.
  - **Unaccounted (`match="all"`)**: Flows falling through to the default allow rule on any policy still in audit mode (`enableDefaultDeny: false`).

## 2. Reusable Scripts (`scripts/`)
Always prefer using the checked-in scripts rather than rewriting ad-hoc python scripts:

- **Audit Prometheus & Hubble Metrics**:
  ```bash
  # Check actively dropped flows (action="dropped") under default-deny enforcement
  ./scripts/cilium-netpol-audit.py dropped [-n <namespace>] [-W <workload>] [-d ingress|egress]

  # Check top unaccounted audit flows (match="all") cluster-wide, by namespace, or by workload
  ./scripts/cilium-netpol-audit.py unaccounted [-n <namespace>] [-W <workload>] [-d ingress|egress]

  # View live flow verdict rates broken down by match type (l3-l4, l3-only, l4-only, all, none)
  ./scripts/cilium-netpol-audit.py verdicts [-W <workload>] [-n <namespace>] [-m <match_type>]

  # Audit non-workload flows (CloudNativePG databases, CRDs, host/world) using -W unknown
  ./scripts/cilium-netpol-audit.py verdicts -W unknown -n security

  # Get accounted vs unaccounted vs dropped summary and coverage percentage
  ./scripts/cilium-netpol-audit.py summary [-n <namespace>] [-W <workload>]

  # Inspect required Prometheus scrape ports (partitioned by cluster pods vs host/node daemons)
  ./scripts/cilium-netpol-audit.py targets [-v]

  # Execute ad-hoc PromQL queries via resilient automatic port-forwarding
  ./scripts/cilium-netpol-audit.py query '<promql-query>' [--json]
  ```

- **Inspect Node-Level BPF Policy Table, Security Identities, and Live Hubble Flows**:
  ```bash
  # Maps pod to node, containers, probes, Service port->targetPort translations, Cilium agent, and BPF rules
  # (Supports exact pod name, name prefix, or CNPG cluster/label selector)
  ./scripts/cilium-endpoint-inspect.py <pod-name-or-selector> [-n <namespace>] [-p <port>] [--active-only]

  # Inspect numeric Cilium Security Identity labels (e.g. 1417) without kubectl exec
  ./scripts/cilium-endpoint-inspect.py --identity <identity-id>

  # Inspect live Hubble flows across an entire namespace without specifying a pod
  ./scripts/cilium-endpoint-inspect.py -n <namespace> -F [-p <port>] [--syn-only] [-c 30]

  # View live Hubble flows directly for a pod, filtering by port, SYN initiation, or fallthrough
  # (Automatically resolves Service ClusterIPs/VIPs, host nodes, LAN, and RevNAT return flows)
  ./scripts/cilium-endpoint-inspect.py <pod-name-or-selector> [-n <namespace>] -F [--syn-only] [--unmatched-only] -c 20

  # Filter flows by direction, verdict, or across pods/namespaces with automatic Cilium node agent discovery
  ./scripts/cilium-endpoint-inspect.py -F --from-pod default/wg-easy -V DROPPED
  ./scripts/cilium-endpoint-inspect.py -F --from-namespace ai --to-namespace network -c 50
  ```

## 3. Critical Cilium Implementation Rules
1. **Kubernetes Service `targetPort` vs `port` on Pod Ingress**:
   - Cilium evaluates pod ingress policies *after* Kubernetes Service DNAT has taken place.
   - If a Service maps `port: 80 -> targetPort: 10080` or `port: 443 -> targetPort: 10443`, an ingress policy with only ports `80` and `443` will fail to match incoming traffic, resulting in `match="all"` fallthrough.
   - Ingress policies must permit the container's actual listening `targetPorts` (e.g. `10080`, `10443`). Run `./scripts/cilium-endpoint-inspect.py <pod>` to see all container ports, probes, and Service translations.

2. **`kubectl apply` Map Merging Trap**:
   - Modifying `matchLabels` keys in a CNP (e.g. from `app.kubernetes.io/name` to `app`) will cause `kubectl apply` to merge the maps, keeping BOTH keys on the cluster.
   - **Always use `kubectl replace -f <manifest>`** or delete/recreate when changing label selector keys.

3. **Port Range Syntax**:
   - The CiliumNetworkPolicy v2 CRD rejects hyphenated ranges (`port: "6800-7300"`).
   - Use `port: "<start>"` (string) and `endPort: <end>` (integer):
     ```yaml
     toPorts:
       - ports:
           - port: "8080"
             endPort: 8082
             protocol: TCP
     ```

4. **Node/Host Scrape Egress**:
   - Pods scraping node-level daemons (kubelet `10250`, node-exporter `9100`, ceph `9926/9283`, cilium `9962-9965`, etcd `2381`) connect to node IPs (`192.168.1.x`).
   - Node IPs are classified under `host` and `remote-node`, **never** `cluster`. Scrape rules must include `toEntities: [cluster, host, remote-node]`.

5. **Always Specify `toPorts` on Scrape/Egress Rules**:
   - Writing `toEntities: [cluster]` without `toPorts` sets the rule to `port: 0, protocol: ANY`, which Hubble evaluates as fallback audit traffic (`match="all"`).
   - Explicit `toPorts` are required for L4 policy accounting (`match="l4-only"` or `match="l3-l4"`).

6. **DSR & Client Ephemeral Return Ports**:
   - With Cilium Direct Server Return (DSR), incoming replies from Service ClusterIPs or UDP DNS arrive on ephemeral client ports. Ingress policies receiving return traffic must allow L3 ingress without pinning destination ports.

7. **Same-Node Service RevNAT & Return Traffic on Ingress**:
   - When pod A connects to a Kubernetes Service whose backend pod B resides on the same node, tc-level RevNAT translates the return packet (`SYN, ACK`) to the Service IP on the local veth pair before conntrack state marks it as reply.
   - Cilium evaluates the packet against pod A's ingress policy with pod B's security identity. Without an ingress rule, it hits `match="all"` (audit fallthrough).
   - **Resolution in Pod A's `CiliumNetworkPolicy`**:
     1. Add `toServices` for the Service in `egress`.
     2. Add `fromEndpoints` matching pod B's labels (e.g. `cnpg.io/cluster: <cluster-name>`) in `ingress` to explicitly allow the return path without port restrictions.


8. **ICMP Rules Must Not Combine with `toPorts`**:
   - In CiliumNetworkPolicy, `icmps` rules must be placed in a separate rule entry from `toPorts`.
   - If `toPorts` and `icmps` are defined under the same egress/ingress list item, Cilium generates L4 port filters that do not match ICMP (which operates without TCP/UDP ports).
   - This causes ICMP EchoRequest (type 8) and EchoReply (type 0) to fall through to `match="all"` (audit fallthrough).
   - Separate them into two distinct list elements under `egress` / `ingress`:
     ```yaml
     egress:
       - toEntities: [world]
         toPorts:
           - ports: [{port: "443", protocol: TCP}]
       - toEntities: [world]
         icmps:
           - fields: [{type: 8, family: IPv4}]
     ```

9. **Cluster-Wide Policies (`CiliumClusterwideNetworkPolicy`) Must Exclude `reserved:health`**:
   - An empty `endpointSelector: {}` in a `CiliumClusterwideNetworkPolicy` matches **every** Cilium-managed endpoint on the node, including the synthetic `cilium-health` endpoint (`reserved:health`, Identity `4`).
   - If the CCNP contains an `ingress` rule, `reserved:health` enters ingress policy tracking and inter-node health probes (`TCP/4240` and `ICMPv4 EchoRequest`) show up in Hubble metrics as `/<unknown> -> /<unknown>` (`match="all"`).
   - Always scope pod-wide CCNPs to actual Kubernetes pods by requiring `io.kubernetes.pod.namespace` to exist:
     ```yaml
     endpointSelector:
       matchExpressions:
         - key: io.kubernetes.pod.namespace
           operator: Exists
     ```

10. **`kubectl port-forward` & Control-Plane Node Identity (`kube-apiserver`)**:
    - On control-plane nodes (`talos-cp-1/2/3`), the node identity combines `reserved:host` / `reserved:remote-node` with `reserved:kube-apiserver`.
    - When `kubectl port-forward` connects to a pod (such as `prometheus:9090`), or when kubelet/hubble-relay probes arrive from a control-plane node, rules that only list `fromEntities: [host, remote-node]` can drop traffic originating from `kube-apiserver`. Include `kube-apiserver` in `fromEntities: [host, remote-node, kube-apiserver]` for services accessed via `port-forward` or control-plane daemons.

11. **Do Not Pin Destination Ports on Client-Side `fromEndpoints` That Receive Same-Node Service RevNAT**:
    - If Pod A (e.g. `prometheus`, `silence-operator`, `gatus`, `envoy`, `cnpg-operator`) connects as a client to Pod B via a Kubernetes Service on the same node, the `SYN, ACK` return packet arrives at Pod A's **ephemeral client port** (`32768-60999`).
    - If Pod A's `ingress` rule for `fromEndpoints` (matching Pod B or Pod B's namespace) pins `toPorts` to Pod A's listening port (e.g. `9090/TCP`), the `SYN, ACK` return packet on the ephemeral port will be **DROPPED** in enforce mode. Omit `toPorts` on `fromEndpoints` rules that also cover same-node Service return traffic.
