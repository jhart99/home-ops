#!/usr/bin/env python3
"""Cilium NetworkPolicy Audit CLI

Inspects Hubble and Prometheus metrics to find unaccounted network flows
(flows that match="all" and would be dropped when enforcing default-deny),
reports policy coverage percentages, inspects Prometheus scrape targets,
and monitors live policy verdicts.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Set, Tuple


DEFAULT_PROMETHEUS_SVC = "svc/kube-prometheus-stack-prometheus"
DEFAULT_PROMETHEUS_NS = "observability"
DEFAULT_PROMETHEUS_PORT = 9090


class PrometheusClient:
    """Manages queries to Prometheus, auto-forwarding a port if necessary."""

    def __init__(self, base_url: Optional[str] = None):
        self.port_forward_proc: Optional[subprocess.Popen] = None
        self.base_url = base_url or os.getenv("PROMETHEUS_URL")

        if not self.base_url:
            self.base_url = self._ensure_port_forward()

    def _is_port_open(self, host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0

    def _ensure_port_forward(self) -> str:
        # Check if 9090 is already answering
        if self._is_port_open("127.0.0.1", DEFAULT_PROMETHEUS_PORT):
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{DEFAULT_PROMETHEUS_PORT}/-/ready", timeout=1
                )
                return f"http://127.0.0.1:{DEFAULT_PROMETHEUS_PORT}"
            except Exception:
                pass

        # Spawn kubectl port-forward
        cmd = [
            "kubectl",
            "port-forward",
            "-n",
            DEFAULT_PROMETHEUS_NS,
            DEFAULT_PROMETHEUS_SVC,
            f"{DEFAULT_PROMETHEUS_PORT}:{DEFAULT_PROMETHEUS_PORT}",
        ]
        self.port_forward_proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        atexit.register(self.cleanup)

        # Wait up to 15s for the port to open
        for _ in range(150):
            if self._is_port_open("127.0.0.1", DEFAULT_PROMETHEUS_PORT):
                time.sleep(0.3)
                return f"http://127.0.0.1:{DEFAULT_PROMETHEUS_PORT}"
            if self.port_forward_proc.poll() is not None:
                break
            time.sleep(0.1)

        raise RuntimeError(
            f"Failed to establish port-forward to {DEFAULT_PROMETHEUS_SVC} on port {DEFAULT_PROMETHEUS_PORT}"
        )

    def cleanup(self) -> None:
        if self.port_forward_proc and self.port_forward_proc.poll() is None:
            self.port_forward_proc.terminate()
            try:
                self.port_forward_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.port_forward_proc.kill()
            self.port_forward_proc = None

    def query(self, promql: str) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/api/v1/query?query=" + urllib.parse.quote(promql)
        req = urllib.request.Request(url, headers={"User-Agent": "cilium-netpol-audit"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") != "success":
                raise RuntimeError(f"Prometheus query failed: {data}")
            return data.get("data", {}).get("result", [])

    def get_targets(self) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/api/v1/targets"
        req = urllib.request.Request(url, headers={"User-Agent": "cilium-netpol-audit"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") != "success":
                raise RuntimeError(f"Failed to fetch targets: {data}")
            return data.get("data", {}).get("activeTargets", [])


def _build_filter_queries(
    metric: str,
    filters: List[str],
    window: str,
    namespace: Optional[str] = None,
    workload: Optional[str] = None,
    group_by: str = "source_namespace, source_workload, destination_namespace, destination_workload, direction, match",
) -> str:
    """Builds PromQL queries matching namespace or workload across source or destination."""
    conds = []
    is_unknown_query = workload in ("unknown", "<unknown>", "none") if workload else False

    if namespace and workload:
        if is_unknown_query:
            conds.append(f'source_namespace="{namespace}", source_workload=""')
            conds.append(f'destination_namespace="{namespace}", destination_workload=""')
        else:
            conds.append(f'source_namespace="{namespace}", source_workload=~".*{workload}.*"')
            conds.append(f'destination_namespace="{namespace}", destination_workload=~".*{workload}.*"')
    elif namespace:
        conds.append(f'source_namespace="{namespace}"')
        conds.append(f'destination_namespace="{namespace}"')
    elif workload:
        if is_unknown_query:
            conds.append('source_workload=""')
            conds.append('destination_workload=""')
        else:
            conds.append(f'source_workload=~".*{workload}.*"')
            conds.append(f'destination_workload=~".*{workload}.*"')

    base_filter = ", ".join(filters) if filters else ""

    if conds:
        queries = []
        for c in conds:
            full = f"{base_filter}, {c}" if base_filter else c
            queries.append(f"sum by ({group_by}) (rate({metric}{{{full}}}[{window}])) > 0")
        return " or ".join(queries)
    else:
        full = f"{{{base_filter}}}" if base_filter else ""
        return f"sum by ({group_by}) (rate({metric}{full}[{window}])) > 0"


def cmd_unaccounted(args: argparse.Namespace, client: PrometheusClient) -> None:
    """List unaccounted flows (match='all') that will be dropped under default-deny."""
    filters = ['match="all"']
    if args.direction and args.direction != "both":
        filters.append(f'direction="{args.direction}"')

    promql = _build_filter_queries(
        "hubble_policy_verdicts_total",
        filters=filters,
        window=args.window,
        namespace=args.namespace,
        workload=args.workload,
        group_by="source_namespace, source_workload, destination_namespace, destination_workload, direction",
    )

    results = client.query(promql)

    rows: List[Tuple[float, str, str, str]] = []
    for r in results:
        rate_val = float(r["value"][1])
        m = r["metric"]
        src = f"{m.get('source_namespace') or ''}/{m.get('source_workload') or '<unknown>'}"
        dst = f"{m.get('destination_namespace') or ''}/{m.get('destination_workload') or '<unknown>'}"
        direction = m.get("direction", "unknown")
        rows.append((rate_val, direction, src, dst))

    rows.sort(key=lambda x: x[0], reverse=True)

    filter_desc = []
    if args.namespace:
        filter_desc.append(f"namespace='{args.namespace}'")
    if args.workload:
        filter_desc.append(f"workload='{args.workload}'")
    target_str = f" for {', '.join(filter_desc)}" if filter_desc else " cluster-wide"

    if not rows:
        print(f"🎉 No unaccounted flows (match='all') observed{target_str} in the last {args.window}!")
        return

    print(f"\nUnaccounted Flows (match='all') over window [{args.window}]{target_str}")
    print(f"{'RATE (flows/s)':<16} {'DIRECTION':<12} {'SOURCE':<38} {'DESTINATION':<38}")
    print("-" * 106)

    limit = args.top if args.top > 0 else len(rows)
    for rate_val, direction, src, dst in rows[:limit]:
        print(f"{rate_val:<16.3f} {direction:<12} {src:<38} {dst:<38}")

    if len(rows) > limit:
        print(f"\n... and {len(rows) - limit} more. Use --top 0 to show all.")

    if any("<unknown>" in src or "<unknown>" in dst for _, _, src, dst in rows[:limit]):
        print("\n💡 Tip: '<unknown>' indicates non-workload pods (e.g. CNPG databases, CRDs) or host/external traffic.")
        print("   Run: ./scripts/cilium-endpoint-inspect.py <pod> -F --unmatched-only to inspect exact IPs, labels, and flags.")


def cmd_verdicts(args: argparse.Namespace, client: PrometheusClient) -> None:
    """Display all active traffic verdicts (l3-l4, l3-only, l4-only, all) broken down by workload."""
    filters = []
    if args.direction and args.direction != "both":
        filters.append(f'direction="{args.direction}"')
    if args.match and args.match != "any":
        filters.append(f'match="{args.match}"')

    promql = _build_filter_queries(
        "hubble_policy_verdicts_total",
        filters=filters,
        window=args.window,
        namespace=args.namespace,
        workload=args.workload,
        group_by="source_namespace, source_workload, destination_namespace, destination_workload, direction, match",
    )

    results = client.query(promql)

    rows: List[Tuple[float, str, str, str, str]] = []
    for r in results:
        rate_val = float(r["value"][1])
        m = r["metric"]
        src = f"{m.get('source_namespace') or ''}/{m.get('source_workload') or '<unknown>'}"
        dst = f"{m.get('destination_namespace') or ''}/{m.get('destination_workload') or '<unknown>'}"
        direction = m.get("direction", "unknown")
        match = m.get("match", "unknown")
        rows.append((rate_val, direction, match, src, dst))

    rows.sort(key=lambda x: x[0], reverse=True)

    filter_desc = []
    if args.namespace:
        filter_desc.append(f"namespace='{args.namespace}'")
    if args.workload:
        filter_desc.append(f"workload='{args.workload}'")
    target_str = f" for {', '.join(filter_desc)}" if filter_desc else " cluster-wide"

    if not rows:
        print(f"No traffic verdicts observed{target_str} in the last {args.window}.")
        return

    print(f"\nActive Traffic Verdicts over window [{args.window}]{target_str}")
    print(f"{'RATE (flows/s)':<16} {'DIRECTION':<12} {'MATCH':<10} {'SOURCE':<36} {'DESTINATION':<36}")
    print("-" * 112)

    limit = args.top if args.top > 0 else len(rows)
    for rate_val, direction, match, src, dst in rows[:limit]:
        print(f"{rate_val:<16.3f} {direction:<12} {match:<10} {src:<36} {dst:<36}")

    if len(rows) > limit:
        print(f"\n... and {len(rows) - limit} more. Use --top 0 to show all.")

    if any("<unknown>" in src or "<unknown>" in dst for _, _, _, src, dst in rows[:limit]):
        print("\n💡 Tip: '<unknown>' indicates non-workload pods (e.g. CNPG databases, CRDs) or host/external traffic.")
        print("   Run: ./scripts/cilium-endpoint-inspect.py <pod> -F to inspect exact IPs, labels, and flags.")


def cmd_summary(args: argparse.Namespace, client: PrometheusClient) -> None:
    """Print accounted vs unaccounted summary and coverage breakdown."""
    def build_summary_query(direction: str) -> str:
        filters = [f'direction="{direction}"']
        is_unknown_query = args.workload in ("unknown", "<unknown>", "none") if args.workload else False
        if direction == "ingress":
            if args.namespace:
                filters.append(f'destination_namespace="{args.namespace}"')
            if args.workload:
                if is_unknown_query:
                    filters.append('destination_workload=""')
                else:
                    filters.append(f'destination_workload=~".*{args.workload}.*"')
        else:
            if args.namespace:
                filters.append(f'source_namespace="{args.namespace}"')
            if args.workload:
                if is_unknown_query:
                    filters.append('source_workload=""')
                else:
                    filters.append(f'source_workload=~".*{args.workload}.*"')
        f_str = ", ".join(filters)
        return f"sum by (match) (rate(hubble_policy_verdicts_total{{{f_str}}}[{args.window}]))"

    ingress_results = client.query(build_summary_query("ingress"))
    egress_results = client.query(build_summary_query("egress"))

    def parse_matches(results: List[Dict[str, Any]]) -> Dict[str, float]:
        matches = {"l3-l4": 0.0, "l3-only": 0.0, "l4-only": 0.0, "all": 0.0, "none": 0.0}
        for r in results:
            m = r["metric"].get("match", "unknown")
            v = float(r["value"][1])
            matches[m] = matches.get(m, 0.0) + v
        return matches

    in_m = parse_matches(ingress_results)
    eg_m = parse_matches(egress_results)

    def print_section(title: str, m: Dict[str, float]) -> None:
        accounted = m["l3-l4"] + m["l3-only"] + m["l4-only"]
        unaccounted = m["all"]
        total = accounted + unaccounted
        pct = (accounted / total * 100) if total > 0 else 100.0

        print(f"\n=== {title} ===")
        print(f"Total Rate:       {total:.3f} flows/sec")
        print(f"Coverage:         {pct:.1f}% accounted for")
        print(f"  - l3-l4:        {m['l3-l4']:<8.3f} flows/sec")
        print(f"  - l3-only:      {m['l3-only']:<8.3f} flows/sec")
        print(f"  - l4-only:      {m['l4-only']:<8.3f} flows/sec")
        print(f"  - all (audit):  {m['all']:<8.3f} flows/sec  <-- UNACCOUNTED (drop risk)")

    scope_parts = []
    if args.namespace:
        scope_parts.append(f"Namespace: {args.namespace}")
    if args.workload:
        scope_parts.append(f"Workload: {args.workload}")
    scope = ", ".join(scope_parts) if scope_parts else "Cluster-wide"

    print(f"Cilium Policy Verdict Summary ({scope}, window: {args.window})")
    print_section("INGRESS", in_m)
    print_section("EGRESS", eg_m)


def cmd_targets(args: argparse.Namespace, client: PrometheusClient) -> None:
    """Analyze Prometheus scrape targets by port, job, and host vs cluster."""
    targets = client.get_targets()
    print(f"Found {len(targets)} active Prometheus scrape targets.\n")

    ports_cluster: Set[str] = set()
    ports_node: Set[str] = set()
    job_map: Dict[str, List[str]] = {}

    for t in targets:
        job = t.get("labels", {}).get("job", "unknown")
        addr = t.get("discoveredLabels", {}).get("__address__", "")
        job_map.setdefault(job, []).append(addr)

        if ":" in addr:
            host_part, port_part = addr.rsplit(":", 1)
            if host_part.startswith("192.168."):
                ports_node.add(port_part)
            else:
                ports_cluster.add(port_part)

    def sort_ports(ports: Set[str]) -> List[str]:
        return sorted(list(ports), key=lambda x: int(x) if x.isdigit() else 999999)

    print("Scrape Ports Required for Cluster Pods (toEntities: [cluster]):")
    print("  " + ", ".join(sort_ports(ports_cluster)))

    print("\nScrape Ports Required for Node / Host Daemons (toEntities: [host, remote-node]):")
    print("  " + ", ".join(sort_ports(ports_node)))

    all_ports = ports_cluster.union(ports_node)
    print("\nCombined Scrape Ports Set:")
    print("  " + ", ".join(sort_ports(all_ports)))

    if args.verbose:
        print(f"\nBreakdown by Job ({len(job_map)} jobs):")
        for job, addrs in sorted(job_map.items()):
            sample = addrs[:3]
            suffix = f" ... and {len(addrs)-3} more" if len(addrs) > 3 else ""
            print(f"  {job:<40} ({len(addrs)} targets): {', '.join(sample)}{suffix}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit Cilium Network Policies against live traffic using Prometheus & Hubble metrics."
    )
    parser.add_argument(
        "--prometheus-url",
        help="Prometheus base URL (defaults to auto port-forwarding svc/kube-prometheus-stack-prometheus)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: unaccounted
    p_unacc = subparsers.add_parser("unaccounted", help="List flows with match='all'")
    p_unacc.add_argument("-n", "--namespace", help="Filter by namespace")
    p_unacc.add_argument("-W", "--workload", help="Filter by workload name (regex/substring)")
    p_unacc.add_argument(
        "-d", "--direction", choices=["ingress", "egress", "both"], default="both", help="Traffic direction"
    )
    p_unacc.add_argument(
        "-w", "--window", default="5m", help="PromQL rate interval window (e.g. 1m, 5m, 15m)"
    )
    p_unacc.add_argument("--top", type=int, default=25, help="Number of flows to show (0 for all)")

    # Subcommand: verdicts
    p_verd = subparsers.add_parser("verdicts", help="Display all active traffic verdicts (l3-l4, l3-only, l4-only, all)")
    p_verd.add_argument("-n", "--namespace", help="Filter by namespace")
    p_verd.add_argument("-W", "--workload", help="Filter by workload name (regex/substring)")
    p_verd.add_argument(
        "-d", "--direction", choices=["ingress", "egress", "both"], default="both", help="Traffic direction"
    )
    p_verd.add_argument(
        "-m", "--match", choices=["all", "l3-l4", "l3-only", "l4-only", "any"], default="any", help="Filter match type"
    )
    p_verd.add_argument(
        "-w", "--window", default="1m", help="PromQL rate interval window (e.g. 1m, 5m)"
    )
    p_verd.add_argument("--top", type=int, default=25, help="Number of flows to show (0 for all)")

    # Subcommand: summary
    p_sum = subparsers.add_parser("summary", help="Print accounted vs unaccounted coverage breakdown")
    p_sum.add_argument("-n", "--namespace", help="Filter by namespace")
    p_sum.add_argument("-W", "--workload", help="Filter by workload name (regex/substring)")
    p_sum.add_argument(
        "-w", "--window", default="5m", help="PromQL rate interval window (e.g. 1m, 5m, 15m)"
    )

    # Subcommand: targets
    p_targets = subparsers.add_parser("targets", help="Inspect active Prometheus scrape targets and ports")
    p_targets.add_argument("-v", "--verbose", action="store_true", help="Show all jobs and endpoints")

    args = parser.parse_args()

    client = PrometheusClient(base_url=args.prometheus_url)
    try:
        if args.command == "unaccounted":
            cmd_unaccounted(args, client)
        elif args.command == "verdicts":
            cmd_verdicts(args, client)
        elif args.command == "summary":
            cmd_summary(args, client)
        elif args.command == "targets":
            cmd_targets(args, client)
    finally:
        client.cleanup()


if __name__ == "__main__":
    main()
