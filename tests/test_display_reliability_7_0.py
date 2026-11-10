#!/usr/bin/env python3
"""Host-only checks for the two display-reliability kernel patches.

These do not build, boot or touch a phone. When the patched 7.0 source is
staged they apply 0036 (DSI PHY PM) and 0037 (DSI command/frame sync) to a
scratch copy of the affected files and assert the properties the review
depends on:

* the shared command/frame mutex is initialised before any encoder or host
  can use it, and the command path sends only while holding it;
* the frame wait is bounded, reads the published atomic counter, and cannot
  return with the mutex held;
* the encoder publishes the frame count/queue under the lock, only as a
  non-slave, and clears them on disable after comparing the pointer;
* the PHY runtime callbacks pair clk_prepare_enable() with
  clk_disable_unprepare(), and the clock pointer exists before runtime PM is
  enabled.

Set NURA_KERNEL_SOURCE to a staged linux-v7.0.0-sm8150 tree to run the apply
checks outside the default .work location. Without a staged tree the class
skips; the patch-file checks still run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "device/testing/rear-camera/linux-postmarketos-qcom-sm8150"
SRC = Path(
    os.environ.get("NURA_KERNEL_SOURCE",
                   ROOT / ".work/rear-camera/linux-v7.0.0-sm8150"))

PATCHES = {
    "0036-dsi-phy-pm.patch": [
        "drivers/gpu/drm/msm/dsi/phy/dsi_phy.c",
        "drivers/gpu/drm/msm/dsi/phy/dsi_phy.h",
    ],
    "0037-dsi-cmd-frame-sync.patch": [
        "drivers/gpu/drm/msm/msm_drv.h",
        "drivers/gpu/drm/msm/msm_drv.c",
        "drivers/gpu/drm/msm/disp/dpu1/dpu_encoder_phys_cmd.c",
        "drivers/gpu/drm/msm/disp/dpu1/dpu_encoder.c",
        "drivers/gpu/drm/msm/dsi/dsi_manager.c",
    ],
}


def patch_paths(patch: Path) -> list[str]:
    """Files a patch touches, as --/++ paths without the a/ and b/ prefixes."""
    found: list[str] = []
    for line in patch.read_text().splitlines():
        if line.startswith("--- a/") or line.startswith("+++ b/"):
            name = line[6:]
            if name not in found:
                found.append(name)
    return found


def function_body(text: str, signature: str) -> str:
    """Text between the braces of the first function matching signature."""
    start = text.index(signature)
    opening = text.index("{", start)
    depth = 0
    for offset in range(opening, len(text)):
        if text[offset] == "{":
            depth += 1
        elif text[offset] == "}":
            depth -= 1
            if depth == 0:
                return text[opening + 1:offset]
    raise AssertionError(f"unterminated body after {signature!r}")


class PatchFiles(unittest.TestCase):
    def test_touch_only_display_sources(self):
        for name in PATCHES:
            patch = RECIPE / name
            self.assertTrue(patch.is_file(), name)
            paths = patch_paths(patch)
            self.assertEqual(paths, PATCHES[name], f"{name} file set")
            for path in paths:
                self.assertTrue(path.startswith("drivers/gpu/drm/msm/"), path)
            text = patch.read_text()
            self.assertNotIn("APKBUILD", text)
            self.assertNotIn("config-postmarketos", text)

    def test_phy_and_frame_patches_are_disjoint(self):
        phy = set(patch_paths(RECIPE / "0036-dsi-phy-pm.patch"))
        frame = set(patch_paths(RECIPE / "0037-dsi-cmd-frame-sync.patch"))
        self.assertFalse(phy & frame, "patches must not depend on each other")


@unittest.skipUnless(SRC.is_dir(), f"patched 7.0 source not staged at {SRC}")
class PatchedSources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("patch") is None:
            raise unittest.SkipTest("patch(1) is not installed")
        cls.tmp = tempfile.TemporaryDirectory(prefix="nura-display-")
        cls.root = Path(cls.tmp.name)
        cls.files: dict[str, str] = {}
        for name, paths in PATCHES.items():
            for rel in paths:
                dst = cls.root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(SRC / rel, dst)
            args = ["patch", "-p1", "--fuzz=0", "--dry-run", "--batch", "--forward", "-s",
                    "-d", str(cls.root), "-i", str(RECIPE / name)]
            forward = subprocess.run(args, capture_output=True, text=True)
            if forward.returncode == 0:
                subprocess.run(args[:3] + args[4:], check=True,
                               capture_output=True, text=True)
            else:
                reverse = subprocess.run(args[:1] + ["-R"] + args[1:],
                                         capture_output=True, text=True)
                if reverse.returncode != 0:
                    raise AssertionError(
                        f"{name} neither applies cleanly nor is already applied:\n"
                        f"{forward.stdout}{forward.stderr}"
                        f"{reverse.stdout}{reverse.stderr}")
        for paths in PATCHES.values():
            for rel in paths:
                cls.files[rel] = (cls.root / rel).read_text()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def file(self, name: str) -> str:
        return self.files[name]

    def test_no_rejects_and_expected_hunks_present(self):
        self.assertFalse(list(self.root.rglob("*.rej")))
        self.assertFalse(list(self.root.rglob("*.orig")))
        self.assertIn("iface_clk", self.file(
            "drivers/gpu/drm/msm/dsi/phy/dsi_phy.h"))
        self.assertIn("cmd_xfer_lock", self.file(
            "drivers/gpu/drm/msm/msm_drv.h"))

    def test_mutex_is_initialised_in_msm_drm_init(self):
        body = function_body(self.file("drivers/gpu/drm/msm/msm_drv.c"),
                             "static int msm_drm_init(")
        init = body.index("mutex_init(&priv->cmd_xfer_lock);")
        self.assertLess(body.index("ddev->dev_private = priv;"), init)
        self.assertLess(init, body.index("INIT_LIST_HEAD(&priv->objects);"))

    def test_kickoff_holds_the_same_mutex_around_the_phys_flush(self):
        body = function_body(self.file(
            "drivers/gpu/drm/msm/disp/dpu1/dpu_encoder.c"),
            "void dpu_encoder_kickoff(struct drm_encoder *drm_enc)")
        lock = body.index("mutex_lock(")
        flush = body.index("_dpu_encoder_kickoff_phys(dpu_enc);")
        unlock = body.index("mutex_unlock(")
        self.assertLess(lock, flush)
        self.assertLess(flush, unlock)
        self.assertIn("cmd_xfer_lock", body[lock:unlock].split("\n")[0])
        self.assertEqual(body.count("mutex_lock("), 1)
        self.assertEqual(body.count("mutex_unlock("), 1)

    def test_command_path_waits_bounded_and_sends_under_the_lock(self):
        body = function_body(self.file(
            "drivers/gpu/drm/msm/dsi/dsi_manager.c"),
            "int msm_dsi_manager_cmd_xfer(int id, const struct mipi_dsi_msg *msg)")
        lock = body.index("mutex_lock(&priv->cmd_xfer_lock);")
        unlock = body.index("mutex_unlock(&priv->cmd_xfer_lock);")
        locked = body[lock:unlock]
        self.assertIn("wait_event_timeout(*priv->cmd_frames_wq", locked)
        self.assertIn("atomic_read(priv->cmd_pending_frames) == 0", locked)
        self.assertIn("msecs_to_jiffies(50)", locked)
        self.assertIn("dsi_mgr_cmd_xfer(id, msg);", locked)
        self.assertNotIn("return", locked, "no early return may hold the lock")
        self.assertEqual(body.count("mutex_lock("), 1)
        self.assertEqual(body.count("mutex_unlock("), 1)
        # The old body is kept as the unlocked helper, so the sync handling
        # and the DSI_1 early return are unchanged.
        helper = function_body(self.file(
            "drivers/gpu/drm/msm/dsi/dsi_manager.c"),
            "static int dsi_mgr_cmd_xfer(int id, const struct mipi_dsi_msg *msg)")
        self.assertIn("msm_dsi_host_cmd_tx(host, msg)", helper)
        self.assertIn("need_sync && (id == DSI_1)", helper)

    def test_encoder_publishes_and_clears_frame_state_under_the_lock(self):
        text = self.file(
            "drivers/gpu/drm/msm/disp/dpu1/dpu_encoder_phys_cmd.c")
        enable = function_body(text, "static void dpu_encoder_phys_cmd_enable(")
        self.assertIn("split_role != ENC_ROLE_SLAVE", enable)
        lock = enable.index("mutex_lock(&priv->cmd_xfer_lock);")
        unlock = enable.index("mutex_unlock(&priv->cmd_xfer_lock);")
        self.assertIn("priv->cmd_pending_frames = &phys_enc->pending_kickoff_cnt;",
                      enable[lock:unlock])
        self.assertIn("priv->cmd_frames_wq = &phys_enc->pending_kickoff_wq;",
                      enable[lock:unlock])

        disable = function_body(text, "static void dpu_encoder_phys_cmd_disable(")
        lock = disable.index("mutex_lock(&priv->cmd_xfer_lock);")
        unlock = disable.index("mutex_unlock(&priv->cmd_xfer_lock);")
        cleared = disable[lock:unlock]
        self.assertIn("priv->cmd_pending_frames == &phys_enc->pending_kickoff_cnt",
                      cleared)
        self.assertIn("priv->cmd_pending_frames = NULL;", cleared)
        self.assertIn("priv->cmd_frames_wq = NULL;", cleared)
        # Disable runs after dpu_encoder_virt_atomic_disable() waited for tx
        # complete, so no frame can still own the queue that is cleared here.
        self.assertIn("dpu_encoder_wait_for_tx_complete(drm_enc);",
                      self.file("drivers/gpu/drm/msm/disp/dpu1/dpu_encoder.c"))

    def test_phy_runtime_callbacks_pair_prepare_and_enable(self):
        text = self.file("drivers/gpu/drm/msm/dsi/phy/dsi_phy.c")
        suspend = function_body(
            text, "static int __maybe_unused dsi_phy_runtime_suspend(")
        resume = function_body(
            text, "static int __maybe_unused dsi_phy_runtime_resume(")
        self.assertIn("clk_disable_unprepare(phy->iface_clk);", suspend)
        self.assertIn("return 0;", suspend)
        self.assertIn("return clk_prepare_enable(phy->iface_clk);", resume)
        self.assertIn("SET_RUNTIME_PM_OPS(dsi_phy_runtime_suspend, "
                      "dsi_phy_runtime_resume, NULL)", text)
        self.assertNotIn("pm_clk_", text, "pm_clk would keep the clock prepared")
        self.assertNotIn("pm_clk_add", text)

    def test_phy_clock_exists_before_runtime_pm_can_call(self):
        text = self.file("drivers/gpu/drm/msm/dsi/phy/dsi_phy.c")
        probe = function_body(text, "static int dsi_phy_driver_probe(")
        drvdata = probe.index("platform_set_drvdata(pdev, phy);")
        lookup = probe.index('phy->iface_clk = devm_clk_get(dev, "iface");')
        runtime = probe.index("devm_pm_runtime_enable(dev);")
        self.assertLess(drvdata, lookup)
        self.assertLess(lookup, runtime)
        self.assertIn('"iface"', text)


if __name__ == "__main__":
    unittest.main()
