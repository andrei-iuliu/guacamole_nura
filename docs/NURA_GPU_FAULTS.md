# GPU faults: evidence and candidates for the next RC

Research updated 2026-11-14. **Proposal only: no GPU patch, refresh-rate
change or persistent renderer override was applied by this investigation.**
The one kernel-side candidate, the 64 KiB managed-VA alignment, is now
recipe patch 0073 in the next RC candidate (see "rc4j candidate" below); it
is experimental and not a validated default.

Recommendation: review a small Phoc thumbnail guard as a separate recovery
fix, and test the matching 64 KiB managed-VA alignment proposal as an
experimental kernel candidate. A full-session `sysmem` trial is the smallest
workaround experiment. None is yet a validated default for the next RC.

## Runtime baseline

OnePlus 7 Pro (guacamole), SM8150 / Adreno 640, local-userdata Phosh:

| Component | Recorded version/state |
| --- | --- |
| Boot ID | `REDACTED_BOOT_ID` |
| Kernel | `7.0.0-sm8150-guacamole-rc4` |
| Installed kernel APK | `7.0.0_p20261114110819-r0` |
| Installed device APK | `4-r17` (the later charge-CLI source is 4-r18) |
| Mesa | `26.2.6-r0` |
| Phoc | `0.58.0-r0` |
| Phosh | `99990.58.0-r0` |
| GTK | `4.24.1-r0` |
| Display | `DSI-1`, 1440×3120, **90 Hz current/preferred**; 60 Hz also advertised |

The kernel release string is shared by several builds; identify trial
images/modules by hashes, not uname alone. The current recipe already has
0068–0071 GPU recovery backports. They do not prevent the measured faults.

## What was measured

### GPU fault and graphical-session crash

Kernel reports READ/TRANSLATION faults from CCU, then hangcheck recovery.
The named submitters include GNOME Settings, Terminal (`kgx`), Phoc, GNOME
Software and Files (`nautilus`). A submitter name identifies the queued work;
it does not establish which component introduced an invalid address.

At 10:45:28 UTC Terminal faults, Phoc follows at 10:45:31, and Phoc takes
SIGSEGV at 10:45:32. The graphical session ends and the greeter returns;
Linux stays running. Four GPU dumps were decoded with Mesa 26.2.6 crashdec:

| Submit | Fault address | Submitted buffer starts | Render target base |
| --- | --- | --- | --- |
| Settings | `0x103d80000` | `0x103d8e000` | `0x103d8f000` |
| Phoc, first | `0x10ee60000` | `0x10ee65000` | `0x10ee65000` |
| Terminal | `0x101bc0000` | `0x101bc2000` | `0x101bc6000` |
| Phoc, final | `0x102020000` | `0x102022000` | `0x102022000` |

In every row, rounding the render target down to a 64 KiB boundary produces
the fault address. It falls below the buffer and outside every submitted BO
range listed in that dump. Raw BO lists and decoded RB_MRT/resolve addresses
were checked. This strongly supports an alignment/overfetch or mapping-life
problem; it does not by itself assign the defect to Mesa versus the kernel.
Settings has multiple pipelined targets; a decoder's estimated stop is not
the exact instruction which initiated an earlier memory request.

All captured dumps were untruncated. crashdec matched its bundled reference
test exactly. It emitted control-register enum warnings; the Terminal dump
also lacked one older command buffer. Those limits are preserved with the
decoded output rather than treating the trace as a complete execution log.

### A specific secondary crash site is now symbolized

The installed Phoc executable and build-root copy have identical SHA256
`1645e7abf59ca98adf4a0a5652d4044dc7a2bb4f67014e74d918532b18b48245`.
The official Alpine `phoc-dbg-0.58.0-r0.apk` debug binary matches build ID
`c3839279103c700ce86cbac34247cb8444e1a8aa`.

Resolving offsets from the saved crash gives:

| Offset | Function / source |
| --- | --- |
| `0x5d3b8` | `wlr_render_pass_add_texture`, wlroots `render/pass.c:23` |
| `0x46e54` | `view_render_to_buffer_iterator`, Phoc `src/render.c:536` |
| `0x8b794` | `surface_for_each_surface`, wlroots `types/wlr_compositor.c:1159` |
| `0x3f198` | `phoc_renderer_render_view_to_buffer` / `thumbnail_frame_handle_copy`, Phoc `src/render.c:590` / `src/phosh-private.c:456` |

The faulting instruction reads the texture width through a texture pointer.
The thumbnail iterator obtains a texture without checking it for NULL, while
another thumbnail iterator in this source already performs that check.
The crash path is confirmed; a NULL pointer specifically is still an
inference because core register values were not inspected.

A community patch from a related A640 phone guards this exact iterator.
It applies to our local Phoc 0.58.0 source copy with zero fuzz and a 99-line
offset. An assertion verified the guard precedes the rendering call. This
was a source/apply check, not a compile or hardware test.

### rc4j: the guard is now a local recipe (Phosh image only)

Branch `rc4j-phoc` turns that reference patch into a recipe:
`flows/rc4/device/testing/phoc` is Alpine `community/phoc` 0.58.0-r0 at
`pkgrel=100` plus
`0001-render-skip-surfaces-that-have-no-texture.patch` (author attribution
kept; the 0.56.0 hunk `-413,6` is refreshed to `-512,6`, and it now applies to
the 0.58.0 tarball with `--fuzz=0` and **no** offset — the 99-line offset above
is gone). The flow builds it only for `NURA_UI=phosh`, installs
`phoc=0.58.0-r100`, verifies that package version in the installed rootfs and
rejects an installed `/usr/bin/phoc` whose SHA256 is the unpatched Alpine
binary (`1645e7ab…b48245`, the build the crash was symbolized against).
`abuild`/`meson` were not run, so this is still an unbuilt source recipe; see
docs/NURA_PHOSH.md §2.1.

**Scope, stated plainly: this prevents the session crash on this path, it does
not prevent GPU faults.** The kernel still faults, the renderer can still
corrupt app content and thumbnails, and a surviving but damaged desktop is not
a repaired GPU. It changes the outcome of the post-fault thumbnail render from
SIGSEGV to a skipped surface. Phosh is not rebuilt: phoc 0.58.0 builds its
`libphoc` as a static library linked into the `phoc` executable and installs
no shared library, so phosh depends on the `phoc` **package** and talks to it
over Wayland/phosh-private. Our phoc is built after gmobile because its
`makedepends` include `gmobile-dev`.

### Scoped sysmem smoke test

Around 11:02:32–11:05:11 UTC, a separate Terminal instance ran with
`FD_MESA_DEBUG=sysmem` and a unique GApplication ID. No new GPU faults,
context-loss messages or compositor restart were observed during that short
interval. The window could be closed through its long-press menu.

This was not a repeated-workload A/B, did not affect other applications or
Phoc, and does not prove the whole session stable. The swipe-to-close report
was not established as a graphics regression.

### Content flicker at 90 Hz

At 11:52:46–11:53:55 UTC, the later kernel query shows eight recoveries
(six GNOME Software, two Nautilus), 39 fault messages and 13 distinct fault
addresses. All addresses are 64 KiB aligned; these newer dumps were not
decoded. The user described flicker inside app content.

GetCurrentState confirms 90 Hz. There is no 60/90 Hz comparison yet, so a
refresh-dependent trigger is not excluded. The queried slice shows GPU
faults without a DSI/vblank timeout or display underflow; that is stronger
evidence for the rendering fault than a panel-timing diagnosis. No claim is
made that 60 Hz fixes it.

## Upstream research and candidates

| Candidate | Evidence / scope | Next-RC decision |
| --- | --- | --- |
| Phoc null-texture thumbnail guard | A community patch checks the missing texture before rendering; our symbolized crash uses that exact path. May avoid session death after a reset, while content can remain damaged. | Local recipe `flows/rc4/device/testing/phoc` (0.58.0-r100) wired into the Phosh image (rc4j); still unbuilt and untested on hardware. It does not prevent GPU faults. |
| 64 KiB kernel-managed GPU VA alignment | A 9 November report on another A640/SM8150 device describes the same boundary-access pattern and reports fault-free trials after alignment. Our source still uses 4 KiB PAGE_SIZE alignment. | Strongest matching kernel experiment, not an approved default. Preserve PAGE_SIZE fallback on ENOSPC for tight GMU windows. Carried as 0073 for the GL/kernel-managed path; Vulkan/Turnip allocates its own VA and is not covered (see the rc4j section). |
| `FD_MESA_DEBUG=sysmem` | Supported by our exact Mesa source; GPU-accelerated system-memory rendering. Short app-only smoke test was quiet. | Test whole-session scope before considering a reversible workaround. Measure power/performance rather than assuming no cost. |
| `FD_MESA_DEBUG=inorder` | Supported by our Mesa source; the external matching reporter says ordered submission suppresses their fault. | Separate diagnostic trial. Never combine with sysmem for the initial comparison. |
| Mesa 26.3.0 | Official release source and relevant MSM allocation path were inspected. No confirmed fix for this exact defect identified. | Separate upgrade experiment if needed; do not advertise as a known repair. |
| 60 Hz | Advertised by the driver, but no A/B done. | Control experiment; not a GPU-memory fix on present evidence. |

The external kernel proposal was questioned by Rob Clark as masking a
userspace bug; he requested a Mesa report and GPU dump. No accepted/merged
fix or revised version was verified by this research. GitLab issue/main
source access was unavailable through the web tool, limiting status checks.

**Use `sysmem`, not `nogmem`.** The external report mentions nogmem, but
that flag is absent from our 26.2.6 source. The earlier nogmem suggestion was
not run. Phoc recovery and GPU fault prevention are separate inclusion items.

### rc4j candidate: recipe patch 0073 (64 KiB kernel-managed GPU VA)

The next RC's kernel recipe carries the alignment proposal as patch
`0073-drm-msm-align-kernel-managed-gpu-va-to-64k.patch` (`Nura-Category:
experimental`, `Nura-Origin: freedreno list 2026-11-09 msg48724`, maintainer
reply msg48725). It is one hunk in `msm_gem_vma_new()`: a kernel-managed
object is inserted into the VM's `drm_mm` with `SZ_64K` alignment, and the
insert is retried with `PAGE_SIZE` when the aligned attempt returns
`-ENOSPC`. The retry is what keeps the GMU working: `gmu->vm` is a managed VM
and three of its allocations use exact windows (`dummy`
`[0x60000000,0x60001000)`, `icache` `[0x4000,0x40000)`, `dcache`
`[0x44000,0x80000)`) in which no 64 KiB-aligned start fits. User (VM_BIND)
VMs are created with `managed=false` and never enter that branch, so no
userspace address changes. Mesa, Phoc, Phosh and the panel mode are
untouched.

**Which path did the dumps use?** The measured buffers are on the
kernel-managed path, so 0073 does reach them. gallium freedreno in Mesa
26.2.6 has no VM_BIND code at all: every BO's iova comes from
`MSM_INFO_GET_IOVA` (`msm_bo.c`, called from `fd_bo_init_common()`), and the
kernel returns `EINVAL` for that ioctl once a context has opted in to
VM_BIND (`msm_drv.c:433`); `msm_bo_iova()` swallows that error and
returns 0, so gallium in that mode would give every BO address 0 and could
not render; a working GL session therefore proves the managed path.
The dumps' addresses (`0x101bc0000`–`0x10ee60000`) sit inside the per-process
window `[0x100000000, BIT(48))` that the dmesg's `Stage-1: 48-bit VA`
implies, but that window is shared with userspace-managed VMs
(`a6xx_create_private_vm()` uses `ADRENO_VM_START` in both modes), so the
address alone proves nothing; the gallium code does.

Turnip (Vulkan) is the exception and is **not** covered by 0073: it enables
VM_BIND whenever the kernel offers it, has no knob to disable it, and
allocates its own VAs from a `util_vma_heap` with page (4 KiB) alignment. An
unbuilt, unwired proposal for that path is
`flows/rc4/topics/gpu-va-align/mesa/0001-tu-align-userspace-va-of-large-bos-to-64k.patch`;
no measured submitter is a Vulkan client, so it is not needed for these
faults. Full transcripts, line references and the falsification criteria are
in `flows/rc4/topics/gpu-va-align/README.md`.

Verified for the recipe only: the patch applies to the 0001-0072 tree with
`patch -p1 --fuzz=0` (no offsets) and `drivers/gpu/drm/msm/msm_gem_vma.o`
compiles with no diagnostics at default and at `W=1`. Nothing was booted.

How to A/B it, both arms with the same Mesa 26.2.6, Phoc 0.58.0, Phosh and
display mode, normal GMEM rendering, and no `sysmem`/`inorder` in either arm:

1. Two temporary boot images from the same recipe: `pkgrel=13` with 0073 and
   `pkgrel=12` without it. The release string is identical, so label images
   and module trees by hash and install the matching modules per arm.
2. Run the fixed workload from item 2 below in each arm, at least three
   logins per arm, and record per-login fault count, fault addresses,
   recoveries, userspace cores and visible damage.
3. Compare: the hypothesis predicts that the faults at
   `round_down(target, 64K)` disappear in the 0073 arm and remain in the
   control arm. A residual fault set, a GMU probe failure (`-ENOSPC` in
   dmesg) or any boot/suspend regression rejects the arm.
4. Sanity-check the arm from the dumps: after 0073 every kernel-managed BO
   start is a multiple of `0x10000`. A render-target BO that is not 64 KiB
   aligned can only come from a userspace-VA (VM_BIND, i.e. Vulkan) context,
   which this patch does not cover.

## Proposed validation before inclusion

1. Preserve the current baseline hashes, matching modules, package versions,
   boot ID, renderer settings and display mode. Keep rollback artifacts.
2. Repeat a fixed workload: launch/scroll Settings, Software, Files and
   Terminal; open/close the overview; create thumbnails; lock/unlock.
   Count faults, recoveries and userspace cores, and note visible damage.
3. Compare 60 and 90 Hz with the renderer unchanged. Separately compare
   normal rendering, sysmem and inorder, restarting affected processes so
   old D-Bus instances cannot retain their earlier environment.
4. Test the Phoc guard independently: it should eliminate the demonstrated
   thumbnail SIGSEGV without hiding continued GPU faults. Confirm that app
   content and thumbnails recover; a surviving but corrupted desktop is not
   complete recovery. The rc4j recipe only makes this testable: the guard was
   verified to compile into the source patch, not to run on the phone.
5. Test VA alignment in an isolated kernel build with matching modules,
   normal GMEM enabled and unchanged Mesa/Phoc. Validate GPU/GMU startup,
   suspend/resume and constrained allocations. Use temporary boot first.
6. Proposed acceptance workload: at least three fresh logins, ten
   lock/suspend/resume cycles and a 30-minute mixed-app run without new
   GPU faults, session loss or visible corruption. These are proposed
   criteria, not completed measurements or a proof of universal stability.

Only the candidates which pass their relevant checks should enter the next
RC. Label an optional sysmem workaround as such, keep a rollback path, and
continue collecting devcoredumps. A kernel change needs recipe/checksum,
image, DTB/module identity and rootfs verification updates together.

## Sources and reproducible evidence

Primary external sources, accessed 2026-11-14:

- [Original managed-VA alignment proposal](https://www.mail-archive.com/freedreno@lists.freedesktop.org/msg48724.html).
- [Maintainer response](https://www.mail-archive.com/freedreno@lists.freedesktop.org/msg48725.html).
- [Mesa capture/decoder and diagnostic options](https://docs.mesa3d.org/drivers/freedreno.html).
- [Community Phoc guard](https://github.com/alexandr-mironov/poco-x3-pro-postmarketos/blob/main/patches/upstreamable/0003-phoc-render-skip-surfaces-without-a-texture.patch), content blob `f98bf99c1d19e818aa8d3705e8f4b38e5d8d4c5d`, originally tested on 0.56.0.
- [Community hardware account](https://github.com/alexandr-mironov/poco-x3-pro-postmarketos/blob/main/README.md): related hardware corroboration, not guacamole validation.
- [Matching Alpine debug package](https://dl-cdn.alpinelinux.org/alpine/edge/community/aarch64/phoc-dbg-0.58.0-r0.apk).
- Official Mesa source archives: [26.2.6](https://archive.mesa3d.org/mesa-26.2.6.tar.xz), [26.3.0](https://archive.mesa3d.org/mesa-26.3.0.tar.xz).

Local private evidence is under `bringup-notes/`:

- `crash-20261114/`: archive, four decoded GPU traces, arithmetic assertions
  in buffer-comparison.tsv, decoder reference check and smoke-test results.
- `flicker-review-20261114/`: recent fault logs and DisplayConfig output.
- `gpu-next-rc/`: runtime identity, symbolized stack, matching debug package,
  the reference Phoc patch and copy-only apply check; SHA256SUMS records
  relevant artifacts. No reference patch was applied to a build recipe there.
  Branch `rc4j-phoc` now carries the refreshed patch in
  `flows/rc4/device/testing/phoc` (phoc 0.58.0-r100, Phosh image only).

Raw dumps may contain rendered application data. They remain local; inspect
before sharing with an upstream report. No issue or message was posted.
