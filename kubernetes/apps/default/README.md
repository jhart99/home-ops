# kubernetes/apps/default/

General purpose user applications and services. This namespace contains most of the "home" and "lab" services.

## Application Inventory

| App | Description | Components |
|-----|-------------|------------|
| **devshell** | Interactive pod for debugging and administrative tasks. | `pod`, `serviceaccount` |
| **echo** | Simple HTTP echo services for testing routing and authentication. | `deployment`, `httproute` |
| **forgejo** | Lightweight self-hosted Git service (Gitea fork). | `helmrelease`, `postgresql`, `storage` |
| **forgejo-runner** | CI/CD runner for Forgejo actions. | `deployment` |
| **freshrss** | Self-hosted RSS feed aggregator. | `helmrelease`, `postgresql` |
| **home-assistant** | Core home automation hub with Zigbee/Z-Wave integration. | `helmrelease`, `hostnetwork`, `usb-passthrough` |
| **immich** | High-performance self-hosted photo and video management. | `helmrelease`, `postgresql`, `redis`, `machine-learning` |
| **mosquitto** | MQTT broker for IoT device communication. | `helmrelease` |
| **ollama** | API for running large language models (LLMs) locally. | `deployment`, `nvidia-gpu` |
| **open-webui** | Web interface for interacting with Ollama. | `helmrelease` |
| **paperless** | Document management system for scanning and archiving. | `helmrelease`, `postgresql`, `redis` |
| **pihole** | Network-wide ad blocking and DNS resolution. | `helmrelease`, `loadbalancer-ip` |
| **renovate** | Automated dependency update bot for this repository. | `cronjob` |
| **smtp-relay** | Internal relay for cluster services to send emails. | `deployment` |
| **wg-easy** | Web UI for managing WireGuard VPN clients. | `helmrelease`, `udp-ingress` |
| **whoami** | Simple HTTP information service (Go-based). | `deployment`, `httproute` |
| **zigbee** | Zigbee2MQTT bridge for Zigbee IoT devices. | `helmrelease`, `usb-passthrough` |
| **zwave** | Z-Wave JS UI bridge for Z-Wave IoT devices. | `helmrelease`, `usb-passthrough` |

## Storage & Databases

Many apps in this namespace leverage:
- **CloudNative-PG**: Managed PostgreSQL clusters (e.g., Forgejo, Immich, Paperless).
- **Rook-Ceph**: Persistent volumes (PVCs) for data storage.
- **VolSync**: Automated backups to S3/Kopia.

## External Access

Publicly accessible services use `HTTPRoute` resources targeting the `envoy-external` gateway. Certificates are managed by `cert-manager` via Let's Encrypt (DNS-01 challenge).
