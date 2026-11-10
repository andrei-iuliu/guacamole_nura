"""Focused, rootless tests for the checkpoint tool.

These tests use temporary directories and a local git repository.  They never
call sudo, mount, pmbootstrap, adb, fastboot, a phone or a network service.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts/checkpoint_working_camera.py"
spec = importlib.util.spec_from_file_location("checkpoint_working_camera", SCRIPT)
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)


def write(path: Path, data: bytes = b"x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_snapshot(snapshot: Path, patch: str, files: dict[str, bytes]) -> None:
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "working-source.patch").write_text(patch)
    for relative, data in files.items():
        write(snapshot / "untracked" / relative, data)
    sums = {"working-source.patch": sha(snapshot / "working-source.patch")}
    for relative in files:
        sums[f"untracked/{relative}"] = sha(snapshot / "untracked" / relative)
    (snapshot / "SHA256SUMS.json").write_text(json.dumps(sums, indent=2) + "\n")


class HashCacheTests(unittest.TestCase):
    def test_hash_cache_reuses_until_metadata_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = write(Path(temporary) / "file", b"one")
            cache = {}
            first, cached = cp.hash_file(path, cache)
            self.assertFalse(cached)
            second, cached = cp.hash_file(path, cache)
            self.assertTrue(cached)
            self.assertEqual(first, second)
            path.write_bytes(b"two")
            third, cached = cp.hash_file(path, cache)
            self.assertFalse(cached)
            self.assertNotEqual(first, third)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_hashes_and_extra_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "snapshot"
            make_snapshot(snapshot, "patch\n", {"docs/x.md": b"x\n"})
            cache = {}
            info = cp.verify_snapshot(snapshot, cache, rehash=False)
            self.assertTrue(info["verified"])
            self.assertEqual(info["entries"], 2)
            write(snapshot / "untracked" / "extra", b"extra")
            info = cp.verify_snapshot(snapshot, cache, rehash=True)
            self.assertFalse(info["verified"])
            self.assertIn("untracked/extra", info["extra_on_disk"])

    def test_snapshot_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "snapshot"
            make_snapshot(snapshot, "patch\n", {"a": b"a\n"})
            (snapshot / "untracked" / "a").write_bytes(b"tampered\n")
            info = cp.verify_snapshot(snapshot, {}, rehash=True)
            self.assertFalse(info["verified"])
            self.assertTrue(info["mismatches"])


class SourceTests(unittest.TestCase):
    def make_repo(self, root: Path) -> tuple[str, str]:
        root.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.invalid",
               "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        (root / "tracked.txt").write_text("base\n")
        subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True, env=env)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        (root / "tracked.txt").write_text("patched\n")
        patch = subprocess.check_output(["git", "diff", "HEAD"], cwd=root, text=True)
        return head, patch

    def test_patch_applies_and_untracked_matches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            head, patch = self.make_repo(root)
            snapshot = Path(temporary) / "snapshot"
            make_snapshot(snapshot, patch, {"new.txt": b"new\n"})
            write(root / "new.txt", b"new\n")
            out = Path(temporary) / "out"
            info, failures, warnings = cp.verify_source(
                root, snapshot, out, {}, rehash=False, expected_head=head)
            self.assertEqual(failures, [])
            self.assertEqual(warnings, [])
            self.assertTrue(info["patch"]["applies"])
            self.assertTrue(info["patch"]["matches_worktree"])
            self.assertTrue(info["untracked"]["matches_worktree"])

    def test_worktree_drift_is_reported_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            head, patch = self.make_repo(root)
            snapshot = Path(temporary) / "snapshot"
            make_snapshot(snapshot, patch, {"new.txt": b"new\n"})
            write(root / "new.txt", b"different\n")
            write(root / "tracked.txt", b"different\n")
            info, failures, warnings = cp.verify_source(
                root, snapshot, Path(temporary) / "out", {}, rehash=False,
                expected_head=head)
            self.assertEqual(failures, [])
            self.assertTrue(info["patch"]["applies"])
            self.assertFalse(info["patch"]["matches_worktree"])
            self.assertFalse(info["untracked"]["matches_worktree"])
            self.assertEqual(len(warnings), 2)


class ArtifactTests(unittest.TestCase):
    def fixture(self, temporary: Path) -> tuple[Path, dict]:
        root = temporary / "root"
        build = root / "build/rear-camera"
        files = {
            "image": write(build / "Image", b"image\n"),
            "boot_img": write(build / "boot.img", b"boot\n"),
            "kernel_package": write(build / "kernel-package/boot/vmlinuz", b"kernel\n"),
            "dtb": write(build / "kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb",
                         b"dtb\n"),
            "initramfs": write(root / ".work/rear-camera/rootfs/export/initramfs",
                               b"initramfs\n"),
            "rootfs_image": write(
                root / ".work/rear-camera/pmbootstrap-work/chroot_native/home/pmos/rootfs/"
                       "oneplus-guacamole.img", b"packaged-rootfs\n"),
            "served": write(
                root / ".work/rear-camera/pmbootstrap-work/images_netboot/"
                       "oneplus-guacamole.img", b"guest-written-cache\n"),
            "cci": write(
                root / "build/rear-camera/kernel-package/usr/lib/modules/"
                       "7.0.0-sm8150-rear1/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst",
                b"future-cci-content-not-todays-hash\n"),
        }
        write(build / "kernel-package/usr/share/kernel/postmarketos-qcom-sm8150/"
                     "kernel.release", b"7.0.0-sm8150-rear1\n")
        write(build / "temporary-dtbo/custom_guacamole-abl-quarantine.dtb", b"wrapper\n")
        write(build / "temporary-dtbo/manifest.json", json.dumps({
            "artifacts": {"wrapper": {
                "path": "build/rear-camera/temporary-dtbo/custom_guacamole-abl-quarantine.dtb",
                "sha256": sha(build / "temporary-dtbo/custom_guacamole-abl-quarantine.dtb"),
                "size": 8,
            }},
        }).encode())
        delivery = {
            "release": "7.0.0-sm8150-rear1",
            "worktree": str(root),
            "created_at": "2026-11-10T00:00:00Z",
            "git_head": "0" * 40,
            "boot_img": {"path": str(files["boot_img"]), "sha256": sha(files["boot_img"]),
                         "size": files["boot_img"].stat().st_size},
            "image": {"path": str(files["image"]), "sha256": sha(files["image"]),
                      "size": files["image"].stat().st_size},
            "kernel_package": {"path": str(files["kernel_package"]),
                               "sha256": sha(files["kernel_package"]),
                               "size": files["kernel_package"].stat().st_size},
            "dtb": {"path": str(files["dtb"]), "sha256": sha(files["dtb"]),
                    "size": files["dtb"].stat().st_size},
            "initramfs": {"path": str(files["initramfs"]),
                          "sha256": sha(files["initramfs"]),
                          "size": files["initramfs"].stat().st_size},
            "rootfs_image": {"path": str(files["rootfs_image"]),
                             "sha256": sha(files["rootfs_image"]),
                             "size": files["rootfs_image"].stat().st_size},
            "i2c_qcom_cci": {"path": str(files["cci"]), "sha256": sha(files["cci"]),
                             "size": files["cci"].stat().st_size},
        }
        write(build / "DELIVERY.json", json.dumps(delivery).encode())
        packaging = {
            "release": "7.0.0-sm8150-rear1",
            "nbd_port": 10001,
            "initramfs_sha256": sha(files["initramfs"]),
            "vmlinuz_sha256": sha(files["kernel_package"]),
            "rootfs_image_sha256": sha(files["rootfs_image"]),
        }
        write(build / "packaging-inputs.json", json.dumps(packaging).encode())
        return root, delivery

    def test_identity_comes_from_manifest_not_a_fixed_today_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, delivery = self.fixture(Path(temporary))
            info, failures, warnings = cp.verify_artifacts(root, {}, rehash=False)
            self.assertEqual(failures, [])
            self.assertTrue(info["files"]["i2c_qcom_cci"]["match"])
            self.assertEqual(info["files"]["rootfs_image"]["role"],
                             "immutable_packaged_rootfs")
            self.assertFalse(info["served_cache"]["same_as_packaged_rootfs"])
            self.assertIn("differs from packaged rootfs",
                          info["served_cache"]["classification"])
            self.assertTrue(any("served netboot cache differs" in item for item in warnings))
            # A future build with a different CCI hash still passes as long as
            # its own manifest records that hash.
            self.assertNotEqual(delivery["i2c_qcom_cci"]["sha256"], "4cd35f05" + "0" * 56)

    def test_manifest_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, delivery = self.fixture(Path(temporary))
            delivery["image"]["sha256"] = "0" * 64
            write(root / "build/rear-camera/DELIVERY.json", json.dumps(delivery).encode())
            info, failures, warnings = cp.verify_artifacts(root, {}, rehash=True)
            self.assertTrue(any("image:" in item for item in failures))

    def test_cache_identical_is_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, delivery = self.fixture(Path(temporary))
            packaged = Path(delivery["rootfs_image"]["path"]).read_bytes()
            Path(root / ".work/rear-camera/pmbootstrap-work/images_netboot/"
                        "oneplus-guacamole.img").write_bytes(packaged)
            info, failures, warnings = cp.verify_artifacts(root, {}, rehash=True)
            self.assertEqual(failures, [])
            self.assertTrue(info["served_cache"]["same_as_packaged_rootfs"])


class CliTests(unittest.TestCase):
    def test_source_only_cli_runs_from_arbitrary_cwd(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            root = temporary / "root"
            root.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            env = {**os.environ, "GIT_AUTHOR_NAME": "Test",
                   "GIT_AUTHOR_EMAIL": "t@example.invalid",
                   "GIT_COMMITTER_NAME": "Test",
                   "GIT_COMMITTER_EMAIL": "t@example.invalid"}
            write(root / "README", b"base\n")
            subprocess.run(["git", "add", "README"], cwd=root, check=True, env=env)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root,
                           check=True, env=env)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root,
                                           text=True).strip()
            recipe = root / "device/testing/rear-camera/linux-postmarketos-qcom-sm8150"
            recipe.mkdir(parents=True)
            patch_bodies = {
                "0027-arm64-dts-enable-guacamole-speaker-bus.patch": "speaker\n",
                "0028-media-camss-sm8150-rebase.patch": "camss\n",
                "0029-media-guacamole-rear-sensors-and-lenses.patch": "sensors\n",
                "0030-arm64-dts-guacamole-three-rear-cameras.patch":
                    "cci1: cci@ac4b000\n\ti2c-bus@1 {\n",
                "0031-i2c-qcom-cci-initialize-all-master-completions.patch":
                    "for (i = 0; i < cci->data->num_masters; i++)\n"
                    "\tinit_completion(&cci->master[i].irq_complete);\n"
                    "for_each_available_child_of_node\n",
            }
            source_lines = []
            sum_lines = []
            for name, body in patch_bodies.items():
                path = write(recipe / name, body.encode())
                source_lines.append(name)
                sum_lines.append(f"{hashlib.sha512(path.read_bytes()).hexdigest()}  {name}")
            apkbuild = ("pkgver=7.0.0\npkgrel=2\nsource=\"\n\t"
                        + "\n\t".join(source_lines)
                        + "\n\"\nsha512sums=\"\n" + "\n".join(sum_lines) + "\n\"\n")
            write(recipe / "APKBUILD", apkbuild.encode())
            lib = root / "device/testing/rear-camera/libcamera-rear"
            lib.mkdir(parents=True)
            write(lib / "APKBUILD", b"pkgver=0.7.2\npkgrel=0\nsource=\"\"\nsha512sums=\"\"\n")
            out = temporary / "out"
            result = subprocess.run(
                ["python3", str(SCRIPT), "--root", str(root), "--out", str(out),
                 "--source-only", "--expected-head", head],
                cwd="/", capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            document = json.loads((out / "CHECKPOINT.json").read_text())
            self.assertTrue(document["status"]["ok"])
            self.assertEqual(document["source"]["head"], head)
            self.assertEqual(document["artifacts"], {"skipped": "source-only"})


class ArchiveTests(unittest.TestCase):
    def test_archive_is_small_and_keeps_snapshot_and_recipes(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            root = temporary / "root"
            snapshot = temporary / "snapshot"
            out = temporary / "out"
            make_snapshot(snapshot, "patch\n", {"docs/a.md": b"a\n"})
            write(root / "device/testing/rear-camera/linux-postmarketos-qcom-sm8150/APKBUILD",
                  b"pkgver=7.0.0\n")
            write(root / "docs/CAMERA_DELIVERY.md", b"doc\n")
            write(root / "build/rear-camera/DELIVERY.json", b"{}\n")
            write(root / "build/rear-camera/Image", b"image\n")
            write(root / ".work/rear-camera/rootfs/export/oneplus-guacamole.img",
                  b"rootfs\n")
            write(root / ".work/rear-camera/pmbootstrap-work/log.txt", b"log\n")
            write(out / "CHECKPOINT.json", b"{}\n")
            write(out / "CHECKPOINT.sha256", b"x\n")
            archive = temporary / "checkpoint.tar.zst"
            info = cp.make_archive(root, snapshot, out, archive)
            self.assertTrue(archive.is_file())
            self.assertTrue(Path(str(archive) + ".sha256").is_file())
            self.assertEqual(info["sha256"], cp.digest_file(archive))
            with tarfile.open(archive, "r:zst") as tar:
                names = set(tar.getnames())
            self.assertIn("nura-working-camera-checkpoint/snapshot/untracked/docs/a.md",
                          names)
            self.assertIn("nura-working-camera-checkpoint/"
                          "device/testing/rear-camera/linux-postmarketos-qcom-sm8150/APKBUILD",
                          names)
            self.assertIn("nura-working-camera-checkpoint/checkpoint/out/CHECKPOINT.json",
                          names)
            self.assertFalse(any(".work" in name for name in names))
            self.assertFalse(any(name.endswith("/Image") for name in names))


class NoHardcodedIdentityTests(unittest.TestCase):
    def test_tool_does_not_embed_the_fixed_cci_hash(self):
        text = SCRIPT.read_text()
        self.assertNotIn("4cd35f05ff873700c43baad265efd0bf73166352baac79f88d4aeca456fb27e4",
                         text)
        self.assertNotIn("0e19bbb5524b8b6b7ae7cc024327375a14678796298c1e8110a4f28cffe0cec9",
                         text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
