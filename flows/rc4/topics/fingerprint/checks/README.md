# Check logs

These are the actual output logs from the rootless checks described in the
topic `README.md`.  The large reflinked kernel source/output trees used to
produce them were removed; the logs and the small compiled artifacts remain.

| File | What it proves |
| --- | --- |
| `apply-sequence-final.log` | 0051 then 0055 applied to a fresh reflinked rc3 source copy with `--fuzz=0`; both exit 0. |
| `checkpatch-final.log` | `checkpatch.pl --no-tree --terse --file src/goodix_fp.c` exited 0 with no output (empty file). |
| `check_lock_order.py` | Static lock-order regression plus `--self-test`; rejects synchronous disable/sync under `g->lock`, IRQ-handler `ops_lock`, and `misc_deregister` after `ops_lock`. |
| `lock-order-check.log` | Actual `--self-test` and source-check output from the final driver. |
| `config-merge.log` | `merge_config.sh` merged gate0/gate1/release-name; only the expected `QCOMTEE` and `LOCALVERSION` redefinition notices. |
| `config-olddefconfig.log` | `make ... olddefconfig` exited 0 on the patched source. |
| `config-symbols.txt` | Final symbols, including `QCOMTEE=m`, `INPUT_GOODIX_FP=m`, `TEE=y`, SHMBRIDGE=y, and the deliberately unset binderfs/sg. |
| `config-final.aarch64` | The merged rc4 config produced by `olddefconfig`. |
| `config-final.diff` | Diff from the rc3 config: exactly LOCALVERSION, INPUT_GOODIX_FP and QCOMTEE. |
| `kernelrelease.txt` | `7.0.0-sm8150-guacamole-rc4`. |
| `in-tree-compile.log` | Targeted in-tree build of the driver object and all four guacamole DTBs after the deadlock fix, exit 0. |
| `in-tree-driver-final.log` | Final driver object target checked against the merged rc4 config, exit 0. |
| `module-build-final.log` | `goodix_fp.ko` external build; BTF skipped because the config-only output has no vmlinux. |
| `qcomtee-module-build.log` | `qcomtee.ko` external build against the merged rc4 config, exit 0. |
| `modinfo.txt` | `goodix_fp.ko` vermagic `7.0.0-sm8150-guacamole-rc4`, OF alias and `allow_power` parameter. |
| `dt-fingerprint.txt` | Decoded node and pinctrl from the main DTB; status disabled in all four DTBs. |
| `recipe-check.log` | Synthetic merged recipe passes; untouched rc3 recipe fails with the expected missing additions. |

`config-final.aarch64` is a generated config kept for review, not a shared
recipe edit.  The parent should merge the fragments into the chosen rc4
recipe rather than copying this file over the canonical config.
