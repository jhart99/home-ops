# kubernetes/apps/actions-runner-system/

This namespace contains the infrastructure for self-hosted CI/CD runners, specifically utilizing the Actions Runner Controller (ARC) for GitHub Actions.

## Application Inventory

| App | Description | Components |
|-----|-------------|------------|
| **actions-runner-controller** | The core controller/operator for managing GitHub Actions self-hosted runners within Kubernetes. | `helmrelease` |
| **runners/home-ops** | The specific self-hosted runner scale set configured to run jobs for this repository. | `runnerdeployment` |

## Overview

Deploying self-hosted runners allows the cluster to execute GitHub Actions workflows locally. This provides the workflows with direct access to internal cluster resources, utilizes local homelab compute (potentially leveraging GPU or ARM nodes), and avoids the execution limits associated with standard GitHub-hosted runners.

Secrets (such as GitHub app credentials or PATs) required for the controller to authenticate with GitHub are typically managed via SOPS-encrypted secrets synced by Flux.
# Actions Runner Security & RBAC Notes

When running vulnerability scanners like Trivy within the `actions-runner` (or `actions-runner-system`) namespace, you may see several RBAC-related security findings flagged against the `home-ops-runner-gha-rs-manager` Role.

### Active Findings
* **AVD-KSV-0050 (CRITICAL):** Manage Kubernetes RBAC resources (`roles`, `rolebindings`).
* **AVD-KSV-0113 (MEDIUM):** Manage namespace secrets (`secrets`).
* **AVD-KSV-0048 (MEDIUM):** Manage Kubernetes workloads and pods (`pods`, `deployments`, `jobs`, etc.).

### Justification and Architecture
These permissions are functionally required by the Actions Runner Controller (ARC) to operate and are considered **accepted risks** constrained to this namespace:

1. **Dynamic Ephemeral Runners:** The controller must be able to spawn new runner pods (Workloads) and inject Just-In-Time (JIT) GitHub registration tokens (Secrets) on demand.
2. **Dynamic RBAC (statusUpdateHook):** If the status update hook is enabled, ARC dynamically generates a unique `ServiceAccount`, `Role`, and `RoleBinding` for each ephemeral runner it spins up so the individual runner can securely report its status back to the Kubernetes API. The controller requires write access to RBAC resources to facilitate this.

### Security Boundary
The blast radius of these permissions is inherently limited. They are granted via a namespace-scoped `Role` rather than a `ClusterRole`. The ARC manager has no authority to create roles, read secrets, or spawn workloads outside of the `actions-runner-system` boundary. Furthermore, standard Kubernetes API restrictions prevent the manager from creating a role with permissions that exceed its own.

### Remediation vs. Acceptance Options
* **Option A: Disable Dynamic RBAC (Resolve AVD-KSV-0050):** If granular status reporting is not required, disable the status update hook in the ARC Helm values (`runner.statusUpdateHook.enabled: false`). This instructs the chart to strip the RBAC management permissions entirely, which automatically clears the critical Trivy alert.
* **Option B: Dashboard Filtering:** Due to a known upstream limitation in Trivy Operator, the RBAC scanner plugin ignores standard `ignore-avd` annotations and `skipResourceByLabels` di