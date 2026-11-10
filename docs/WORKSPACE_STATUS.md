# Authoritative workspace and runtime status

2026-11-10: `runtime-workspace` is the consolidated source workspace.
It includes the latest tested-source delta from `nura-rear-cameras-7-0`,
reviewed delivery lifecycle fixes, checkpoint tooling and profiling tooling.
The user requested a local Git checkpoint of these consolidated fixes.
No push, worktree deletion, runtime migration or phone action is included.

The hardware-tested runtime artifacts, privileged chroots and original boot/serve
commands remain in `nura-rear-cameras-7-0`. Do not run packaging in this checkout
expecting those runtime paths to migrate automatically. Recreate/verify local
build inputs, pmbootstrap config, packaging receipts and DELIVERY.json before
using this checkout for another build. Do not copy privileged live chroots.
The original runtime checkout is retained as rollback evidence, not the source
location for new development. The frozen source checkpoint remains outside both.

User confirmed all three rear cameras and recording; recording is choppy.
No measured FPS or regression/suspend result was collected by this swarm.
The dual-boot feasibility report is an investigation, not deployment approval.
No partition write/resize/slot change is prepared or authorized.
The user has deferred local installation: continue fixing usability with netboot,
then revisit direct installation once the system is sufficiently usable.

Future entrypoints: camera_build.sh, camera_serve.sh, camera_boot.sh,
camera_verify.sh; see docs/DELIVERY_LIFECYCLE.md. The smaller rear_camera
launchers are retained as historical fallback. Never refresh a live rootfs.
Checkpoint: docs/WORKING_CAMERA_CHECKPOINT.md. Profiling:
docs/DIAGNOSTIC_PROTOCOL.md. All newly integrated tooling still needs an actual
end-to-end user-run test; source/mock tests alone do not establish hardware health.
