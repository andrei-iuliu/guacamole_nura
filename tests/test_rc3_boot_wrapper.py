"""Exercise wrapper-before-packer ordering with fake host tools only."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Rc3BootWrapperTests(unittest.TestCase):
    def test_wrapper_is_prepared_from_rc3_inputs_before_packing(self):
        script = (ROOT / 'scripts/rootfs_prepare_plasma.sh').read_text()
        function = script.split('pack_boot_image() {', 1)[1].split('\nwrite_kernel_manifest()', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'rootfs/export').mkdir(parents=True)
            (root / 'rootfs/export/initramfs').write_bytes(b'fixture')
            (root / 'output').mkdir()
            env = dict(os.environ, FIXTURE=str(root))
            shell = '''set -euo pipefail
ROOT="$FIXTURE"; STATE="$FIXTURE"; KERNEL_OUT="$FIXTURE/output"
KERNEL_SOURCE="$FIXTURE/source"; RELEASE=7.0.0-sm8150-rc3
note() { :; }
die() { echo "$*" >&2; exit 1; }
sha256() { echo fixturehash; }
json_get() { echo fixturehash; }
python3() {
    [[ "$1" = "$ROOT/scripts/prepare_temporary_dtb.py" ]]
    [[ "$*" != *--verify* ]]
    [[ "$*" = *"--base-dtb $KERNEL_OUT/kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb"* ]]
    [[ "$*" = *"--base-source-dts $KERNEL_SOURCE/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dts"* ]]
    mkdir -p "$KERNEL_OUT/temporary-dtbo"
    echo prepared > "$KERNEL_OUT/temporary-dtbo/manifest.json"
}
bash() {
    [[ -f "$TEMP_DTB_DIR/manifest.json" ]]
    [[ "$BASE_DTB" = "$KERNEL_OUT/kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb" ]]
    [[ "$INITRAMFS" = "$STATE/rootfs/export/initramfs" ]]
    touch "$KERNEL_OUT/boot.img"
}
pack_boot_image() {''' + function + '\npack_boot_image\n'
            result = subprocess.run(['bash', '-c', shell], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((root / 'output/boot.img').exists())
