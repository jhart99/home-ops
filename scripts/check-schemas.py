#!/usr/bin/env python3
import os
import sys

base_kinds = {
    'Binding', 'ComponentStatus', 'ConfigMap', 'Endpoints', 'Event', 'LimitRange', 'Namespace', 'Node',
    'PersistentVolume', 'PersistentVolumeClaim', 'Pod', 'PodTemplate', 'ReplicationController',
    'ResourceQuota', 'Secret', 'ServiceAccount', 'Service', 'ControllerRevision', 'DaemonSet',
    'Deployment', 'ReplicaSet', 'StatefulSet', 'HorizontalPodAutoscaler', 'CronJob', 'Job',
    'CertificateSigningRequest', 'Lease', 'EndpointSlice', 'FlowSchema', 'PriorityLevelConfiguration',
    'Ingress', 'IngressClass', 'NetworkPolicy', 'RuntimeClass', 'PodDisruptionBudget', 'ClusterRole',
    'ClusterRoleBinding', 'Role', 'RoleBinding', 'PriorityClass', 'CSIDriver', 'CSINode',
    'StorageClass', 'VolumeAttachment', 'Kustomization', 'ResourceClaim', 'ResourceClaimTemplate',
    'PodSchedulingContext'
}

apps_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kubernetes", "apps")

failed = False
missing_files = []

for root, dirs, files in os.walk(apps_dir):
    for file in files:
        if file.endswith('.yaml') or file.endswith('.yml'):
            filepath = os.path.join(root, file)
            try:
                with open(filepath, 'r') as f:
                    content = f.read()
                
                # Find top-level apiVersion and kind
                api_version = None
                kind = None
                for line in content.splitlines():
                    if line.startswith('apiVersion:'):
                        api_version = line.split('apiVersion:', 1)[1].strip().strip('"').strip("'")
                    elif line.startswith('kind:'):
                        kind = line.split('kind:', 1)[1].strip().strip('"').strip("'")
                
                if not kind or not api_version:
                    continue
                
                if kind in base_kinds:
                    continue
                
                # Check if it has schema
                has_schema = False
                for line in content.splitlines()[:5]:
                    if 'yaml-language-server: $schema=' in line or '$schema=' in line:
                        has_schema = True
                        break
                
                if not has_schema:
                    print(f"Error: {os.path.relpath(filepath, apps_dir)} ({api_version} / {kind}) is missing a schema comment.")
                    missing_files.append(filepath)
                    failed = True
            except Exception as e:
                print(f"Error checking {filepath}: {e}")

if failed:
    print(f"\nFailed: {len(missing_files)} file(s) are missing a schema comment.")
    sys.exit(1)
else:
    print("Success: All CRD YAML files under kubernetes/apps/ have a schema reference.")
    sys.exit(0)
