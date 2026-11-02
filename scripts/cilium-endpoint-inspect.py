#!/usr/bin/env python3
"""Cilium Endpoint & BPF Policy Inspector CLI

Bridges Kubernetes Pods to their underlying Cilium Endpoint, BPF Policy
tables, and Hubble flow streams on the respective cluster node.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple


MATCH_TYPE_NAMES = {
    0: "NONE",
    1: "L3_ONLY",
    2: "L4_ONLY",
    3: "L3_L4",
    4: "ALL (audit)",
}


def run_cmd(cmd: List[str]) -> str:
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Command failed ({' '.join(cmd)}):\n{res.stderr.strip()}")
    return res.stdout.strip()


def find_pod(
    name_or_query: str, namespace: str = "default", label_selector: Optional[str] = None
) -> Dict[str, Any]:
    """Finds a pod by exact name, label selector, CNPG cluster, or name prefix."""
    if label_selector:
        cmd = ["kubectl", "get", "pods", "-n", namespace, "-l", label_selector, "-o", "json"]
        raw = run_cmd(cmd)
        data = json.loads(raw)
        if not data.get("items"):
            raise RuntimeError(f"No pods found in namespace '{namespace}' matching selector '{label_selector}'")
        return data["items"][0]

    if "/" in name_or_query:
        namespace, name_or_query = name_or_query.split("/", 1)

    # 1. Try exact pod name
    try:
        raw = run_cmd(["kubectl", "get", "pod", "-n", namespace, name_or_query, "-o", "json"])
        return json.loads(raw)
    except RuntimeError:
        pass

    # 2. Try common labels (CNPG cluster, Helm release, app name)
    selectors = [
        f"cnpg.io/cluster={name_or_query}",
        f"app.kubernetes.io/name={name_or_query}",
        f"app.kubernetes.io/instance={name_or_query}",
        f"app={name_or_query}",
    ]
    for sel in selectors:
        try:
            raw = run_cmd(["kubectl", "get", "pods", "-n", namespace, "-l", sel, "-o", "json"])
            data = json.loads(raw)
            if data.get("items"):
                return data["items"][0]
        except RuntimeError:
            pass

    # 3. Try name prefix matching (e.g. authentik-server-* or authentik-db-*)
    try:
        raw = run_cmd(["kubectl", "get", "pods", "-n", namespace, "-o", "json"])
        data = json.loads(raw)
        for item in data.get("items", []):
            pname = item.get("metadata", {}).get("name", "")
            if pname.startswith(f"{name_or_query}-") or pname.startswith(name_or_query):
                return item
    except RuntimeError:
        pass

    raise RuntimeError(f"No pods found in namespace '{namespace}' matching '{name_or_query}' (exact name, prefix, or label)")


def find_cilium_pod_on_node(node_name: Optional[str] = None) -> str:
    """Finds the Cilium agent pod running on the specified node, or any running agent."""
    cmd = [
        "kubectl",
        "get",
        "pods",
        "-n",
        "kube-system",
        "-l",
        "k8s-app=cilium",
    ]
    if node_name:
        cmd.extend(["--field-selector", f"spec.nodeName={node_name}"])
    else:
        cmd.extend(["--field-selector", "status.phase=Running"])
    cmd.extend(["-o", "jsonpath={.items[0].metadata.name}"])
    pod_name = run_cmd(cmd)
    if not pod_name:
        msg = f"No Cilium agent pod found on node '{node_name}'" if node_name else "No running Cilium agent pod found"
        raise RuntimeError(msg)
    return pod_name


def get_cilium_endpoint_by_ip(cilium_pod: str, pod_ip: str) -> Dict[str, Any]:
    """Retrieves endpoint details from the Cilium agent by pod IP."""
    cmd = [
        "kubectl",
        "exec",
        "-n",
        "kube-system",
        cilium_pod,
        "-c",
        "cilium-agent",
        "--",
        "cilium-dbg",
        "endpoint",
        "list",
        "-o",
        "json",
    ]
    raw = run_cmd(cmd)
    endpoints = json.loads(raw)
    for ep in endpoints:
        addr_list = ep.get("status", {}).get("networking", {}).get("addressing", [])
        for addr in addr_list:
            if addr.get("ipv4") == pod_ip:
                return ep

    raise RuntimeError(f"Endpoint with IP '{pod_ip}' not found in Cilium agent '{cilium_pod}'")


def get_bpf_policy(cilium_pod: str, endpoint_id: int) -> List[str]:
    """Retrieves the BPF policy table for the given endpoint ID."""
    cmd = [
        "kubectl",
        "exec",
        "-n",
        "kube-system",
        cilium_pod,
        "-c",
        "cilium-agent",
        "--",
        "cilium-dbg",
        "bpf",
        "policy",
        "get",
        str(endpoint_id),
    ]
    raw = run_cmd(cmd)
    return raw.splitlines()


def inspect_identity(identity_id: int, cilium_pod: str) -> None:
    """Inspects a numeric Cilium Security Identity and displays its labels."""
    cmd = [
        "kubectl",
        "exec",
        "-n",
        "kube-system",
        cilium_pod,
        "-c",
        "cilium-agent",
        "--",
        "cilium-dbg",
        "identity",
        "get",
        str(identity_id),
        "-o",
        "json",
    ]
    try:
        raw = run_cmd(cmd)
        data = json.loads(raw)
    except Exception as e:
        print(f"Error querying identity {identity_id}: {e}", file=sys.stderr)
        sys.exit(1)

    if not data:
        print(f"Identity {identity_id} not found.")
        return

    item = data[0] if isinstance(data, list) else data
    labels = item.get("labels", [])
    lbl_ns, lbl_name, kind = extract_endpoint_labels_info(labels)

    print(f"Security Identity: {identity_id}")
    if lbl_ns:
        print(f"Namespace:         {lbl_ns}")
    if lbl_name:
        workload_str = f"{lbl_name} ({kind})" if kind else lbl_name
        print(f"Workload/App:      {workload_str}")
    print(f"Labels ({len(labels)}):")
    for l in sorted(labels):
        print(f"  - {l}")


def get_hubble_flows(
    cilium_pod: str,
    pod_full_name: Optional[str] = None,
    namespace: Optional[str] = None,
    last: int = 30,
    direction: str = "both",
    port: Optional[str] = None,
    unmatched_only: bool = False,
    non_reply_only: bool = False,
    syn_only: bool = False,
    dropped_only: bool = False,
) -> List[Dict[str, Any]]:
    """Retrieves recent Hubble flows for a pod or namespace via the node's Cilium agent."""
    cmd = [
        "kubectl",
        "exec",
        "-n",
        "kube-system",
        cilium_pod,
        "-c",
        "cilium-agent",
        "--",
        "hubble",
        "observe",
        "--last",
        str(last),
        "-o",
        "json",
    ]
    if pod_full_name:
        cmd.extend(["--pod", pod_full_name])
    elif namespace:
        cmd.extend(["-n", namespace])

    if port:
        cmd.extend(["--port", str(port)])

    if dropped_only:
        cmd.extend(["--verdict", "DROPPED"])

    raw = run_cmd(cmd)
    flows = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            flow_data = json.loads(line).get("flow", {})
            if not flow_data:
                continue

            td = flow_data.get("traffic_direction", "").lower()
            if direction != "both" and td != direction:
                continue

            is_reply = flow_data.get("is_reply", False)
            if non_reply_only and is_reply:
                continue

            l4 = flow_data.get("l4", {})
            if syn_only:
                tcp_flags = l4.get("TCP", {}).get("flags", {})
                is_syn = tcp_flags.get("SYN", False)
                is_ack = tcp_flags.get("ACK", False)
                if not (is_syn and not is_ack):
                    continue

            pmt = flow_data.get("policy_match_type", 0)
            if unmatched_only and pmt not in (0, 4):
                continue

            flows.append(flow_data)
        except Exception:
            continue
    return flows


def extract_endpoint_labels_info(labels: List[str]) -> Tuple[str, str, str]:
    """Extracts (namespace, workload_or_instance, kind) from endpoint labels list."""
    ns = ""
    name = ""
    kind = ""
    for l in labels:
        if l.startswith("k8s:io.kubernetes.pod.namespace="):
            ns = l.split("=", 1)[1]
        elif l.startswith("k8s:cnpg.io/instanceName="):
            name = l.split("=", 1)[1]
            kind = "cnpg"
        elif not name and l.startswith("k8s:cnpg.io/cluster="):
            name = l.split("=", 1)[1]
            kind = "cnpg"
        elif not name and l.startswith("k8s:app.kubernetes.io/instance="):
            name = l.split("=", 1)[1]
        elif not name and l.startswith("k8s:app.kubernetes.io/name="):
            name = l.split("=", 1)[1]
        elif not name and l.startswith("k8s:app="):
            name = l.split("=", 1)[1]
    return (ns, name, kind)


def format_endpoint_summary(ep: Dict[str, Any]) -> str:
    """Formats source or destination endpoint representation, resolving non-workload labels if pod_name is missing."""
    if not ep:
        return "<unknown>"
    pod = ep.get("pod_name")
    ns = ep.get("namespace")
    labels = ep.get("labels", [])
    identity = ep.get("identity")

    if pod and ns:
        return f"{ns}/{pod}"
    elif pod:
        return pod

    lbl_ns, lbl_name, kind = extract_endpoint_labels_info(labels)
    eff_ns = ns or lbl_ns
    if eff_ns and lbl_name:
        extra = f" ({kind})" if kind else ""
        return f"{eff_ns}/{lbl_name}{extra}"
    elif lbl_name:
        return lbl_name

    for l in labels:
        if l.startswith("reserved:"):
            return l

    return f"id:{identity}" if identity else "<unknown>"


def print_flows_table(flows: List[Dict[str, Any]]) -> None:
    """Prints formatted table of Hubble flows."""
    if not flows:
        print("\nNo flows matched the filter criteria.")
        return

    print(f"\n{'TIME':<12} {'DIR':<7} {'VERDICT':<9} {'MATCH_TYPE':<12} {'SOURCE':<34} {'DESTINATION':<34} {'PORT':<10} {'SUMMARY'}")
    print("-" * 133)

    for f in flows:
        raw_time = f.get("time", "")
        short_time = raw_time[11:19] if len(raw_time) >= 19 else raw_time
        direction = f.get("traffic_direction", "")[:6]
        verdict = f.get("verdict", "")
        pmt = f.get("policy_match_type", 0)
        match_str = MATCH_TYPE_NAMES.get(pmt, str(pmt))
        src = format_endpoint_summary(f.get("source", {}))
        dst = format_endpoint_summary(f.get("destination", {}))

        l4 = f.get("l4", {})
        port = ""
        proto = ""
        if "TCP" in l4:
            proto = "TCP"
            port = str(l4["TCP"].get("destination_port", ""))
        elif "UDP" in l4:
            proto = "UDP"
            port = str(l4["UDP"].get("destination_port", ""))
        elif "ICMPv4" in l4 or "ICMPv6" in l4:
            proto = "ICMP"

        port_str = f"{port}/{proto}" if port else proto
        summary = f.get("Summary", "")

        # Detect same-node Service RevNAT return edge-case:
        is_reply = f.get("is_reply", False)
        if direction.startswith("INGRES") and not is_reply:
            flags = l4.get("TCP", {}).get("flags", {})
            if flags.get("SYN") and flags.get("ACK"):
                summary += " [RevNAT return]"

        print(f"{short_time:<12} {direction:<7} {verdict:<9} {match_str:<12} {src:<34} {dst:<34} {port_str:<10} {summary}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect a pod's Cilium Endpoint ID, Security Identity, realized BPF policy rules, and live Hubble flows."
    )
    parser.add_argument("pod", nargs="?", help="Pod name, namespace/name, or label prefix")
    parser.add_argument("-n", "--namespace", default="default", help="Kubernetes namespace (default: default)")
    parser.add_argument("-l", "--selector", help="Label selector (e.g. app=grafana)")
    parser.add_argument("-p", "--port", help="Filter BPF rules and Hubble flows by port (e.g. 80, 3000, 5140)")
    parser.add_argument(
        "-d", "--direction", choices=["ingress", "egress", "both"], default="both", help="Filter direction"
    )
    parser.add_argument(
        "--active-only", action="store_true", help="Only show BPF rules with matched packets > 0"
    )
    parser.add_argument("--json", action="store_true", help="Output raw endpoint JSON")
    parser.add_argument("--node", help="Target specific node Cilium agent directly")
    parser.add_argument("--identity", type=int, help="Inspect a numeric Cilium Security Identity ID and its labels")

    # Hubble flows inspection options
    parser.add_argument("-F", "--flows", action="store_true", help="Inspect recent live Hubble flows")
    parser.add_argument("--last", type=int, default=30, help="Number of Hubble flows to inspect (default: 30)")
    parser.add_argument(
        "--unmatched-only", action="store_true", help="Filter Hubble flows for policy fallthrough (match='all' or none)"
    )
    parser.add_argument(
        "--non-reply-only", action="store_true", help="Filter out TCP reply packets to focus on initiated traffic"
    )
    parser.add_argument(
        "--syn-only", action="store_true", help="Filter Hubble flows for newly initiated TCP connections (SYN without ACK)"
    )
    parser.add_argument(
        "--dropped-only", action="store_true", help="Filter Hubble flows for dropped packets (verdict=DROPPED)"
    )

    args = parser.parse_args()

    # 1. Identity lookup mode
    if args.identity:
        cilium_pod = find_cilium_pod_on_node(args.node)
        inspect_identity(args.identity, cilium_pod)
        return

    # 2. Namespace-wide flow inspection mode (no pod specified)
    if args.flows and not args.pod and not args.selector:
        cilium_pod = find_cilium_pod_on_node(args.node)
        print(f"Querying Hubble flows for namespace '{args.namespace}' (Cilium agent: {cilium_pod})...")
        flows = get_hubble_flows(
            cilium_pod,
            namespace=args.namespace,
            last=args.last,
            direction=args.direction,
            port=args.port,
            unmatched_only=args.unmatched_only,
            non_reply_only=args.non_reply_only,
            syn_only=args.syn_only,
            dropped_only=args.dropped_only,
        )
        print_flows_table(flows)
        return

    if not args.pod and not args.selector:
        parser.error("Must provide a pod name, label selector (-l), --identity, or -n <ns> -F.")

    try:
        pod = find_pod(args.pod or "", namespace=args.namespace, label_selector=args.selector)
    except Exception as e:
        print(f"Error finding pod: {e}", file=sys.stderr)
        sys.exit(1)

    pod_name = pod["metadata"]["name"]
    pod_ns = pod["metadata"]["namespace"]
    pod_ip = pod.get("status", {}).get("podIP")
    node_name = args.node or pod.get("spec", {}).get("nodeName")
    pod_labels = pod.get("metadata", {}).get("labels", {})

    if not pod_ip:
        print(f"Error: Pod {pod_ns}/{pod_name} does not have an assigned IP address yet.", file=sys.stderr)
        sys.exit(1)

    print(f"Pod:       {pod_ns}/{pod_name}")
    print(f"Node:      {node_name}")
    print(f"IP:        {pod_ip}")
    print(f"Labels:    {json.dumps(pod_labels)}")

    try:
        cilium_pod = find_cilium_pod_on_node(node_name)
        ep = get_cilium_endpoint_by_ip(cilium_pod, pod_ip)
    except Exception as e:
        print(f"Error querying Cilium agent: {e}", file=sys.stderr)
        sys.exit(1)

    ep_id = ep.get("id")
    sec_id = ep.get("status", {}).get("identity", {}).get("id")
    state = ep.get("status", {}).get("state")

    print(f"Cilium:    {cilium_pod} (Endpoint ID: {ep_id}, Security Identity: {sec_id}, State: {state})")

    if args.json:
        print("\n" + json.dumps(ep, indent=2))
        return

    # If --flows is requested, run Hubble flow inspection
    if args.flows:
        flows = get_hubble_flows(
            cilium_pod,
            pod_full_name=f"{pod_ns}/{pod_name}",
            last=args.last,
            direction=args.direction,
            port=args.port,
            unmatched_only=args.unmatched_only,
            non_reply_only=args.non_reply_only,
            syn_only=args.syn_only,
            dropped_only=args.dropped_only,
        )
        print_flows_table(flows)
        return

    try:
        bpf_lines = get_bpf_policy(cilium_pod, ep_id)
    except Exception as e:
        print(f"Error fetching BPF policy table: {e}", file=sys.stderr)
        sys.exit(1)

    if not bpf_lines:
        print("\nBPF policy table is empty.")
        return

    header = bpf_lines[0]
    print(f"\n{header}")
    print("-" * len(header))

    matching_count = 0
    for line in bpf_lines[1:]:
        if not line.strip():
            continue

        parts = line.split()
        if len(parts) >= 2:
            direction = parts[1].lower()
            if args.direction != "both" and direction != args.direction:
                continue

        if args.port and args.port not in line:
            continue

        if args.active_only:
            tokens = [t for t in parts if t.isdigit()]
            if not tokens:
                continue
            if len(tokens) >= 2 and tokens[1] == "0":
                continue

        print(line)
        matching_count += 1

    if matching_count == 0:
        print("No BPF policy rules matched the specified filters.")


if __name__ == "__main__":
    main()
