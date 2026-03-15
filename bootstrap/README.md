# bootstrap/

One-time bootstrap resources for initializing the cluster. These are only needed for initial setup — day-to-day operations use `kubernetes/` via Flux.

## Files

| File / Directory | Purpose |
|-----------------|---------|
| `helmfile.d/` | Helmfile definitions for bootstrapping core apps (Cilium, CoreDNS, Flux) |
| `kustomization.yaml` | Kustomize entry point for bootstrap resources |
| `namespace.yaml` | Namespace definitions needed before Flux is running |
| `github-deploy-key.sops.yaml` | Encrypted SSH deploy key for Flux to read this repository |
| `sops-age.sops.yaml` | Encrypted age key used by Flux for SOPS decryption |

## Bootstrap Process

> Only needed when setting up the cluster from scratch or after a full reset.

### Prerequisites

- Age key at `age.key` in the repo root
- SOPS config at `.sops.yaml`
- `talconfig.yaml` with node definitions
- All required tools installed via `mise install`

### Step 1: Bootstrap Talos

Generates secrets, creates node configs, and applies them to all nodes. Talos will form a cluster but Kubernetes won't be usable yet (no CNI).

```sh
task bootstrap:talos
```

After this completes, commit the generated `talos/talsecret.sops.yaml`:

```sh
git add talos/talsecret.sops.yaml
git commit -m "chore: add talhelper encrypted secret"
git push
```

### Step 2: Bootstrap Apps

Installs the core in-cluster dependencies via Helmfile (Cilium, CoreDNS, Spegel), then deploys Flux and syncs the cluster to the repository state.

```sh
task bootstrap:apps
```

This runs the bootstrap script which:
1. Applies `helmfile.d/` charts (Cilium CNI, CoreDNS, Spegel image cache)
2. Creates the `flux-system` namespace and applies SOPS/deploy key secrets
3. Installs the Flux operator
4. Creates the `GitRepository` pointing at this repo
5. Applies the root `flux/cluster/ks.yaml` Kustomization

### Watching the Rollout

```sh
kubectl get pods --all-namespaces --watch
flux get ks -A
flux get hr -A
```

Initial rollout takes 10+ minutes. Errors during this period are normal while CRDs and dependencies come online.

## Secrets in Bootstrap

The two SOPS-encrypted files here are applied to the cluster during bootstrap and are not managed by Flux:

- `sops-age.sops.yaml` — Contains the age private key as a Kubernetes Secret in `flux-system`. Flux uses this to decrypt all `*.sops.yaml` files it encounters.
- `github-deploy-key.sops.yaml` — SSH private key allowing Flux to pull from the private GitHub repository.

To re-encrypt after rotating:
```sh
sops --encrypt --in-place bootstrap/sops-age.sops.yaml
sops --encrypt --in-place bootstrap/github-deploy-key.sops.yaml
```
