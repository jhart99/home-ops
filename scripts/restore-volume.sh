#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
LIB_DIR="${SCRIPT_DIR}/lib"

# Load common library if available, otherwise define minimal fallback
if [[ -f "${LIB_DIR}/common.sh" ]]; then
    # shellcheck source=/dev/null
    source "${LIB_DIR}/common.sh"
else
    function log() {
        local level="${1:-info}"
        shift
        local msg="$1"
        shift
        printf "[%s] [%s] %s %s\n" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "${level^^}" "${msg}" "$*"
        if [[ "$level" == "error" ]]; then
            exit 1
        fi
    }
    function check_cli() {
        for dep in "$@"; do
            if ! command -v "${dep}" &>/dev/null; then
                log error "Missing required tool: ${dep}"
            fi
        done
    }
fi

# Usage / Help
function usage() {
    cat <<USAGE_EOF
Usage: $(basename "$0") -n <namespace> -a <app_name> [options]

Automates the recovery of corrupted Ceph RBD PVCs using VolSync / Kopia backups:
  1. Scales down the workload (Deployment or StatefulSet) to 0.
  2. Waits for all application pods to terminate completely.
  3. Drops the corrupted PVC.
  4. Triggers VolSync ReplicationDestination restore and waits for mover completion.
  5. Reconciles Flux Kustomization to recreate the PVC bound from the restore snapshot.
  6. Scales the workload back up to 1 replica and waits for 1/1 Ready status.

Options:
  -n, --namespace <name>    Kubernetes namespace (required)
  -a, --app <name>          Application / Workload name (required)
  -p, --pvc <name>          PersistentVolumeClaim name (default: <app_name>)
  -d, --dst <name>          ReplicationDestination name (default: <app_name>-dst)
  -t, --type <type>         Workload type: 'deployment' or 'statefulset' (default: auto-detect)
      --previous <offset>   Restore previous Kopia snapshot offset (0=latest, 1=1 older, etc.)
      --timeout <seconds>   Maximum wait timeout per step in seconds (default: 300)
  -h, --help                Show this help message

Examples:
  $(basename "$0") -n default -a wg-easy
  $(basename "$0") -n sensitive -a qui
  $(basename "$0") -n communication -a tuwunel -t statefulset
  $(basename "$0") -n sensitive -a qui --previous 1
USAGE_EOF
    exit 0
}

# Defaults
NAMESPACE=""
APP_NAME=""
PVC_NAME=""
DST_NAME=""
WORKLOAD_TYPE=""
PREVIOUS_OFFSET=""
TIMEOUT=300

# Parse CLI arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        -a|--app)
            APP_NAME="$2"
            shift 2
            ;;
        -p|--pvc)
            PVC_NAME="$2"
            shift 2
            ;;
        -d|--dst)
            DST_NAME="$2"
            shift 2
            ;;
        -t|--type)
            WORKLOAD_TYPE="${2,,}"
            shift 2
            ;;
        --previous)
            PREVIOUS_OFFSET="$2"
            shift 2
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            log error "Unknown option: $1"
            ;;
    esac
done

# Validate required inputs
if [[ -z "${NAMESPACE}" || -z "${APP_NAME}" ]]; then
    usage
fi

PVC_NAME="${PVC_NAME:-${APP_NAME}}"
DST_NAME="${DST_NAME:-${APP_NAME}-dst}"

# Ensure required CLIs are present
check_cli kubectl flux

log info "Starting volume restore workflow" "namespace=${NAMESPACE}" "app=${APP_NAME}" "pvc=${PVC_NAME}" "destination=${DST_NAME}"

# Helper: Wait until command succeeds with timeout and sleep interval
function wait_until() {
    local desc="$1"
    local timeout_secs="$2"
    local interval_secs="$3"
    shift 3
    local cmd=("$@")

    log info "Waiting for: ${desc}..." "timeout=${timeout_secs}s"
    local elapsed=0
    while ! "${cmd[@]}" &>/dev/null; do
        if (( elapsed >= timeout_secs )); then
            log error "Timed out waiting for: ${desc}" "elapsed=${elapsed}s"
        fi
        sleep "${interval_secs}"
        elapsed=$(( elapsed + interval_secs ))
    done
    log info "Successfully completed: ${desc}" "duration=${elapsed}s"
}

# --- Step 1: Detect Workload Type & Validate Resources ---
if [[ -z "${WORKLOAD_TYPE}" ]]; then
    if kubectl get deployment -n "${NAMESPACE}" "${APP_NAME}" &>/dev/null; then
        WORKLOAD_TYPE="deployment"
    elif kubectl get statefulset -n "${NAMESPACE}" "${APP_NAME}" &>/dev/null; then
        WORKLOAD_TYPE="statefulset"
    else
        log error "Could not auto-detect workload (Deployment or StatefulSet)" "app=${APP_NAME}" "namespace=${NAMESPACE}"
    fi
fi
log info "Detected workload type" "type=${WORKLOAD_TYPE}"

if ! kubectl get replicationdestination -n "${NAMESPACE}" "${DST_NAME}" &>/dev/null; then
    log error "ReplicationDestination not found" "name=${DST_NAME}" "namespace=${NAMESPACE}"
fi

# --- Step 2: Scale Down Workload & Wait for Pods to Terminate ---
log info "Scaling down ${WORKLOAD_TYPE} to 0 replicas" "app=${APP_NAME}" "namespace=${NAMESPACE}"
kubectl scale "${WORKLOAD_TYPE}" -n "${NAMESPACE}" "${APP_NAME}" --replicas=0

# Clean up any lingering mover pods or jobs
kubectl delete pod -n "${NAMESPACE}" -l "app.kubernetes.io/name=${APP_NAME}" --force --grace-period=0 --ignore-not-found &>/dev/null || true
kubectl delete job -n "${NAMESPACE}" "volsync-src-${APP_NAME}" --ignore-not-found &>/dev/null || true

# Function to check if all workload pods have terminated
function check_pods_terminated() {
    local pod_count
    pod_count=$(kubectl get pods -n "${NAMESPACE}" -l "app.kubernetes.io/name=${APP_NAME}" --no-headers 2>/dev/null | wc -l)
    [[ "${pod_count}" -eq 0 ]]
}
wait_until "workload pods to terminate" "${TIMEOUT}" 2 check_pods_terminated

# --- Step 3: Delete Corrupted PVC & Wait for Removal ---
log info "Deleting corrupted PVC" "pvc=${PVC_NAME}" "namespace=${NAMESPACE}"
kubectl delete pvc -n "${NAMESPACE}" "${PVC_NAME}" --ignore-not-found &>/dev/null || true

function check_pvc_deleted() {
    ! kubectl get pvc -n "${NAMESPACE}" "${PVC_NAME}" &>/dev/null
}
wait_until "PVC deletion" "${TIMEOUT}" 2 check_pvc_deleted

# --- Step 4: Trigger VolSync Restore & Wait for Mover Pod ---
TRIGGER_ID="restore-$(date +%s)"
log info "Triggering VolSync restore" "destination=${DST_NAME}" "trigger=${TRIGGER_ID}"

PATCH_JSON="{\"spec\":{\"trigger\":{\"manual\":\"${TRIGGER_ID}\"}}}"
if [[ -n "${PREVIOUS_OFFSET}" ]]; then
    log info "Setting previous snapshot offset" "offset=${PREVIOUS_OFFSET}"
    PATCH_JSON="{\"spec\":{\"kopia\":{\"previous\":${PREVIOUS_OFFSET}},\"trigger\":{\"manual\":\"${TRIGGER_ID}\"}}}"
fi
kubectl patch replicationdestination -n "${NAMESPACE}" "${DST_NAME}" --type merge -p "${PATCH_JSON}"

# Wait for the mover pod to start
function check_mover_pod_running() {
    local mover_phase
    mover_phase=$(kubectl get pod -n "${NAMESPACE}" -l "volsync.backube/destinationName=${DST_NAME}" -o jsonpath='{.items[0].status.phase}' 2>/dev/null || true)
    [[ "${mover_phase}" == "Running" || "${mover_phase}" == "Succeeded" ]]
}

log info "Waiting for VolSync restore mover pod to initialize"
wait_until "restore mover pod to start" "${TIMEOUT}" 2 check_mover_pod_running

# Stream mover logs in background if mover is running
MOVER_POD=$(kubectl get pod -n "${NAMESPACE}" -l "volsync.backube/destinationName=${DST_NAME}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [[ -n "${MOVER_POD}" ]]; then
    log info "Streaming restore mover logs" "pod=${MOVER_POD}"
    kubectl logs -n "${NAMESPACE}" "${MOVER_POD}" -f --tail=20 2>/dev/null || true
fi

# Wait for ReplicationDestination to complete sync
function check_restore_complete() {
    local last_manual is_syncing
    last_manual=$(kubectl get replicationdestination -n "${NAMESPACE}" "${DST_NAME}" -o jsonpath='{.status.lastManualSync}' 2>/dev/null || true)
    is_syncing=$(kubectl get replicationdestination -n "${NAMESPACE}" "${DST_NAME}" -o jsonpath='{.status.conditions[?(@.type=="Synchronizing")].status}' 2>/dev/null || true)
    
    [[ "${last_manual}" == "${TRIGGER_ID}" && "${is_syncing}" == "False" ]]
}
wait_until "VolSync restore completion" "${TIMEOUT}" 3 check_restore_complete

LAST_SYNC=$(kubectl get replicationdestination -n "${NAMESPACE}" "${DST_NAME}" -o jsonpath='{.status.lastSyncTime}' 2>/dev/null || true)
log info "VolSync restore finished successfully" "lastSyncTime=${LAST_SYNC}"

# --- Step 5: Reconcile Flux Kustomization & Wait for PVC Bound ---
log info "Reconciling Flux Kustomization to recreate PVC" "app=${APP_NAME}" "namespace=${NAMESPACE}"
flux reconcile kustomization -n "${NAMESPACE}" "${APP_NAME}" --with-source &>/dev/null || \
flux reconcile kustomization -n "${NAMESPACE}" "${APP_NAME}" &>/dev/null || true

function check_pvc_bound() {
    local pvc_status
    pvc_status=$(kubectl get pvc -n "${NAMESPACE}" "${PVC_NAME}" -o jsonpath='{.status.phase}' 2>/dev/null || true)
    [[ "${pvc_status}" == "Bound" ]]
}
wait_until "PVC recreation and Bound state" "${TIMEOUT}" 2 check_pvc_bound

# --- Step 6: Scale Workload Up & Verify Pod Readiness ---
log info "Scaling ${WORKLOAD_TYPE} up to 1 replica" "app=${APP_NAME}" "namespace=${NAMESPACE}"
kubectl scale "${WORKLOAD_TYPE}" -n "${NAMESPACE}" "${APP_NAME}" --replicas=1

function check_workload_ready() {
    local ready_replicas
    if [[ "${WORKLOAD_TYPE}" == "deployment" ]]; then
        ready_replicas=$(kubectl get deployment -n "${NAMESPACE}" "${APP_NAME}" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
    else
        ready_replicas=$(kubectl get statefulset -n "${NAMESPACE}" "${APP_NAME}" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
    fi
    [[ "${ready_replicas}" -ge 1 ]]
}
wait_until "workload to reach 1/1 Ready" "${TIMEOUT}" 3 check_workload_ready

log info "Workload is healthy and Ready" "app=${APP_NAME}" "namespace=${NAMESPACE}"

# Display recent application logs
echo ""
echo "=== Recent Container Logs for ${APP_NAME} ==="
kubectl logs -n "${NAMESPACE}" -l "app.kubernetes.io/name=${APP_NAME}" --tail=15 2>&1 || true
echo "============================================="
log info "Volume recovery workflow completed successfully!"
