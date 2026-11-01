#!/usr/bin/env python3
"""Cilium Endpoint & BPF Policy Inspector CLI

Bridges Kubernetes Pods to their underlying Cilium Endpoint and BPF Policy
tables on the respective cluster node. Inspects realized L3/L4 rules,
port mappings, and live packet/byte counters.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple


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
        # Check if name is formatted as namespace/pod
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect a pod's Cilium Endpoint ID, Security Identity, and realized BPF policy rules."
    )
    parser.add_argument("pod", nargs="?", help="Pod name or namespace/name")
    parser.add_argument("-n", "--namespace", default="default", help="Kubernetes namespace (default: default)")
    parser.add_argument("-l", "--selector", help="Label selector (e.g. app=grafana)")
    parser.add_argument("-p", "--port", help="Filter BPF rules by port (e.g. 80, 3000, 9100)")
    parser.add_argument(
        "-d", "--direction", choices=["ingress", "egress", "both"], default="both", help="Filter BPF direction"
    )
    parser.add_argument(
        "--active-only", action="store_true", help="Only show rules with matched packets > 0"
    )
    parser.add_argument("--json", action="store_true", help="Output raw endpoint JSON")

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
    policy_status = ep.get("status", {}).get("policy", {})
    state = ep.get("status", {}).get("state")

    print(f"Cilium:    {cilium_pod} (Endpoint ID: {ep_id}, Security Identity: {sec_id}, State: {state})")

    if args.json:
        print("\n" + json.dumps(ep, indent=2))
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
            # Check PACKETS column
            # Header: POLICY DIRECTION LABELS PORT/PROTO PROXY AUTH BYTES PACKETS PREFIX COOKIE
            # If PACKETS is "-" or "0", skip
            tokens = [t for t in parts if t.isdigit()]
            if not tokens:
                continue
            # Usually bytes and packets are adjacent numbers
            if len(tokens) >= 2 and tokens[1] == "0":
                continue

        print(line)
        matching_count += 1

    if matching_count == 0:
        print("No BPF policy rules matched the specified filters.")


if __name__ == "__main__":
    main()
