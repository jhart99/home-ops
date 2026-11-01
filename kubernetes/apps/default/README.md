# kubernetes/apps/default/

General purpose user applications and services. This namespace contains most of the "home" and "lab" services.

## Application Inventory

| App | Description | Components |
|-----|-------------|------------|
| **blog** | Personal blog site. | `helmrelease`, `httproute` |
| **forgejo** | Lightweight self-hosted Git service (Gitea fork). | `helmrelease`, `postgresql`, `dragonfly`, `storage` |
| **forgejo-runner** | CI/CD runner for Forgejo actions. | `helmrelease`, `statefulset` |
| **freshrss** | Self-hosted RSS feed aggregator. | `helmrelease`, `postgresql` |
| **home-assistant** | Core home automation hub with Zigbee/Z-Wave integration. | `helmrelease`, `multus`, `storage` |
| **immich** | High-performance self-hosted photo and video management. | `helmrelease`, `postgresql`, `dragonfly`, `machine-learning` |
| **mosquitto** | MQTT broker for IoT device communication. | `helmrelease` |
| **paperless** | Document management system for scanning and archiving. | `helmrelease`, `postgresql`, `dragonfly` |
| **pihole** | Network-wide ad blocking and DNS resolution. | `helmrelease`, `loadbalancer-ip` |
| **renovate** | Automated dependency update operator and jobs (GitHub & Forgejo). | `helmrelease`, `dragonfly` |
| **wg-easy** | Web UI for managing WireGuard VPN clients. | `helmrelease`, `udp-ingress` |
| **whoami** | Simple HTTP information service (Go-based). | `helmrelease`, `httproute` |
| **zigbee** | Zigbee2MQTT bridge for Zigbee IoT devices. | `helmrelease`, `usb-passthrough` |
| **zwave** | Z-Wave JS UI bridge for Z-Wave IoT devices. | `helmrelease`, `usb-passthrough` |

## Storage & Databases

Many apps in this namespace leverage:
- **CloudNative-PG**: Managed PostgreSQL clusters (e.g., Forgejo, Immich, Paperless).
- **Rook-Ceph**: Persistent volumes (PVCs) for data storage.
- **VolSync**: Automated backups to S3/Kopia.

## External Access

Publicly accessible services use `HTTPRoute` resources targeting the `envoy-external` gateway. Certificates are managed by `cert-manager` via Let's Encrypt (DNS-01 challenge).
