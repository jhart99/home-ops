# Cilium CNI & NetworkPolicy Auditing

This directory contains the Flux deployment for **Cilium** (`app/`) and its **Grafana dashboards** (`dashboard/`), including the **Cilium NetworkPolicy Audit** dashboard (`uid: cilium-netpol-audit`).

---

## Directory Structure

```text
kubernetes/apps/kube-system/cilium/
├── ks.yaml                              # Flux Kustomizations (cilium & cilium-dashboard)
├── app/
│   ├── helmrelease.yaml                 # Cilium HelmRelease (netkit, BGP, WireGuard, Hubble metrics)
│   ├── networkpolicies.yaml             # Cluster-wide DNS and Kubernetes API egress policies (CCNP)
│   ├── networks.yaml                    # BGP Peer/Advertisement & L2 LoadBalancer IP pools
│   ├── httproute.yaml                   # Hubble UI HTTPRoute
│   └── talospatcher.yaml                # Role/SA for Talos config patching
└── dashboard/
    ├── kustomization.yaml               # ConfigMap generator (substitution disabled)
    ├── grafanadashboard.yaml            # GrafanaDashboard CRs (cilium, operator, hubble, netpol-audit)
    └── cilium-netpol-audit.json         # Cilium NetworkPolicy Audit dashboard JSON
```

---

## How CiliumNetworkPolicy Audit Mode Works

All `CiliumNetworkPolicy` (CNP) resources in the cluster are deployed in **Audit Mode** (`enableDefaultDeny: { ingress: false, egress: false }`) before switching on enforcement.

### 1. Tier Compilation (`tier: 200` vs `tier: 255`)
When a pod is selected by one or more `CiliumNetworkPolicy` resources with `enableDefaultDeny: false`:
1. Cilium compiles all explicit allow rules from matching CNPs into the endpoint's BPF policy map at **`tier: 200`**.
2. Because `enableDefaultDeny` is `false`, Cilium appends a synthetic catch-all allow rule at **`tier: 255`** labeled:
   * Ingress: `reserved:io.cilium.policy.derived-from=allow-any-ingress`
   * Egress: `reserved:io.cilium.policy.derived-from=allow-any-egress`
3. Every new connection generates a Hubble `PolicyVerdict` event (`event_type.type == 5`):
   * **Accounted-for traffic** matches an explicit rule at `tier: 200` $\rightarrow$ Hubble records the CNP name in `ingress_allowed_by` / `egress_allowed_by` and sets `policy_match_type` to `1` (`l3-only`), `2` (`l3-l4`), or `3` (`l4-only`).
   * **Unaccounted-for traffic** misses all `tier: 200` rules and falls through to `allow-any-*` at `tier: 255` $\rightarrow$ Hubble sets `policy_match_type` to `4` (`match="all"`). **Traffic is still forwarded (`action="forwarded"`), not dropped.**

---

### 2. Why Every Namespace Must Include `components/deny-all`
If a pod is **not** selected by any `CiliumNetworkPolicy` in a given direction (for example, if a workload has no CNP yet, or its CNP has a typo in `endpointSelector`), Cilium leaves policy enforcement on that endpoint as `Disabled` (`policy-enabled: none`) and **emits zero `PolicyVerdict` events for it**.

To prevent unpolicied pods from hiding from Hubble/Prometheus during auditing, every namespace `kustomization.yaml` (`kubernetes/apps/<namespace>/kustomization.yaml`) includes `../../components/deny-all` ([`kubernetes/components/deny-all/default-deny.yaml`](../../../components/deny-all/default-deny.yaml)):

```yaml
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: default-deny-ingress
spec:
  enableDefaultDeny:
    ingress: false
    egress: false
  endpointSelector: {}
  ingress:
    - fromEndpoints:
        - matchLabels:
            io.cilium.policy/audit-noop: "true"
```

#### Important Cilium Gotcha: `ingress: [{}]` vs No-Op Selector
* In standard Kubernetes `NetworkPolicy` (`networking.k8s.io/v1`), `ingress: []` means deny-all and `ingress: [{}]` means allow-all.
* In Cilium's `CiliumNetworkPolicy` (`cilium.io/v2`):
  * `ingress: []` is ignored ("rule does not apply at ingress").
  * `ingress: [{}]` is an empty `IngressRule` with no `from*` or `toPorts` restriction — Cilium treats a rule with no `from*` clause as **allow from ALL sources on ALL ports at `tier: 200`**, which defeats both default-deny and audit fallback!
  * Using `fromEndpoints` / `toEndpoints` matching the non-existent label `io.cilium.policy/audit-noop: "true"` forces Cilium to enable policy evaluation (`policy-enabled: both`) on **every pod in the namespace** (`endpointSelector: {}`) while matching **zero** traffic at `tier: 200`. Unmatched traffic therefore cleanly falls through to `tier: 255` (`match="all"`) during auditing, and will be strictly denied when `enableDefaultDeny` is flipped to `true`.

---

## Monitoring Accounted vs. Unaccounted Traffic

### 1. Grafana Dashboard (`Cilium NetworkPolicy Audit`)
Open the **Cilium NetworkPolicy Audit** dashboard in Grafana (`uid: cilium-netpol-audit`).
* Filter by one or more namespaces using the `$namespace` dropdown.
* **Ingress / Egress Policy Coverage %**: Shows the ratio of `match=~"l3-l4|l3-only|l4-only"` verdicts to total `action="forwarded"` verdicts. A namespace at **100%** has zero unaccounted flows in the selected time window.
* **Unaccounted Ingress / Egress by Workload (`match="all"`)**: Tables listing the exact `(source_namespace, source_workload) -> (destination_namespace, destination_workload)` pairs that fell through to `allow-any`.

### 2. Prometheus Metrics (`hubble_policy_verdicts_total`)
Configured in [`app/helmrelease.yaml`](./app/helmrelease.yaml):
```yaml
hubble:
  metrics:
    enabled:
      - drop:labelsContext=source_namespace,source_workload,destination_namespace,destination_workload
      - policy:labelsContext=source_namespace,source_workload,destination_namespace,destination_workload,traffic_direction
```

Useful PromQL queries:
* **Unaccounted Ingress (`match="all"`)**:
  ```promql
  sum by (destination_namespace, destination_workload, source_namespace, source_workload) (
    increase(hubble_policy_verdicts_total{direction="ingress", match="all"}[1h])
  ) > 0
  ```
* **Unaccounted Egress (`match="all"`)**:
  ```promql
  sum by (source_namespace, source_workload, destination_namespace, source_workload, destination_workload) (
    increase(hubble_policy_verdicts_total{direction="egress", match="all"}[1h])
  ) > 0
  ```

---

### 3. Inspecting Exact L4 Ports via Hubble Relay CLI
Prometheus metrics intentionally omit L4 port labels to prevent cardinality explosion. When `hubble_policy_verdicts_total{match="all"}` shows an unaccounted workload pair, run this one-liner against `hubble-relay` to see the exact protocol and destination port:

```bash
# Fetch the last 2000 cluster-wide PolicyVerdict events from hubble-relay and list unaccounted flows with ports
RELAY_IP=$(kubectl -n kube-system get svc hubble-relay -o jsonpath='{.spec.clusterIP}')
kubectl -n kube-system exec ds/cilium -c cilium-agent -- \
  hubble observe --server "${RELAY_IP}:80" --type policy-verdict --last 2000 -o json | python3 -c '
import sys, json, collections
counts = collections.Counter()
for line in sys.stdin:
    if not line.strip(): continue
    f = json.loads(line).get("flow", {})
    l4 = f.get("l4", {})
    # Ignore TCP ACK return packets (SYN+ACK) and UDP DNS replies to ephemeral ports
    if "TCP" in l4 and l4["TCP"].get("flags", {}).get("ACK"): continue
    if "UDP" in l4 and f.get("traffic_direction") == "INGRESS" and l4["UDP"].get("source_port") == 53 and l4["UDP"].get("destination_port", 0) >= 32768: continue
    d = f.get("traffic_direction", "")
    allowed = f.get("ingress_allowed_by", []) if d == "INGRESS" else f.get("egress_allowed_by", [])
    labels = [l for e in allowed for l in e.get("labels", [])]
    if any("allow-any-" in l for l in labels):
        src = f.get("source", {})
        dst = f.get("destination", {})
        s_ns = src.get("namespace") or ",".join(l for l in src.get("labels", []) if l.startswith("reserved:"))
        s_wk = (src.get("workloads") or [{}])[0].get("name") or src.get("pod_name") or s_ns
        d_ns = dst.get("namespace") or ",".join(l for l in dst.get("labels", []) if l.startswith("reserved:"))
        d_wk = (dst.get("workloads") or [{}])[0].get("name") or dst.get("pod_name") or d_ns
        port = next((f"{p}/{l4[p].get(\"destination_port\", \"\")}" for p in ("TCP", "UDP", "ICMPv4", "SCTP") if p in l4), "")
        counts[(d, f"{s_ns}/{s_wk}", f"{d_ns}/{d_wk}", port)] += 1
for (d, s, dst, p), c in counts.most_common(40):
    print(f"{c:>5}x [{d:<7}] {s} -> {dst} ({p})")
'
```

---

### 4. Static Audit: Finding `CiliumNetworkPolicy` Selector Mismatches
To check if any `CiliumNetworkPolicy` in the cluster has an `endpointSelector` that matches **zero** running pods (e.g., label mismatch between chart defaults and CNP `matchLabels`):

```bash
kubectl get pods,cnp -A -o json | python3 -c '
import sys, json
data = json.load(sys.stdin)
pods = [i for i in data["items"] if i["kind"] == "Pod" and i["status"].get("phase") == "Running" and not i["spec"].get("hostNetwork")]
cnps = [i for i in data["items"] if i["kind"] == "CiliumNetworkPolicy"]

def match(pl, sel):
    for k, v in (sel or {}).get("matchLabels", {}).items():
        k = k.split(":", 1)[1] if ":" in k and k.split(":", 1)[0] in ("k8s", "any") else k
        if pl.get(k) != v: return False
    for e in (sel or {}).get("matchExpressions", []):
        k = e["key"]
        k = k.split(":", 1)[1] if ":" in k and k.split(":", 1)[0] in ("k8s", "any") else k
        op, vals = e["operator"], e.get("values", [])
        if (op == "In" and pl.get(k) not in vals) or (op == "NotIn" and pl.get(k) in vals) or (op == "Exists" and k not in pl) or (op == "DoesNotExist" and k in pl):
            return False
    return True

for c in cnps:
    ns, name, sel = c["metadata"]["namespace"], c["metadata"]["name"], c["spec"].get("endpointSelector", {})
    if not any(p["metadata"]["namespace"] == ns and match(p["metadata"].get("labels", {}), sel) for p in pods):
        print(f"[{ns}] {name} matches 0 running pods -> selector: {sel}")
'
```

---

## Enforcing Default-Deny

Once a namespace shows **100% coverage** (`0` unaccounted `match="all"` verdicts) in the **Cilium NetworkPolicy Audit** dashboard:
1. To enforce per-app or per-namespace: change `enableDefaultDeny.ingress` / `enableDefaultDeny.egress` from `false` to `true` in that namespace's `networkpolicies.yaml` (or in `components/deny-all/default-deny.yaml` when flipping cluster-wide).
2. Watch the **Enforced Policy Drops** panel on the `Cilium NetworkPolicy Audit` dashboard (or `hubble observe --verdict DROPPED`) to confirm no legitimate traffic is blocked.
