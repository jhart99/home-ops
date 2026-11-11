# kubernetes/components/

Reusable [Kustomize Components](https://kubectl.docs.kubernetes.io/references/kustomize/kustomization/components/) shared across app deployments. Reference them from an app's `kustomization.yaml` to add capabilities without duplicating manifests.

## Available Components

### `sops/`

Deploys the `cluster-secrets` Secret (SOPS-encrypted) into the target namespace. Required by any app that uses `substituteFrom: cluster-secrets` in its Flux `Kustomization`.

**Use when**: An app needs access to cluster-wide variables like `SECRET_DOMAIN`, credentials, or other shared secrets.

Reference in `kustomization.yaml`:
```yaml
components:
  - ../../../../components/sops
```

---

### `volsync/`

Adds VolSync PVC replication resources to an app: a PersistentVolumeClaim, a `ReplicationSource` (backup), and a `ReplicationDestination` (restore). Also includes the encrypted VolSync credentials secret.

**Use when**: An app has stateful data that needs backup and restore via Kopia/VolSync.

Required substitution variables in the Flux `ks.yaml`:
```yaml
postBuild:
  substitute:
    APP: <app-name>           # used to name the PVC and replication resources
    VOLSYNC_CAPACITY: 10Gi    # PVC size
```

Reference in `kustomization.yaml`:
```yaml
components:
  - ../../../../components/volsync
```

---

### `alerts/`

Deploys Flux `Alert` and `Provider` resources for notification on reconciliation events. Includes two sub-components:
- `alertmanager/` — sends alerts to Alertmanager
- `github-status/` — posts commit status checks to GitHub (requires encrypted secret)

**Use when**: A namespace should report Flux reconciliation status to Alertmanager or GitHub.

---

### `intel-gpu/`

Adds a `ResourceClaimTemplate` for Dynamic Resource Allocation (DRA) access to Intel GPU resources.

**Use when**: A workload needs access to an Intel iGPU via the Kubernetes DRA API.

---

### `nfs-scaler/`

Adds a KEDA `ScaledObject` that scales a deployment based on NFS server availability.

**Use when**: A workload backed by NFS should scale down when the NFS server is unreachable.

---

### `vpn/`

Adds a Cilium `EgressGatewayPolicy` to route a namespace's egress traffic through a VPN gateway.

**Use when**: An app's outbound traffic must exit through the VPN (e.g., for download apps or privacy-sensitive workloads).

---

### `deny-all/`

Adds baseline per-namespace `CiliumNetworkPolicy` resources (`default-deny-ingress` and `default-deny-egress`) selecting `endpointSelector: {}` across the namespace.

* **Audit Mode (`enableDefaultDeny: false`, current default)**: Forces Cilium to enable policy evaluation (`policy-enabled: both`) on every pod in the namespace using a no-op selector (`io.cilium.policy/audit-noop: "true"`) so that pods without a dedicated CNP still emit Hubble `PolicyVerdict` (`match="all"`) metrics without dropping traffic.
* **Enforce Mode (`enableDefaultDeny: true`)**: Blocks all ingress and egress traffic to/from pods in the namespace unless explicitly allowed by another `CiliumNetworkPolicy`.

Reference in `kustomization.yaml`:
```yaml
components:
  - ../../components/deny-all
```

---

### `cnpg-netpol/`

Adds the standard set of Cilium `CiliumNetworkPolicy` resources for a CloudNativePG (`cnpg.io/cluster: ${APP}`) PostgreSQL cluster:
- `${APP}-allow-client-ingress` — SQL client ingress (`5432/TCP`) from pods matching `app.kubernetes.io/instance: ${CNPG_CLIENT}` in the same namespace
- `${APP}-allow-instance-traffic` — streaming replication (`5432/TCP`) and instance-manager (`8000/TCP`) between cluster instances, plus CNPG operator management (`8000/TCP` from `database/cloudnative-pg`)
- `${APP}-allow-backup-egress` — Barman Cloud WAL/backup archiving to `s3.rnatr.com` (`443/TCP` and in-cluster `envoy-internal` `443, 10443/TCP`)
- `${APP}-allow-kubelet-probes` — kubelet liveness/readiness probes (`5432, 8000/TCP` from `host`/`remote-node`)

Required substitution variables in the database Flux `ks.yaml`:
```yaml
postBuild:
  substitute:
    APP: <cluster-name>         # e.g. immich-db (matches cnpg.io/cluster)
    CNPG_CLIENT: <client-app>   # e.g. immich (matches app.kubernetes.io/instance)
```

Reference in `database/kustomization.yaml`:
```yaml
components:
  - ../../../../components/cnpg-netpol
```

---

## How Components Work

Components are `kustomize.toolkit.fluxcd.io/v1alpha1` `Component` resources. Unlike regular `bases`, components can be composed — multiple components can be applied to a single app without conflict.

A typical app `kustomization.yaml` might look like:
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ./helmrelease.yaml
components:
  - ../../../../components/sops
  - ../../../../components/volsync
```

And the app's `ks.yaml` passes in the required substitution variables:
```yaml
postBuild:
  substitute:
    APP: myapp
    VOLSYNC_CAPACITY: 5Gi
  substituteFrom:
    - kind: Secret
      name: cluster-secrets
```
