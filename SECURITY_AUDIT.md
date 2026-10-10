# Security Audit — Findings List

> Source: security audit performed 2026-09-29 (4 parallel reviews: container security
> contexts, network exposure, secrets/RBAC, supply chain/Talos). Findings below were
> re-verified against `origin/main` at commit `64175f84` on 2026-09-30 — all items are
> still present unless noted.
>
> Status: **description only, no fixes applied.** Work through these incrementally.
> One item is already partially addressed (see #8).

## 🔴 Critical — fix first

1. **VNC password hardcoded as plaintext**
   `kubernetes/apps/thin-client/desktop/app/helmrelease.yaml:36` sets `VNC_PW: password`
   as a plain env var. Anyone reaching port 6901 can log in with "password".
   *Remediation:* move to a SOPS-encrypted secret.

2. **Mosquitto MQTT broker allows anonymous access**
   `kubernetes/apps/default/mosquitto/app/helmrelease.yaml:61` has `allow_anonymous true`.
   Anyone on the network can publish/subscribe to all MQTT topics with no credentials —
   a common IoT attack vector.
   *Remediation:* require credentials (password file / ACLs) and disable anonymous access.

3. **Tuwunel (Matrix) has open registration**
   `kubernetes/apps/communication/tuwunel/app/helmrelease.yaml:42` sets
   `CONDUWUIT_ALLOW_REGISTRATION: "true"`. Anyone on the internet can create accounts on
   the Matrix server.
   *Remediation:* disable open registration (or gate it behind an admin approval flow).

4. **JupyterHub: all network policies disabled**
   `kubernetes/apps/jupyterhub/jupyterhub/app/helmrelease.yaml:20-30` sets
   `networkPolicy.enabled: false` on hub, proxy, and singleuser, plus
   `cloudMetadata.blockWithIptables: false`. User notebook pods can reach the Kubernetes
   API, the cloud metadata service, and every other namespace.
   *Remediation:* re-enable the chart's network policies and metadata blocking.

5. **JupyterHub: unpinned `:latest` images**
   `kubernetes/apps/jupyterhub/jupyterhub/app/helmrelease.yaml:37,42` uses
   `jupyter/pyspark-notebook:latest` and `jupyter/datascience-notebook:latest`. Mutable
   tags allow silent supply-chain injection.
   *Remediation:* pin to digests (Renovate can keep them updated).

6. **Hermes DinD sidecar runs fully privileged as root**
   `kubernetes/apps/ai/hermes/app/helmrelease.yaml:46-50` runs the `daemon` container with
   `privileged: true`, `runAsUser: 0`, `allowPrivilegeEscalation: true`. Container escape
   gives full host access.
   *Remediation:* consider rootless alternatives (rootless Docker/Podman, Kaniko for
   builds) to eliminate privileged DinD.

7. **Forgejo runner DinD runs privileged**
   `kubernetes/apps/default/forgejo-runner/app/helmrelease.yaml:68-69` runs the `daemon`
   container `privileged: true`. Any CI job can escape to the host.
   *Remediation:* DinD over TCP to a non-privileged daemon, or rootless Docker.

## 🟠 High — remediate soon

8. **Cluster-wide NetworkPolicies — mostly still missing**
   Originally only one CiliumNetworkPolicy existed (echo-oidcauth); there was no
   default-deny anywhere. **Progress:** the `system-upgrade` namespace now has
   default-deny + Tuppr egress policies (merged via PR #1398, using the new
   `kubernetes/components/deny-all` and `kubernetes/components/egress` components).
   Remaining namespaces have no default-deny or isolation policies.
   *Remediation:* roll the same component pattern out to the remaining namespaces.

9. **Grafana: anonymous read access**
   `kubernetes/apps/observability/grafana/instance/grafana.yaml:19` has
   `auth.anonymous.enabled: "true"`. Anyone on the LAN can view all dashboards.
   *Remediation:* disable anonymous auth; require login (OIDC/Authentik).

10. ~~**Kromgo externally exposed with no auth**~~ ✅ **Accepted risk — hardened**
    ~~`kubernetes/apps/observability/kromgo/app/helmrelease.yaml` is routed via
    `envoy-external` with no SecurityPolicy. Cluster metrics are visible on the public
    internet at `kromgo.${SECRET_DOMAIN}`.~~
    *Resolution (2026-10-10, #1426): public unauthenticated reads are **accepted by
    design** — the README's shields.io badges fetch `kromgo.${SECRET_DOMAIN}` from the
    internet, kromgo only executes the queries preconfigured in
    `resources/config.yaml` (Prometheus itself is never exposed), and the default
    responses carry only the curated badge values (versions, counts, utilisation,
    alert count) that the README already publishes. **Hardening** (same PR): requests
    for `?format=raw` — which additionally returns internal labels (pod names,
    pod/node IPs, namespace names) — are denied at the gateway by a backend-less
    HTTPRoute rule, and a `BackendTrafficPolicy` local rate limit (60 req/min) bounds
    unauthenticated PromQL load on Prometheus. **Residual accepted:** a
    percent-encoded query-parameter *name* bypasses the `format=raw` deny (Envoy
    matches query strings verbatim), still bounded by the rate limit.*

11. **Echo externally exposed with no auth**
    `kubernetes/apps/default/echo/app/helmrelease.yaml` on `envoy-external` with no
    authentication. Leaks request headers; unnecessary attack surface.
    *Remediation:* add auth or remove the external route.

12. **ToolHive MCP gateway: anonymous auth**
    `kubernetes/apps/ai/toolhive/app/virtualmcpserver.yaml:19` has
    `incomingAuth.type: anonymous`, and the registry uses `mode: anonymous`
    (`kubernetes/apps/ai/toolhive/registry/helmrelease.yaml:67`). Any pod in the cluster
    can use the aggregated MCP tools.
    *Remediation:* enable incoming auth on the MCP gateway and registry.

13. **Flux: network policies disabled**
    `kubernetes/apps/flux-system/flux-instance/app/helmrelease.yaml:18` sets
    `cluster.networkPolicy: false`. Flux controllers (which can read secrets) are
    network-accessible from any pod.
    *Remediation:* set `cluster.networkPolicy: true` and/or add namespace default-deny.

14. **Wazuh agent: privileged with 9 hostPath mounts**
    `kubernetes/apps/security/wazuh/app/helmrelease.yaml:171-172,194-218` — privileged
    container mounting `/var/run`, `/dev`, `/sys`, `/proc`, `/etc`, `/boot`, `/usr`,
    `/lib/modules`, `/var/log`. Near-complete host filesystem access.
    *Remediation:* evaluate Wazuh's container-native/unprivileged monitoring mode; trim
    mounts to the minimum required.

15. **Trivy operator: hostPath mounts to control plane**
    `kubernetes/apps/security/trivy/app/helmrelease.yaml:170-178` mounts
    `/var/lib/etcd`, `/var/lib/kubelet`, `/etc/kubernetes`, `/etc/cni/net.d/`,
    `/system/secrets/kubernetes`. Exposes cluster control-plane secrets.
    *Remediation:* verify whether these mounts are required by the current Trivy version;
    scope down or make read-only where possible.

16. **No Pod Security Standards on 18/20 namespaces**
    Only `rook-ceph` and `rustfs` have `pod-security.kubernetes.io` labels. Everything
    else has no admission-level enforcement, so any pod can request `privileged`,
    `hostNetwork`, `hostPID`, etc.
    *Remediation:* add PSS labels (`audit`/`warn: restricted` first, then enforce) to all
    namespaces.

17. **Unpinned image tags**
    - `kubernetes/apps/ai/github-mcp/app/mcpserver.yaml:8` — `github-mcp-server` with no
      tag (defaults to `:latest`)
    - `kubernetes/apps/ai/kubesearch-mcp/app/mcpserver.yaml:8` — `kubesearch-mcp:master`
      (branch name as tag)
    - `kubernetes/apps/ai/llmkube/models/qwen3-embedding-06b.yaml:33` and
      `qwen36-27B-q4.yaml:30` — `llama.cpp:server-cuda` with no tag/digest
    *Remediation:* pin all to version + digest.

18. **No image scanning/signing enforcement**
    Trivy operator is installed, but no Kyverno/OPA policy enforces scan results before
    deployment, and no Cosign/Notation signature verification is configured on Flux
    image repositories.
    *Remediation:* add a policy that blocks images with critical CVEs; add signature
    verification to Flux `ImageRepository` resources.

19. **Gatus: automountServiceAccountToken enabled**
    `kubernetes/apps/observability/gatus/app/helmrelease.yaml:75` explicitly sets
    `automountServiceAccountToken: true` on the pod, and Gatus has a ClusterRoleBinding
    for services/gateways/httproutes read access. A compromised pod gets a token with
    cluster-wide read on those resources.
    *Remediation:* disable token automount if Gatus doesn't need in-cluster API access;
    otherwise narrow the ClusterRole.

20. **Netboot: root + unauthenticated TFTP on LoadBalancer**
    `kubernetes/apps/thin-client/netboot/app/helmrelease.yaml:36-48` runs as root
    (`runAsUser: 0`) and exposes TFTP (69/UDP) on `192.168.1.70`. Any LAN device can
    download boot images; TFTP has no auth by design.
    *Remediation:* run as non-root; consider restricting TFTP exposure (VLAN/firewall)
    since thin clients only need it at PXE time.

21. ~~**UniFi controller: root + 6 exposed ports**~~ ✅ **Resolved**
    ~~`kubernetes/apps/network/unifi/app/helmrelease.yaml:37-59` runs as root with
    `RUNAS_UID0: "true"` and exposes 8443, 8080, 6789, 3478 (STUN, unencrypted), 5514
    (syslog, unencrypted), 10001 via LoadBalancer.~~
    *Resolution:* app removed entirely (PR #1415) — no longer needed. The `unifi-dns`
    external-dns webhook and observability integrations (unpoller, blackbox-exporter)
    reference an external UniFi controller and are unaffected.

## 🟡 Medium — hardening

22. **Single SOPS age key for all secrets**
    `.sops.yaml:5,9` — one age key encrypts all 59+ SOPS-encrypted files (Flux tokens,
    TLS keys, DB credentials, API tokens). Compromise of that one key exposes everything.
    *Remediation:* split keys by category (e.g., Flux/infrastructure vs. application
    secrets) or by environment.

23. **SMTP relay: TLS disabled on inbound listener**
    `kubernetes/apps/communication/smtp-relay/app/helmrelease.yaml:75` — `tls off` in
    maddy.conf; plaintext SMTP on port 25 via LoadBalancer.
    *Remediation:* enable TLS on the inbound listener.

24. **Pi-hole: DNSSEC disabled**
    `kubernetes/apps/default/pihole/app/helmrelease.yaml:30` —
    `FTLCONF_dns_dnssec: 'false'`. Vulnerable to DNS spoofing/cache poisoning, and DNS
    is exposed on a LoadBalancer.
    *Remediation:* enable DNSSEC.

25. **ComfyUI: securityContext commented out**
    `kubernetes/apps/ai/comfyui/app/helmrelease.yaml:96-101` — the intended
    `runAsUser: 1000` / `runAsNonRoot: true` block is commented out; the container runs
    as root by default.
    *Remediation:* re-enable the securityContext block (verify the image supports UID
    1000 first).

26. **LiteLLM: no securityContext at all**
    `kubernetes/apps/ai/litellm/app/helmrelease.yaml` — the proxy that handles all LLM
    API keys and routing has zero container security restrictions.
    *Remediation:* add runAsNonRoot, readOnlyRootFilesystem, capability drops.

27. **Dangerous Linux capabilities**
    - `DAC_OVERRIDE` on Hermes and SearXNG (`ai/hermes/app/helmrelease.yaml:163-168`,
      `ai/searxng/app/helmrelease.yaml:183-187`) — bypasses file permission checks
    - `SYS_CHROOT` on Wazuh master/worker (`security/wazuh/app/helmrelease.yaml:131-133,
      157-159`) — enables chroot escape techniques
    - `NET_RAW` on wg-easy (`default/wg-easy/app/helmrelease.yaml:46-48`) — packet
      injection/sniffing; verify whether it's strictly required
    *Remediation:* drop each capability and test; keep only what's demonstrably needed.

28. **15+ HelmReleases missing securityContext entirely**
    immich, freshrss, tuwunel, backstage, coder, jupyterhub, grafana,
    kube-prometheus-stack, all ToolHive components (operator/crds/registry/ui), and the
    MCP server CRDs. The app-template chart does not inject defaults, so these likely
    run as root with full capabilities.
    *Remediation:* add a standard securityContext block (non-root, drop ALL caps,
    readOnlyRootFilesystem, RuntimeDefault seccomp) modeled on well-configured apps in
    this repo (e.g., pihole, smtp-relay).

29. **kubectl-mcp-readonly ClusterRole: broad read access**
    `kubernetes/apps/ai/kubectl-mcp/app/rbac.yaml:7-136` — `resources: ["*"]` across 50+
    API groups plus an aggregation rule that pulls in all CRDs with
    `aggregate-to-view`. Secrets are correctly excluded from core v1, but all ConfigMaps
    and CRD specs (ExternalSecrets, Certificates, HelmRelease values) are readable.
    *Remediation:* enumerate the specific resources/groups actually needed instead of
    `*`.

30. **Beta API server features enabled**
    `talos/patches/global/cluster.yaml` — `MutatingAdmissionPolicy=true` and
    `admissionregistration.k8s.io/v1beta1=true` runtime config. Beta APIs with
    security-relevant behavior increase attack surface.
    *Remediation:* evaluate whether the beta features are required; remove if not.

## 🔵 Low — review when convenient

31. **Forgejo SSH exposed via LoadBalancer**
    `kubernetes/apps/default/forgejo/app/helmrelease.yaml:27-33` — SSH on port 2222 with
    DNS registration. Registration is disabled and sign-in is required, so risk is
    limited, but internet-facing git SSH is still notable surface.

32. **Flux webhook receiver on external gateway**
    `kubernetes/apps/flux-system/flux-instance/app/httproute.yaml` —
    `flux-webhook.${SECRET_DOMAIN}/hook/` relies on shared-secret validation only, no
    gateway-level auth. Path is predictable.

33. **Renovate webhook on external gateway**
    `kubernetes/apps/default/renovate/operator/helmrelease.yaml:41-44` — same pattern as
    the Flux webhook; no SecurityPolicy.

---

## Suggested order of attack (highest impact-to-effort)

1. **Default-deny NetworkPolicies everywhere** — pattern proven in `system-upgrade`
   (PR #1398); this is the blast-radius multiplier for everything else.
2. **Quick config wins:** VNC password → SOPS secret; Mosquitto auth on; Tuwunel
   registration off; JupyterHub netpols on; Grafana anonymous off.
3. **Pin image tags** to digests; enable Trivy policy enforcement.
4. **PSS labels** on all namespaces (`audit`/`warn: restricted` first).
5. **Harden privileged workloads** (Hermes DinD, Forgejo runner, Wazuh) and add
   securityContext blocks to the apps listed in #28.
