#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Regression checks for the 7.0 driver-reliability patches.

The test applies the three patches to a private copy of the relevant 7.0
source files and asserts the ordering and unwind properties they establish.
It skips when the kernel source is not staged.  It never touches hardware,
the staged kernel tree, or a running image.
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
SRC = Path(os.environ.get(
    "NURA_KERNEL_SOURCE",
    ROOT / ".work/rear-camera/linux-v7.0.0-sm8150",
))

SLIM = "drivers/slimbus/qcom-ngd-ctrl.c"
FASTRPC = "drivers/misc/fastrpc.c"
TOUCH = "drivers/input/touchscreen/s6sy761.c"
PATCHES = (
    RECIPE / "0033-slim-ngd-work-init.patch",
    RECIPE / "0034-fastrpc-freezable-wait.patch",
    RECIPE / "0035-s6sy761-resume-irq.patch",
)


def read(root: Path, relative: str) -> str:
    return (root / relative).read_text()


def between(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    return text[begin:text.index(end, begin)]


class DriverReliabilityPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        missing = [p.name for p in PATCHES if not p.is_file()]
        if missing:
            raise unittest.SkipTest(f"patches not staged: {', '.join(missing)}")
        if not all((SRC / name).is_file() for name in (SLIM, FASTRPC, TOUCH)):
            raise unittest.SkipTest(f"patched 7.0 source not staged: {SRC}")

        cls._tree = tempfile.TemporaryDirectory(prefix="nura-driver-reliability-")
        cls.addClassCleanup(cls._tree.cleanup)
        cls.tree = Path(cls._tree.name)
        for name in (SLIM, FASTRPC, TOUCH):
            target = cls.tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SRC / name, target)

        for patch in PATCHES:
            # --forward makes an already-applied patch a no-op.  A genuinely
            # mismatched patch leaves the marker assertions below to fail.
            subprocess.run(
                ["patch", "-p1", "--fuzz=0", "--forward",
                 "--no-backup-if-mismatch", "-i", str(patch)],
                cwd=cls.tree, capture_output=True, text=True,
            )

    def test_ngd_work_ssr_and_qmi_ordering(self) -> None:
        text = read(self.tree, SLIM)
        child = between(text, "static int qcom_slim_ngd_probe(",
                        "static int qcom_slim_ngd_ctrl_probe(")
        self.assertNotIn("INIT_WORK(&ctrl->ngd_up_work", child)
        # The QMI lookup can publish qmi_up and wake ngd_up_work; the
        # master worker and its queue must already exist when it does.
        self.assertLess(child.index("INIT_WORK(&ctrl->m_work"),
                        child.index("qcom_slim_ngd_qmi_svc_event_init("))
        self.assertLess(child.index("create_singlethread_workqueue("),
                        child.index("qcom_slim_ngd_qmi_svc_event_init("))
        self.assertIn("destroy_workqueue(ctrl->mwq);", child)
        self.assertIn("ctrl->mwq = NULL;", child)

        ctrl = between(text, "static int qcom_slim_ngd_ctrl_probe(",
                       "static void qcom_slim_ngd_ctrl_remove(")
        self.assertLess(ctrl.index("init_completion(&ctrl->qmi_up)"),
                        ctrl.index("INIT_WORK(&ctrl->ngd_up_work"))
        self.assertLess(ctrl.index("INIT_WORK(&ctrl->ngd_up_work"),
                        ctrl.index("qcom_register_ssr_notifier("))
        self.assertLess(ctrl.index("qcom_register_ssr_notifier("),
                        ctrl.index("pdr_handle_alloc("))

        # The error path releases PDR and unregisters SSR before cancelling
        # the work, so no callback can queue it again after the cancel.
        error = ctrl.split("err_pdr_release:")[1]
        self.assertLess(error.index("pdr_handle_release("),
                        error.index("qcom_unregister_ssr_notifier("))
        self.assertLess(error.index("qcom_unregister_ssr_notifier("),
                        error.index("cancel_work_sync(&ctrl->ngd_up_work)"))

        # qmi_up may complete inside the synchronous child probe, so the
        # register window must be valid before platform_device_add().
        register = between(text, "static int of_qcom_slim_ngd_register(",
                           "static int qcom_slim_ngd_probe(")
        self.assertLess(register.index("ngd->base ="),
                        register.index("ret = platform_device_add("))

        remove = between(text, "static void qcom_slim_ngd_remove(",
                         "static int __maybe_unused qcom_slim_ngd_runtime_idle(")
        self.assertIn("cancel_work_sync(&ctrl->ngd_up_work)", remove)

        # The 5 s QMI wait remains a separate policy change even though the
        # original dmesg showed one timeout.
        self.assertIn("msecs_to_jiffies(MSEC_PER_SEC)", text)
        self.assertNotIn("msecs_to_jiffies(5 * MSEC_PER_SEC)", text)

    def test_fastrpc_wait_is_freezable_and_keeps_error_paths(self) -> None:
        text = read(self.tree, FASTRPC)
        self.assertNotIn("wait_for_completion_interruptible(&ctx->work)", text)
        self.assertIn("wait_for_completion_state(&ctx->work", text)
        self.assertIn("TASK_INTERRUPTIBLE | TASK_FREEZABLE", text)

        # Kernel invokes keep their timeout and interrupted invokes keep the
        # existing context/mmap bookkeeping.
        self.assertIn("wait_for_completion_timeout(&ctx->work, 10 * HZ)", text)
        self.assertIn("err != -ERESTARTSYS && err != -ETIMEDOUT", text)
        self.assertIn("if (err == -ERESTARTSYS) {", text)

    def test_touch_resume_unmasks_irq_after_power_on(self) -> None:
        text = read(self.tree, TOUCH)
        resume = between(text, "static int s6sy761_resume(",
                         "static const struct dev_pm_ops")
        self.assertLess(resume.index("s6sy761_power_on(sdata)"),
                        resume.index("s6sy761_restore_sense(sdata)"))
        self.assertLess(resume.index("s6sy761_restore_sense(sdata)"),
                        resume.index("enable_irq(sdata->client->irq)"))
        self.assertIn("err_power_off:", resume)
        self.assertIn("s6sy761_power_off(sdata);", resume)

    def test_touch_power_and_irq_lifetimes_are_balanced(self) -> None:
        text = read(self.tree, TOUCH)
        self.assertIn("bool powered_up;", text)
        self.assertIn("guard(mutex)(&sdata->input->mutex);", text)

        power_on = between(text, "static int s6sy761_power_on(",
                           "static int s6sy761_hw_init(")
        self.assertIn("err_disable_regulators:", power_on)
        self.assertIn("sdata->powered_up = true;", power_on)
        self.assertIn("regulator_bulk_disable(", power_on.split(
            "err_disable_regulators:")[1])

        power_off = between(text, "static void s6sy761_power_off(",
                            "static int s6sy761_probe(")
        self.assertNotIn("disable_irq", power_off)
        self.assertIn("if (!sdata->powered_up)", power_off)

        suspend = between(text, "static int s6sy761_suspend(",
                          "static int s6sy761_restore_sense(")
        self.assertIn("if (sdata->powered_up)", suspend)
        self.assertIn("disable_irq(sdata->client->irq)", suspend)


if __name__ == "__main__":
    unittest.main()
