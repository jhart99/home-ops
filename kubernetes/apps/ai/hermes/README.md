# kubernetes/apps/ai/hermes

[Hermes](https://github.com/jhart99/jhart99-hermes-agent) is a self-hosted AI coding agent with a web dashboard, persistent memory, and the ability to execute code in containers. It runs in the `ai` namespace alongside its LLM backend (llama-cpp) and memory service (agentmemory).

## Components

| File | Description |
|------|-------------|
| `ks.yaml` | Flux Kustomization — defines deployment order and VolSync PVC (5Gi) |
| `app/helmrelease.yaml` | Main deployment via bjw-s app-template |
| `app/ocirepository.yaml` | OCI source for the app-template Helm chart |
| `app/configmap.sops.yaml` | SOPS-encrypted Hermes config (model endpoints, etc.) |
| `app/secret.sops.yaml` | SOPS-encrypted secrets (API keys, OIDC, Matrix credentials) |
| `app/agentmemory-plugin.yaml` | Python plugin ConfigMap for cross-session persistent memory |

## Endpoints

- **Dashboard**: `https://hermes.${SECRET_DOMAIN}` (port 9119, OIDC via Authentik)
- **Gateway/API**: port 8642 (cluster-internal only, not externally routed)

## Dependencies

- **llama-cpp** — local LLM inference backend
- **agentmemory** — persistent cross-session memory service
- **rook-ceph-cluster** — block storage for the 5Gi PVC
- **volsync** — PVC snapshot/backup replication

## Odd Settings Explained

### Docker-in-Docker sidecar (daemon init container)

Hermes is a coding agent that needs to run arbitrary code in isolated containers. Rather than requiring access to the host's container runtime, it runs its own Docker daemon as a sidecar. This is implemented as an init container with `restartPolicy: Always` (Kubernetes "sidecar" pattern) so it starts before the main container and stays running.

- Runs **privileged** with `runAsUser: 0` — required for DinD to manage its own filesystem and networking.
- `DOCKER_TLS_CERTDIR: /certs` tells the DinD daemon to auto-generate TLS certificates. The certs land in `/certs/server` (daemon) and `/certs/client` (shared via emptyDir).
- The main container sets `DOCKER_HOST: tcp://localhost:2376` and `DOCKER_TLS_VERIFY: "1"` with `DOCKER_CERT_PATH: /certs/client` to connect to the DinD daemon securely over TLS.
- The `/certs` emptyDir volume is the handshake mechanism between daemon and client.

### Main container runs as root (UID 0) despite HERMES_UID/HERMES_GID = 10000

The container runs as root initially but hermes internally drops privileges to `HERMES_UID: 10000` / `HERMES_GID: 10000` for its worker processes. The elevated capabilities are needed for:

- **CHOWN, FOWNER, SETUID, SETGID** — hermes creates/manages its own user environment inside `/opt/data` (home directories, virtualenvs, Go toolchains). It needs to chown files to its internal unprivileged user.
- **DAC_OVERRIDE** — read/write files regardless of permission bits during environment bootstrapping.
- `allowPrivilegeEscalation: true` — needed for the privilege drop to work.
- `readOnlyRootFilesystem: false` — hermes installs tools (Go, Python packages, Homebrew packages) into `/opt/data` at runtime.

### GOPATH, PYTHONPATH, and custom PATH

Hermes installs its own toolchains at runtime into the persistent volume:

- `GOPATH: /opt/data/go` — Go modules and build cache
- `PYTHONPATH: /opt/hermes/.venv/lib/python3.13/site-packages/` — points to the venv baked into the container image
- `PIP_BREAK_SYSTEM_PACKAGES: "1"` — allows pip to install into the container's system Python (needed for agent-installed packages)
- `PATH` includes `/opt/data/.local/bin`, `/opt/data/.local/go/bin`, `/opt/data/.local/homebrew/bin` — user-installed tools (via Go, pip, brew) that persist across container restarts on the PVC

This means hermes can `pip install`, `go install`, or `brew install` tools on demand, and they survive pod restarts because `/opt/data` is a persistent volume.

### Init container copies config.yaml to PVC (cp -n)

The SOPS-encrypted ConfigMap is mounted only to the init container, which runs `cp -n` (no-clobber) to copy `config.yaml` into `/opt/data` on the PVC. This serves two purposes:

1. **ConfigMap can't be mounted as read-write** — so the file is copied to the PVC where hermes can modify it.
2. **`-n` flag** — won't overwrite if the user has manually edited config.yaml on the PVC. This preserves local customizations across Flux reconciliation cycles.

### HOME set to /opt/data

`HOME: /opt/data` ensures all dotfiles (`.bashrc`, `.local/`, `.cache/`, etc.) land on the persistent volume so they survive restarts. Without this, tool installations and shell config would be lost on every pod recreation.

### TERM: xterm-256color

Hermes has a TUI dashboard (`HERMES_DASHBOARD_TUI: "1"`), so a proper terminal type is needed even though it runs in a container. This prevents garbled output if anyone exec's into the pod.

### RBAC — three separate roles instead of one

The RBAC is deliberately split into a principle of least privilege:

| Role | Type | Purpose |
|------|------|---------|
| `hermes-read-all` | ClusterRole | Read-only across the entire cluster — the agent needs to observe pods, services, events, Flux state, etc. to understand what's deployed |
| `hermes-pod-delete` | ClusterRole | Delete pods — allows the agent to restart stuck workloads |
| `hermes-exec-deploy` | Role (ai namespace only) | Exec into pods and patch/update deployments — allows the agent to deploy changes, but **only in the `ai` namespace** where it has legitimate business |

The `exec-deploy` role is intentionally namespace-scoped, not cluster-wide. The agent can modify deployments in `ai` but not elsewhere.

### Resource limits (500m–4 CPU, 2Gi–16Gi memory)

The wide range reflects that hermes is a coding agent — workload varies dramatically from idle dashboard to compiling code inside Docker containers. The 16Gi upper bound accommodates builds and large LLM context windows.

### Dashboard OIDC via Authentik

The dashboard (port 9119) is the only externally exposed service. It's protected by OIDC through Authentik at `https://auth.rnatr.com/application/o/hermes/`. The OIDC client ID is not secret (it's a public identifier), but the client secret is in the SOPS-encrypted secret.

The gateway API (port 8642) is deliberately **not** externally routed — it's only accessible within the cluster, protected by `API_SERVER_KEY` from the secret.

### Matrix credentials in secret

The SOPS secret includes Matrix homeserver/user/password credentials. Hermes can send notifications or interact via Matrix chat — this is likely an alternative notification channel alongside the dashboard.

## Adding or Modifying

When updating the image tag, also update the digest. Renovate monitors `jhart99/jhart99-hermes-agent` and will auto-propose image updates via PR.
