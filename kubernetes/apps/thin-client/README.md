# thin-client/

Remote Linux desktop infrastructure for Dell Wyse 5070 thin clients.

## Architecture

The Wyse 5070 thin client PXE-boots a minimal Alpine Linux environment that runs entirely in RAM, then auto-connects to a Linux desktop (XFCE4) running as a container on the Kubernetes cluster.

```
┌──────────────────────┐     ┌──────────────────────────────────┐
│   Wyse 5070          │     │   Kubernetes Cluster             │
│                      │     │                                  │
│  ┌────────────────┐  │     │  ┌────────────┐                  │
│  │ UEFI PXE Boot  │──│──DHCP──│  netboot   │ (iPXE + TFTP)   │
│  └───────┬────────┘  │     │  └────────────┘                  │
│          │           │     │                                  │
│  ┌───────▼────────┐  │     │  ┌────────────────────────────┐  │
│  │ Alpine Linux   │  │     │  │  desktop                   │  │
│  │ (runs in RAM)  │  │     │  │  XFCE4 + KasmVNC           │  │
│  │                │──│─HTTPS──│  linuxserver/webtop         │  │
│  │ Chromium Kiosk │  │     │  │                            │  │
│  └────────────────┘  │     │  │  Persistent: 20Gi Ceph PVC │  │
│                      │     │  └────────────────────────────┘  │
└──────────────────────┘     └──────────────────────────────────┘
```

## Apps

| App | Purpose | Image |
|-----|---------|-------|
| [desktop](desktop/) | XFCE4 Linux desktop (KasmVNC web access) | `lscr.io/linuxserver/webtop:alpine-xfce` |
| [netboot](netboot/) | iPXE boot server (TFTP + HTTP + web UI) | `ghcr.io/netbootxyz/netbootxyz` |

## Boot Chain

1. **Wyse 5070 powers on** → UEFI PXE boot request
2. **DHCP server** responds with `next-server` (netboot LoadBalancer IP) and `filename` (ipxe.efi)
3. **iPXE firmware** chainloads from the netboot pod via TFTP
4. **iPXE script** (`custom.ipxe`) downloads Alpine Linux kernel + initramfs from Alpine CDN
5. **Alpine Linux** boots diskless (entirely in RAM)
6. **Post-boot script** installs Chromium, Openbox, and auto-starts X11
7. **Chromium kiosk** connects to `https://desktop.<domain>` — full XFCE desktop via KasmVNC

## DHCP Configuration

Your network DHCP server must be configured to point PXE clients at the netboot service:

| Option | Value | Description |
|--------|-------|-------------|
| Option 66 (`next-server`) | `192.168.1.70` | netboot LoadBalancer IP |
| Option 67 (`bootfile-name`) | `netboot.xyz.efi` | iPXE UEFI binary |

### UniFi Controller

In the UniFi Network UI:
1. Go to **Settings → Networks → (your LAN)**
2. Enable **DHCP Network Boot**
3. Set **Server** to `192.168.1.70`
4. Set **File** to `netboot.xyz.efi`

> **Tip**: To scope this to only the Wyse 5070, create a DHCP reservation for the thin client's MAC address and apply the boot options only to that reservation.

## Desktop Access

| Method | URL | Use Case |
|--------|-----|----------|
| Internal (browser) | `https://desktop.<domain>` | Access from any device on the LAN |
| Thin client (auto) | Chromium kiosk → same URL | Wyse 5070 auto-connects on boot |

## First-Time Alpine Setup

After the Wyse 5070 boots into Alpine for the first time, you need to run the setup script to install the desktop client packages:

```sh
# From the Alpine console (Alt+F2 for second TTY)
wget -qO- http://192.168.1.70:3000/config/menus/custom/thin-client-setup.sh | sh
```

This installs Chromium, Openbox, Intel GPU drivers, and configures auto-login + kiosk mode. After setup, the thin client will automatically boot into the desktop on subsequent restarts.

> **Note**: For a fully automated experience, consider building a custom Alpine overlay (`apkovl.tar.gz`) with these packages pre-installed and hosting it on the netboot server.

## Persistent Storage

The desktop pod uses a 20Gi Ceph block PVC mounted at `/config`. This persists:
- Desktop configuration and customizations
- Application data and settings  
- User files stored under `/config`

Backups are managed by VolSync (hourly Kopia snapshots to S3).

## Future Enhancements

- [ ] Custom Alpine overlay (`apkovl.tar.gz`) for zero-touch thin client boot
- [ ] XRDP server container for native RDP multi-monitor support
- [ ] Moonlight/Sunshine streaming for GPU-accelerated desktop (DGX Spark worker)
- [ ] Network policies restricting desktop access to thin client subnet
- [ ] Authentik SSO integration for web-based desktop access
- [ ] Multiple desktop profiles (dev workstation, media center, etc.)
