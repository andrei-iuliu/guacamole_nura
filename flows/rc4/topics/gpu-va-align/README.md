# rc4 topic: GPU VA alignment — which VA path do the fault dumps use?

Status: investigation closed for the measured faults.  Answer: the GL stack
that produced the dumps gets its GPU addresses from the kernel
(`vm->managed`), so recipe patch 0073 does reach its render targets.  Turnip
(Vulkan) does use userspace VA and is **not** covered by 0073; a proposed,
unbuilt Mesa patch for that path is in `mesa/` and is not wired into any
build.  Nothing here was run on a phone.

## The question

0073 (`device/testing/linux-postmarketos-qcom-sm8150/`, pkgrel 13) changes
only the kernel-managed branch of `msm_gem_vma_new()`.  If Mesa 26.2.6
freedreno enables VM_BIND (`MSM_PARAM_EN_VM_BIND` /
`DRM_IOCTL_MSM_VM_BIND`), userspace allocates the VA itself and 0073 would be
a no-op for the buffers in the dumps.

## Answer in one table

| Client | VA picked by | Affected by 0073 |
| --- | --- | --- |
| Phoc, GTK4 apps (`gnome-control-center`, `kgx`, Nautilus, …) via gallium/GL | kernel, `msm_gem_vma_new()` → `drm_mm` | **yes** — every BO, render targets included |
| kernel BOs on the same VM (ringbuffers, memptrs, …) | kernel | yes |
| GMU (`gmu->vm`) | kernel | log/HFI 64 KiB aligned, `dummy`/`icache`/`dcache` keep page alignment via the `-ENOSPC` retry |
| Turnip (Vulkan, `has_vm_bind`) | userspace `util_vma_heap`, 4 KiB alignment | **no** — see `mesa/` below |
| `freedreno/decode` replay / `drm-shim` | its own fake heap | n/a |

So: the dumps' buffers are on the kernel-managed path and 0073 applies to
them; a Vulkan client would be outside 0073's reach.

## Provenance

* `mesa-26.2.6.tar.xz`, sha256
  `b9436b3e1a8be76fbd4823dc884f90596ab7c6ac5e1867a305e66ffccfb31a92`,
  downloaded from `https://archive.mesa3d.org/mesa-26.2.6.tar.xz`.  The copy
  used for the 14 November decode
  (`bringup-notes/crash-20261114/mesa-26.2.6.tar.xz`)
  has the same sha256, so the findings describe the Mesa that produced the
  dumps.
* Runtime the dumps came from
  (`bringup-notes/gpu-next-rc/runtime-identity.txt`,
  `.../crash-20261114/capture-and-packages.txt`): `mesa-26.2.6-r0`,
  `mesa-dri-gallium`, `mesa-egl`, `mesa-gbm`, `mesa-gl`, `mesa-gles`,
  `gtk4.0-4.24.1-r0`, `phoc-0.58.0-r0`, `phosh-99990.58.0-r0`; no
  `mesa-vulkan-*` package is listed.  Kernel
  `[drm] Initialized msm 1.13.0` — the driver *can* do VM_BIND, which is not
  the same as a client using it.

## Finding 1 — gallium freedreno has no VM_BIND code at all

Only the Vulkan driver and the `drm-shim` mention VM_BIND; the gallium DRM
backend and the gallium driver do not:

```
$ grep -rln "VM_BIND\|vm_bind" src/freedreno src/gallium/drivers/freedreno
src/freedreno/drm-shim/freedreno_noop.c
src/freedreno/vulkan/tu_device.cc
src/freedreno/vulkan/tu_device.h
src/freedreno/vulkan/tu_knl_drm.cc
src/freedreno/vulkan/tu_knl_drm_msm.cc
src/freedreno/vulkan/tu_knl_drm_virtio.cc
```

`src/freedreno/drm/msm/` is the whole gallium backend (1653 lines:
`msm_bo.c`, `msm_device.c`, `msm_pipe.c`, `msm_priv.h`, `msm_ringbuffer.c`,
`msm_ringbuffer_sp.c`).  It

* never sets `MSM_PARAM_EN_VM_BIND` (`msm_pipe_set_param()` only handles
  `MSM_PARAM_SYSPROF`, `msm_pipe.c:105-114`),
* creates submitqueues with flags `0` or `MSM_SUBMITQUEUE_ALLOW_PREEMPT`,
  never `MSM_SUBMITQUEUE_VM_BIND` (`msm_pipe.c:142-180`),
* never calls `MSM_INFO_SET_IOVA` (grep: no hit in `src/freedreno` or
  `src/gallium/drivers/freedreno`), and
* asks the kernel for every address:
  `msm_bo_iova()` → `MSM_INFO_GET_IOVA` (`msm_bo.c:85-98`), wired as
  `.iova = msm_bo_iova` (`msm_bo.c:180`), called once per BO from
  `fd_bo_init_common()`: `bo->iova = bo->funcs->iova(bo);`
  (`freedreno_bo.c:82`).

There is no VA heap in the gallium path; the only `util_vma_heap` users in
`src/freedreno` outside Vulkan are `decode/replay.c` and the BO-suballocation
heap in `drm/freedreno_bo_heap.c` (an offset heap inside one BO, not VA).

There is also no knob to disable VM_BIND in gallium because there is nothing
to disable: the whole `FD_MESA_DEBUG` table
(`gallium/drivers/freedreno/freedreno_screen.c:51-85`, 32 options from
`msgs` to `abort`) has no VA/VM entry.

## Finding 2 — a VM_BIND context cannot serve gallium at all

The kernel rejects `MSM_INFO_GET_IOVA` and `MSM_INFO_SET_IOVA` once the
context has opted in:

```
msm_drv.c:433   return UERR(EINVAL, dev, "VM_BIND is enabled");
msm_drv.c:457   return UERR(EINVAL, dev, "VM_BIND is enabled");
```

The opt-in is per DRM file (`msm_context.userspace_managed_vm`,
`adreno_gpu.c:498-510`, and the VM is created lazily with
`!ctx->userspace_managed_vm` as `kernel_managed`, `msm_drv.c:236-238`).  So
Turnip enabling VM_BIND on its own fd cannot change Phoc's or a GTK app's
context, and a gallium client inside a VM_BIND context would get iova 0 for
every BO — `msm_bo_iova()` returns 0 instead of reporting the error
(`msm_bo.c:93-97`) — so it could not render at all.  A working GL session
whose dumps carry sensible BO addresses is therefore proof of the managed
path, not just evidence.

## Finding 3 — cross-check against the decoded dumps

Per-process window on this kernel: `ADRENO_VM_START = 0x100000000`
(`adreno/adreno_gpu.h:607`) up to `BIT(ias)`
(`adreno_private_vm_size()`, `adreno/adreno_gpu.c:229-254`).  The GPU SMMU
reports `Stage-1: 48-bit VA -> 48-bit IPA`
(`bringup-notes/crash-20261114/kernel-and-crash.txt:337`),
so the window is `[0x100000000, 0x1000000000000)`, 256 TiB − 4 GiB.

| Dump | Fault address | Render-target base | In window? |
| --- | --- | --- | --- |
| Settings | `0x103d80000` | `0x103d8f000` | yes, 4.06 GiB |
| Phoc, first | `0x10ee60000` | `0x10ee65000` | yes, 4.23 GiB |
| Terminal | `0x101bc0000` | `0x101bc6000` | yes, 4.03 GiB |
| Phoc, final | `0x102020000` | `0x102022000` | yes, 4.03 GiB |

**This alone does not discriminate the two modes.** `a6xx_create_private_vm()`
uses `ADRENO_VM_START` and the same size for the managed and user VM
(`a6xx_gpu.c:2516-2527`), and `MSM_PARAM_VA_START`/`VA_SIZE` report exactly
that window (`adreno_gpu.c:418-427`), so Turnip's heap starts at the same
base and also hands out addresses just above 4 GiB.  What discriminates is
Findings 1+2: those clients are gallium GL clients, and gallium cannot run
in a VM_BIND context.

Useful consequence for the next dump: after 0073, **every kernel-managed BO
starts on a 64 KiB boundary**, so a BO start that is not a multiple of
`0x10000` in a future dump can only come from a userspace-VA (VM_BIND)
context.  Before 0073 all managed BOs are 4 KiB packed, which is why the
current dumps cannot make that distinction.

## Finding 4 — Turnip does use userspace VA, at 4 KiB

* `tu_knl_drm_msm.cc:1624` `device->has_vm_bind = tu_try_enable_vm_bind(fd) == 0;`
  during physical-device setup, and `msm_device_init()` enables it
  unconditionally when true (`tu_knl_drm_msm.cc:261-267`); failure to enable
  aborts device creation.
* No environment or debug knob disables it: the `TU_DEBUG` flag list
  (`tu_util.h:40-78`, bits 0-37) has no VM entry, and `has_vm_bind` comes
  only from the probe.
* The userspace heap is `util_vma_heap` over
  `[va_start, va_start + va_size)` (`tu_device.cc:2910-2911`), and every
  allocation except a client-specified replay address uses
  `util_vma_heap_alloc(&dev->vma, size, os_page_size)`
  (`tu_knl_drm.cc:39` and `:43`) with `os_page_size` = 4096
  (`tu_device.cc:57`, set from `os_get_page_size()` at `:1428`).

So a Vulkan client on this image keeps the 4 KiB VA packing that 0073
removes for GL clients.  `mesa/0001-tu-align-userspace-va-of-large-bos-to-64k.patch`
is the matching userspace change: BOs of 64 KiB or more get a 64 KiB heap
alignment; smaller BOs and client-supplied addresses are unchanged.  It is
**not built, not run, and not wired into any recipe or flow** — the measured
faults are GL faults and do not need it.

## Reproduce

```sh
d=$(mktemp -d); cd "$d"
curl -sSLO https://archive.mesa3d.org/mesa-26.2.6.tar.xz
sha256sum mesa-26.2.6.tar.xz      # b9436b3e...ffccfb31a92
tar -xf mesa-26.2.6.tar.xz mesa-26.2.6/src mesa-26.2.6/include
cd mesa-26.2.6
grep -rln "VM_BIND\|vm_bind" src/freedreno src/gallium/drivers/freedreno
grep -n "iova\|MSM_\(INFO\|PARAM\)" src/freedreno/drm/msm/msm_bo.c
grep -n "os_page_size" src/freedreno/vulkan/tu_knl_drm.cc
patch -p1 --fuzz=0 --dry-run < "$NURA_REPO"/flows/rc4/topics/gpu-va-align/mesa/0001-tu-align-userspace-va-of-large-bos-to-64k.patch
```

## What would change the conclusion

* A dump after 0073 whose render-target BO starts are not 64 KiB aligned —
  that buffer came from a userspace VA context, and the GL-path claim above
  would have to be revisited.
* A future Mesa release that adds VM_BIND to the gallium backend (nothing
  suggests one; the gallium DRM backend has no VM code today).
* Evidence that a faulting submitter ran with `GSK_RENDERER=vulkan` and a
  Turnip/Vulkan stack on this image.  The runtime identity lists only the
  GL/EGL/GBM Mesa packages, and the named submitters are GL clients.
