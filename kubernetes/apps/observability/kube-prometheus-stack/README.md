# kube-prometheus-stack

Deploys [Prometheus](https://prometheus.io/), [Alertmanager](https://prometheus.io/docs/alerting/latest/alertmanager/), node-exporter, and kube-state-metrics via the [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack) Helm chart.

Grafana is **not** deployed here — it is managed separately in the `grafana` app and connects to this Prometheus instance as a datasource.

## Endpoints

| Service | URL |
|---------|-----|
| Prometheus | `https://prometheus.${SECRET_DOMAIN}` (internal gateway) |
| Alertmanager | `https://alertmanager.${SECRET_DOMAIN}` (internal gateway) |

## Storage

| Component | Storage Class | Size |
|-----------|--------------|------|
| Prometheus TSDB | `ceph-fast` | 50 Gi |
| Alertmanager | `ceph-fast` | 1 Gi |

Retention: **14 days** / **50 GB**, whichever is reached first.

## Alerting

Alerts route to **Pushover** for mobile notifications. The routing config (`alertmanagerconfig.yaml`):

- Critical alerts → Pushover (priority high, sound `gamelan`)
- `InfoInhibitor` alerts → blackhole (suppressed)
- Critical alerts suppress warnings for the same `alertname`/`namespace`
- Grouping by `alertname`, `cluster`, `job` with 1 min group wait and 5 min group interval
- Resolved notifications are sent (`sendResolved: true`)

Pushover credentials are stored in `alertmanager-secret` (SOPS-encrypted).

## Custom Alert Rules

Three additional rule groups are defined in the HelmRelease:

### `dockerhub-rules`
**DockerhubRateLimitRisk** (critical) — fires when more than 100 containers are actively pulling from `docker.io`. Warns before Docker Hub rate limits hit.

### `oom-rules`
**OomKilled** (critical) — fires when a container has been OOM-killed at least once in the last 10 minutes. Calculated from restart counter deltas combined with last-terminated-reason.

### `zfs-rules`
**ZfsUnexpectedPoolState** (critical) — fires when a ZFS pool is in any state other than `online`. Targets the NAS which exposes node metrics via node-exporter.

## Grafana Dashboards

Dashboards are provisioned as `GrafanaDashboard` CRs (fetched from grafana.com at deploy time) targeting the `grafana` instance:

| Dashboard | Grafana ID |
|-----------|-----------|
| Kubernetes / API server | 15761 |
| Kubernetes / CoreDNS | 15762 |
| Kubernetes / Global | 15757 |
| Kubernetes / Namespaces | 15758 |
| Kubernetes / Nodes | 15759 |
| Kubernetes / Pods | 15760 |
| Kubernetes / Volumes | 11454 |
| Node Exporter Full | 1860 |
| Prometheus | 19105 |

## Scrape Configuration

All selectors have `NilUsesHelmValues: false`, meaning Prometheus will discover **all** `ServiceMonitor`, `PodMonitor`, `ProbeConfig`, `ScrapeConfig`, and `PrometheusRule` objects cluster-wide regardless of Helm release labels. This is intentional — apps across all namespaces can define their own monitors without needing to match chart labels.

## NAS / External Host Monitoring

To scrape metrics from hosts outside the cluster, run exporters on those hosts via Docker Compose and expose their ports to Prometheus. Example configs for common exporters:

### node-exporter

```yaml
services:
  node-exporter:
    command:
      - '--path.rootfs=/host/root'
      - '--path.procfs=/host/proc'
      - '--path.sysfs=/host/sys'
      - '--path.udev.data=/host/root/run/udev/data'
      - '--web.listen-address=0.0.0.0:9100'
      - >-
        --collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)
    image: quay.io/prometheus/node-exporter:v1.9.0
    network_mode: host
    ports:
      - '9100:9100'
    restart: always
    volumes:
      - /:/host/root:ro
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
```

### smartctl-exporter

```yaml
services:
  smartctl-exporter:
    command:
      - '--smartctl.device-exclude=nvme0'
    image: quay.io/prometheuscommunity/smartctl-exporter:v0.13.0
    ports:
      - '9633:9633'
    privileged: True
    restart: always
    user: root
```

Once the exporter is running on the NAS, add a `ScrapeConfig` or `ServiceMonitor` in the `observability` namespace pointing at the NAS IP and port to pull metrics into Prometheus.
