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
    """Finds a pod either by name or label selector."""
    if label_selector:
        cmd = ["kubectl", "get", "pods", "-n", namespace, "-l", label_selector, "-o", "json"]
    else:
        if "/" in name_or_query:
            namespace, name_or_query = name_or_query.split("/", 1)
        cmd = ["kubectl", "get", "pod", "-n", namespace, name_or_query, "-o", "json"]

    raw = run_cmd(cmd)
    data = json.loads(raw)
    if "items" in data:
        if not data["items"]:
            raise RuntimeError(f"No pods found in namespace '{namespace}' matching selector '{label_selector}'")
        return data["items"][0]
    return data


def find_cilium_pod_on_node(node_name: str) -> str:
    """Finds the Cilium agent pod running on the specified node."""
    cmd = [
        "kubectl",
        "get",
        "pods",
        "-n",
        "kube-system",
        "-l",
        "k8s-app=cilium",
        "--field-selector",
        f"spec.nodeName={node_name}",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    ]
    pod_name = run_cmd(cmd)
    if not pod_name:
        raise RuntimeError(f"No Cilium agent pod found on node '{node_name}'")
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


def get_hubble_flows(
    cilium_pod: str,
    pod_full_name: str,
    last: int = 30,
    direction: str = "both",
    unmatched_only: bool = False,
    non_reply_only: bool = False,
) -> List[Dict[str, Any]]:
    """Retrieves recent Hubble flows for the pod via the node's Cilium agent."""
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
        "--pod",
        pod_full_name,
        "--last",
        str(last),
        "-o",
        "json",
    ]
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

            pmt = flow_data.get("policy_match_type", 0)
            if unmatched_only and pmt not in (0, 4):
                continue

            flows.append(flow_data)
        except Exception:
            continue
    return flows


def format_endpoint_summary(ep: Dict[str, Any]) -> str:
    """Formats source or destination endpoint representation."""
    if not ep:
        return "<unknown>"
    pod = ep.get("pod_name")
    ns = ep.get("namespace")
    if pod and ns:
        return f"{ns}/{pod}"
    elif pod:
        return pod
    labels = ep.get("labels", [])
    for l in labels:
        if l.startswith("reserved:"):
            return l
    identity = ep.get("identity")
    return f"id:{identity}" if identity else "<unknown>"


def print_flows_table(flows: List[Dict[str, Any]]) -> None:
    """Prints formatted table of Hubble flows."""
    if not flows:
        print("\nNo flows matched the filter criteria.")
        return

    print(f"\n{'TIME':<12} {'DIR':<7} {'VERDICT':<9} {'MATCH_TYPE':<12} {'SOURCE':<30} {'DESTINATION':<30} {'PORT':<10} {'SUMMARY'}")
    print("-" * 125)

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

        print(f"{short_time:<12} {direction:<7} {verdict:<9} {match_str:<12} {src:<30} {dst:<30} {port_str:<10} {summary}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect a pod's Cilium Endpoint ID, Security Identity, realized BPF policy rules, and live Hubble flows."
    )
    parser.add_argument("pod", nargs="?", help="Pod name or namespace/name")
    parser.add_argument("-n", "--namespace", default="default", help="Kubernetes namespace (default: default)")
    parser.add_argument("-l", "--selector", help="Label selector (e.g. app=grafana)")
    parser.add_argument("-p", "--port", help="Filter BPF rules by port (e.g. 80, 3000, 9100)")
    parser.add_argument(
        "-d", "--direction", choices=["ingress", "egress", "both"], default="both", help="Filter direction"
    )
    parser.add_argument(
        "--active-only", action="store_true", help="Only show BPF rules with matched packets > 0"
    )
    parser.add_argument("--json", action="store_true", help="Output raw endpoint JSON")

    # Hubble flows inspection options
    parser.add_argument("-F", "--flows", action="store_true", help="Inspect recent live Hubble flows for the pod")
    parser.add_argument("--last", type=int, default=30, help="Number of Hubble flows to inspect (default: 30)")
    parser.add_argument(
        "--unmatched-only", action="store_true", help="Filter Hubble flows for policy fallthrough (match='all' or none)"
    )
    parser.add_argument(
        "--non-reply-only", action="store_true", help="Filter out TCP reply packets to focus on initiated traffic"
    )

    args = parser.parse_args()

    if not args.pod and not args.selector:
        parser.error("Must provide either a pod name or a label selector (-l).")

    try:
        pod = find_pod(args.pod or "", namespace=args.namespace, label_selector=args.selector)
    except Exception as e:
        print(f"Error finding pod: {e}", file=sys.stderr)
        sys.exit(1)

    pod_name = pod["metadata"]["name"]
    pod_ns = pod["metadata"]["namespace"]
    pod_ip = pod.get("status", {}).get("podIP")
    node_name = pod.get("spec", {}).get("nodeName")
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
            f"{pod_ns}/{pod_name}",
            last=args.last,
            direction=args.direction,
            unmatched_only=args.unmatched_only,
            non_reply_only=args.non_reply_only,
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
