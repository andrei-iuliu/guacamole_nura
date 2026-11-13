# Review copies of the three new drivers

These `.c` files are extracted from the supplied patches **as the rebase leaves
them**, i.e. `sha256` of `src/imx471.c` equals the blob 0053 adds. They exist so
a reviewer can read the code without applying a patch, and so
`checks/static_checks.py` can grep the constants.

| File | From | Notes |
| --- | --- | --- |
| `mxm1120.c` | 0052 | unchanged from 0035, 202 lines, sha256 `ddbafd34…150f` |
| `imx471.c` | 0053 | unchanged from 0036, 1299 lines, sha256 `3522aae4…47df` |
| `hotdog-popup-motor.c` | 0054 | unchanged from 0037, 845 lines, sha256 `f42c597a…ae4b` |

The full hashes are in `../inventory.json`; they were compared against a
byte-extraction of the supplied patches (`checks/logs/` has the run that proves
the rebased series produces the same blobs from a pristine rc3 tree).

No driver code was changed in this rebase. The only edits are the Kconfig and
Makefile insertion points (see `../README.md`), because rc3's series moved the
context lines those hunks quoted.

Verified by `checks/run_all.sh`: applying `../patches/*.patch` to the rc3-patched
sources produces these exact files, and the three objects compile with `W=1`.
