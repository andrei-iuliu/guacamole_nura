# Parent review — 2026-11-12

Status: ready for the USER-RUN isolated build; not ready for userdata writes,
slot selection, boot_b installation or a local-storage hardware trial.

Reviewed the actual entrypoint, rootfs/checker/boot copies, guarded hook recipe,
manifest checks, and integration tests against the working rc3 source.

Confirmed fixed flow-local state/output, two-way overlap guards, mount/lock
checks before copying or cleanup, local kernel-source/output isolation, separate
r4 baseline and r5 candidate hook policies, guarded-hook build before install,
matching installed/archive hooks, and final boot payload/manifest checks.
The real copied rootfs/checker pipeline executes in the synthetic integration
suite; pmbootstrap/admin commands and DTB preparation are replaced with fixtures.
These checks do not establish a real build or hardware readiness.

Parent added optional --authorized-key forwarding and refusal to run the main
entrypoint as root. SSH public keys remain explicitly opt-in. Added a focused
argument-forwarding regression, including paths containing spaces.

Verification: 10 rootless tests pass; Bash syntax checks pass for all four flow
shell scripts; the actual entrypoint dry run succeeds. The three originals in
ORIGIN-SHA256.json were independently verified unchanged. No actual local build,
sudo, mount/unmount, phone command, partition write or flash was performed.

The first local trial deliberately reuses the actual 7.0.0-sm8150-rc3 kernel.
The next rebuilt release is 7.0.0-sm8150-guacamole-rc4; no binaries are relabelled.
After the user build, review DELIVERY.local.json and real package/initramfs
checks. Before any write obtain current slot/userdata/sector facts, verified
boot-chain backups and recovery artifacts, and validate the 4096-sector nested
partition path. The GPU/Plasma failure is independent of NBD removal.
