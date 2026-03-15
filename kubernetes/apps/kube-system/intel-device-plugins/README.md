# Intel Device Plugins

Deploys the [Intel Device Plugins Operator](https://github.com/intel/intel-device-plugins-for-kubernetes) and a `GpuDevicePlugin` CR for i915 (Intel integrated GPU) access.

## Why Not `intel-gpu-resource-driver`?

The newer `intel-gpu-resource-driver` uses Kubernetes Dynamic Resource Allocation (DRA) and is **not compatible with i915 (Intel iGPU)**. DRA support for i915 is not implemented. The legacy device plugin is still the correct choice for iGPU workloads.

`intel-gpu-resource-driver` is deployed separately for DRA-capable discrete Intel GPUs. This app (`intel-device-plugins`) handles iGPU via the classic `resource.requests`/`resource.limits` device plugin interface.

## Flux Structure

Two Kustomizations are deployed in sequence:

```
intel-device-plugins-operator   →   intel-device-plugins-gpu
```

`intel-device-plugins-gpu` also depends on `intel-gpu-resource-driver` being ready before it installs, ensuring both plugins coexist cleanly.

## Configuration

### Operator (`app/helmrelease.yaml`)

The operator is configured with `devices.gpu: true`, which tells it to watch for `GpuDevicePlugin` CRs and manage the i915 device plugin DaemonSet.

### GPU Plugin (`gpu/helmrelease.yaml`)

| Value | Setting |
|-------|---------|
| `name` | `i915` — targets Intel integrated GPUs |
| `sharedDevNum` | `99` — allows up to 99 pods to share each GPU simultaneously |
| `nodeFeatureRule` | `false` — node feature rules disabled; scheduling is managed manually |

`sharedDevNum: 99` is intentionally high to allow all workloads that can use the iGPU (e.g., transcoding, inference) to access it without contention. The GPU itself arbitrates actual workload scheduling.

## Using the GPU in a Pod

Request the i915 device in your pod spec:

```yaml
resources:
  limits:
    gpu.intel.com/i915: "1"
```

## Notes

- Only nodes with i915 hardware will have the device plugin pod scheduled (the operator uses node feature labels to target correct nodes)
- The control plane nodes have iGPUs and are running workloads (`allowSchedulingOnControlPlanes: true` in Talos patches)
- Worker-5 has an NVIDIA GPU managed separately by `nvidia-device-plugin` / `nvidia-operator`
