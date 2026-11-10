#!/usr/bin/env python3
"""Host-side tests for analyze_profile.py; no phone, SSH, sudo or hardware."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
ANALYZER = SCRIPTS / "analyze_profile.py"
sys.path.insert(0, str(SCRIPTS))

import analyze_profile as ap  # noqa: E402


def snapshot_text(sections: dict[str, str]) -> str:
    return "".join(f"@@{name}@@\n{body}\n" for name, body in sections.items())


BASE_SECTIONS = {
    "META": "\n".join(
        (
            "probe_version=1",
            "collected_utc=2026-11-10T12:00:00Z",
            "collected_epoch=1762776000",
            "kernel_release=7.0.0-fake",
            "kernel_version=#1 SMP PREEMPT",
            "os_pretty=postmarketOS test",
            "boot_cmdline=console=tty0 pmos.root=/dev/nbd0p2",
        )
    ),
    "CAMERA_PROCESSES": "\n".join(
        (
            "PROCESS pid=1234 comm=nura-camera exe=/usr/bin/nura-camera devices=/dev/video0",
            "CMDLINE nura-camera --preview",
            "ENV LD_LIBRARY_PATH=/opt/nura-rear-camera/lib",
            "MAP /opt/nura-rear-camera/lib/libcamera.so.0.7.2",
        )
    ),
    "LIBCAMERA_LIBS": "\n".join(
        (
            "LOADED /opt/nura-rear-camera/lib/libcamera.so.0.7.2",
            "  realpath=/opt/nura-rear-camera/lib/libcamera.so.0.7.2",
            "  size=2211064",
            "  sha256=" + "a" * 64,
            "  owner=/opt/nura-rear-camera/lib/libcamera.so.0.7.2 is owned by libcamera-rear-0.7.2-r0",
        )
    ),
    "DMESG": "[NO_MATCH]",
    "WATCH": "",
}


class AnalyzeProfileTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    # --------------------------------------------------------------- helpers

    def write_snapshot(self, sections: dict[str, str] | None = None) -> Path:
        path = self.tmp / "snapshot.txt"
        path.write_text(snapshot_text(sections if sections is not None else BASE_SECTIONS))
        return path

    def analyze(self, sections: dict[str, str] | None = None, **kwargs):
        snapshot = self.write_snapshot(sections)
        return ap.analyze(str(snapshot), **kwargs)

    def write_manifest(self, data: dict) -> Path:
        path = self.tmp / "DELIVERY.json"
        path.write_text(json.dumps(data))
        return path

    def ffmpeg_clip(self, name: str = "main-scene.mp4", rate: int = 10) -> Path | None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return None
        path = self.tmp / name
        subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"testsrc=size=160x120:rate={rate}",
                "-t", "1", "-pix_fmt", "yuv420p", str(path),
            ],
            check=True,
        )
        return path

    # ------------------------------------------------------------- no bogus pass

    def test_missing_data_is_insufficient_not_pass(self) -> None:
        analysis = self.analyze()
        self.assertEqual(analysis["verdict"], "INSUFFICIENT_DATA")
        self.assertEqual(analysis["identity"]["checks"]["release"], "UNKNOWN")
        for item in analysis["regressions"].values():
            self.assertEqual(item["functional"], "NOT_TESTED")
            self.assertNotEqual(item["collection"], "PASS")
        report = ap.render_report(analysis)
        self.assertIn("No FPS or choppiness conclusion", report)
        self.assertNotIn("| PASS |", report)
        self.assertTrue(any("No fix recommendation" in item["statement"] for item in analysis["triage"]))

    def test_regression_collection_never_claims_pass(self) -> None:
        sections = dict(BASE_SECTIONS)
        sections["AUDIO"] = "[DENIED] /proc/asound/cards"
        sections["WIFI"] = "[MISSING] iw"
        sections["CHARGING"] = "SUPPLY battery\n  status=Charging\n  input_current_limit=0"
        sections["SUSPEND"] = "success=3\nfail=2\nlast_failed_dev=abc"
        analysis = self.analyze(sections)
        self.assertEqual(analysis["regressions"]["speakers_mic"]["collection"], "DENIED")
        self.assertEqual(analysis["regressions"]["wifi"]["collection"], "MISSING")
        self.assertEqual(analysis["regressions"]["charging"]["collection"], "OBSERVED")
        self.assertEqual(analysis["regressions"]["suspend"]["collection"], "OBSERVED")
        self.assertTrue(any("input_current_limit" in note for note in analysis["regressions"]["charging"]["notes"]))
        self.assertTrue(any("suspend_stats.fail" in note for note in analysis["regressions"]["suspend"]["notes"]))
        for item in analysis["regressions"].values():
            self.assertEqual(item["functional"], "NOT_TESTED")
            self.assertNotEqual(item["collection"], "PASS")

    # ----------------------------------------------------------------- identity

    def test_manifest_release_and_cci_identity(self) -> None:
        manifest = self.write_manifest({
            "release": "7.0.0-fake",
            "i2c_qcom_cci": {"sha256": "b" * 64, "size": 8950, "path": "/tmp/cci"},
        })
        sections = dict(BASE_SECTIONS)
        sections["CCI_MODULE"] = "\n".join(
            (
                "path=/usr/lib/modules/7.0.0-fake/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst",
                "size=8950",
                "sha256=" + "b" * 64,
            )
        )
        analysis = self.analyze(sections, manifest_path=str(manifest))
        self.assertEqual(analysis["identity"]["checks"]["release"], "MATCH")
        self.assertEqual(analysis["identity"]["checks"]["cci_sha256"], "MATCH")
        self.assertEqual(analysis["identity"]["checks"]["cci_size"], "MATCH")

        bad = self.write_manifest({"release": "6.17.0-fake"})
        analysis = self.analyze(sections, manifest_path=str(bad))
        self.assertEqual(analysis["identity"]["checks"]["release"], "MISMATCH")
        self.assertTrue(any("identity" in item["statement"] for item in analysis["triage"]))

    def test_missing_manifest_and_bad_manifest_are_unknown(self) -> None:
        analysis = self.analyze()
        self.assertEqual(analysis["identity"]["checks"]["release"], "UNKNOWN")
        self.assertEqual(analysis["identity"]["checks"]["cci_sha256"], "UNKNOWN")
        bad = self.tmp / "bad.json"
        bad.write_text("{not json")
        analysis = self.analyze(manifest_path=str(bad))
        self.assertEqual(analysis["identity"]["checks"]["release"], "UNKNOWN")
        self.assertIn("manifest_error", analysis["identity"])
        self.assertTrue(analysis["identity"]["manifest_error"])

    def test_nested_manifest_release_is_found(self) -> None:
        manifest = self.write_manifest({"artifacts": {"kernel": {"release": "7.0.0-fake"}}})
        analysis = self.analyze(manifest_path=str(manifest))
        self.assertEqual(analysis["identity"]["checks"]["release"], "MATCH")

    # -------------------------------------------------------------- camera stack

    def test_private_stack(self) -> None:
        analysis = self.analyze()
        self.assertEqual(analysis["camera_stack"]["classification"], "private-0.7.2")
        self.assertEqual(analysis["camera_stack"]["processes"][0]["stack"], "private-0.7.2")

    def test_system_stack(self) -> None:
        sections = dict(BASE_SECTIONS)
        sections["CAMERA_PROCESSES"] = "\n".join(
            (
                "PROCESS pid=42 comm=pipewire exe=/usr/bin/pipewire devices=/dev/video0",
                "CMDLINE pipewire",
                "MAP /usr/lib/libcamera.so.0.8",
                "MAP /usr/lib/spa-0.2/libcamera/libspa-libcamera.so",
            )
        )
        sections["LIBCAMERA_LIBS"] = "LOADED /usr/lib/libcamera.so.0.8\n  size=1\n  sha256=" + "c" * 64
        analysis = self.analyze(sections)
        self.assertEqual(analysis["camera_stack"]["classification"], "system-0.8")

    def test_mixed_stack(self) -> None:
        sections = dict(BASE_SECTIONS)
        sections["CAMERA_PROCESSES"] = "\n".join(
            (
                "PROCESS pid=42 comm=nura-camera exe=/usr/bin/nura-camera devices=/dev/video0",
                "CMDLINE nura-camera",
                "MAP /opt/nura-rear-camera/lib/libcamera.so.0.7.2",
                "MAP /usr/lib/libcamera.so.0.8",
            )
        )
        sections["LIBCAMERA_LIBS"] = "\n".join(
            (
                "LOADED /opt/nura-rear-camera/lib/libcamera.so.0.7.2",
                "  size=1",
                "LOADED /usr/lib/libcamera.so.0.8",
                "  size=1",
            )
        )
        analysis = self.analyze(sections)
        self.assertEqual(analysis["camera_stack"]["classification"], "mixed")
        triage_text = " ".join(item["statement"] for item in analysis["triage"]).lower()
        self.assertIn("private", triage_text)
        self.assertIn("system", triage_text)

    def test_package_presence_does_not_identify_stack(self) -> None:
        sections = dict(BASE_SECTIONS)
        sections["PACKAGES"] = "libcamera-99990.8.0-r1\npipewire-spa-libcamera-1.6.11-r0"
        sections["CAMERA_PROCESSES"] = "[NOT_OBSERVED] no camera process"
        sections["LIBCAMERA_LIBS"] = "[NOT_OBSERVED] no libcamera mapped"
        analysis = self.analyze(sections)
        self.assertEqual(analysis["camera_stack"]["classification"], "unknown")
        self.assertTrue(any("installed package names" in item["statement"] for item in analysis["triage"]))

    # -------------------------------------------------------------------- frames

    def test_regular_frame_times(self) -> None:
        result = ap.analyze_frame_times([i / 30.0 for i in range(300)], 30.0)
        self.assertEqual(result["status"], "PROBED_REGULAR")
        self.assertEqual(result["gap_count"], 0)
        self.assertAlmostEqual(result["effective_fps"], 30.0, places=1)
        self.assertGreater(result["fps_ratio"], 0.99)

    def test_gap_frame_times(self) -> None:
        times = [i / 30.0 for i in range(150)] + [5.5 + i / 30.0 for i in range(150)]
        result = ap.analyze_frame_times(times, 30.0)
        self.assertEqual(result["status"], "PROBED_GAPS")
        self.assertEqual(result["gap_count"], 1)
        self.assertGreaterEqual(result["inferred_missing_frames"], 10)
        self.assertGreater(result["largest_gaps"][0]["gap_ms"], 500)

    def test_uniform_low_rate_is_not_regular(self) -> None:
        result = ap.analyze_frame_times([i / 15.0 for i in range(150)], 30.0)
        self.assertEqual(result["status"], "PROBED_LOW_RATE")
        self.assertEqual(result["gap_count"], 0)
        self.assertAlmostEqual(result["fps_ratio"], 0.5, places=2)

    def test_too_few_frames(self) -> None:
        result = ap.analyze_frame_times([0.0, 0.1, 0.2], 30.0)
        self.assertEqual(result["status"], "INSUFFICIENT_FRAMES")

    # --------------------------------------------------------------------- video

    def test_missing_ffprobe_and_empty_file_are_graceful(self) -> None:
        video = self.tmp / "main-scene.mp4"
        video.write_bytes(b"not a video")
        result = ap.probe_video(video, "/definitely/not/ffprobe")
        self.assertEqual(result["status"], "TOOL_MISSING")
        self.assertIn("ffprobe", result["detail"])
        empty = self.tmp / "empty.mp4"
        empty.write_bytes(b"")
        result = ap.probe_video(empty, "/definitely/not/ffprobe")
        self.assertEqual(result["status"], "UNREADABLE")

    def test_sidecar_sensor_override_and_ambiguity(self) -> None:
        self.assertEqual(ap.video_sensor(Path("tele-scene.mp4"), {}), "tele")
        self.assertEqual(ap.video_sensor(Path("whatever.mp4"), {"sensor": "ultrawide"}), "wide")
        self.assertEqual(ap.video_sensor(Path("main-wide.mp4"), {}), "ambiguous")
        self.assertEqual(ap.video_sensor(Path("clip.mp4"), {}), "unlabeled")

    def test_requested_fps_mismatch_is_low_rate(self) -> None:
        video = {
            "path": "/tmp/main-scene.mp4",
            "status": "PROBED_REGULAR",
            "nominal_fps": 10.0,
            "effective_fps": 10.0,
            "duration_s": 1.0,
        }
        ap.decorate_video(
            video, None,
            {"sensor": "main", "fps": "30", "wall_seconds": "1"},
            "unknown",
        )
        self.assertFalse(video["setting_match"])
        self.assertTrue(video["below_requested_fps"])
        self.assertEqual(video["status"], "PROBED_LOW_RATE")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe not installed")
    def test_ffprobe_tiny_clip(self) -> None:
        clip = self.ffmpeg_clip("main-tiny.mp4", 10)
        assert clip is not None
        result = ap.probe_video(clip, shutil.which("ffprobe") or "ffprobe")
        self.assertIn(result["status"], {"PROBED_REGULAR", "PROBED_GAPS"})
        self.assertGreater(result["effective_fps"] or 0, 5.0)
        self.assertEqual(result["width"], 160)
        self.assertEqual(result["height"], 120)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe not installed")
    def test_cli_end_to_end_labeled_clip(self) -> None:
        clips = self.tmp / "clips"
        clips.mkdir()
        clip = self.ffmpeg_clip("clips/main-scene.mp4", 10)
        assert clip is not None
        (clips / "main-scene.txt").write_text("sensor=main\nsymptom=preview\nwall_seconds=1\n")
        manifest = self.write_manifest({
            "release": "7.0.0-fake",
            "i2c_qcom_cci": {"sha256": "a" * 64, "size": 8950},
        })
        snapshot = self.write_snapshot()
        out = self.tmp / "out"
        result = subprocess.run(
            [
                sys.executable, str(ANALYZER),
                "--snapshot", str(snapshot), "--out", str(out),
                "--recordings", str(clips), "--manifest", str(manifest),
                "--ffprobe", shutil.which("ffprobe") or "ffprobe",
            ],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((out / "report.md").is_file())
        self.assertTrue((out / "analysis.json").is_file())
        analysis = json.loads((out / "analysis.json").read_text())
        self.assertEqual(analysis["per_sensor"]["main"]["status"], "REGULAR")
        self.assertEqual(analysis["symptom"]["label"], "PREVIEW_PATH_SUSPECT")
        self.assertNotEqual(analysis["verdict"], "INSUFFICIENT_DATA")
        self.assertIn("main-scene.mp4", (out / "report.md").read_text())

    def test_missing_video_tool_yields_partial_not_pass(self) -> None:
        clips = self.tmp / "clips"
        clips.mkdir()
        (clips / "main-scene.mp4").write_bytes(b"not really a video")
        analysis = self.analyze(recordings=str(clips), ffprobe_bin="/definitely/not/ffprobe")
        self.assertEqual(analysis["videos"][0]["status"], "TOOL_MISSING")
        self.assertEqual(analysis["verdict"], "PARTIAL")
        self.assertNotIn("PASS", analysis["verdict"])

    # -------------------------------------------------------------------- watch

    def test_watch_flags(self) -> None:
        sections = dict(BASE_SECTIONS)
        sections["WATCH"] = "\n".join(
            (
                "# epoch load1 load5 psi_cpu_some psi_io_some cpu_max_khz cpu_gov gpu_hz gpu_gov thermal_max_c batt_status batt_capacity usb_online input_limit_ua",
                "1762776000 2.0 1.0 55.0 1.0 1800000 schedutil 500000000 msm-v2 80 Charging 50 1 1800000",
                "1762776001 2.1 1.0 60.0 1.0 800000 schedutil 300000000 msm-v2 82 Charging 49 1 1800000",
            )
        )
        analysis = self.analyze(sections)
        watch = analysis["watch"]
        self.assertEqual(watch["status"], "OBSERVED")
        self.assertEqual(watch["samples"], 2)
        self.assertEqual(watch["thermal_max_c"], 82)
        self.assertIn("thermal_max_at_or_above_75C", watch["flags"])
        self.assertIn("cpu_psi_some_at_or_above_50", watch["flags"])
        self.assertIn("cpu_freq_dropped_below_half_of_window_max", watch["flags"])

    def test_triage_uses_thermal_flag_without_inventing_fix(self) -> None:
        analysis = {
            "verdict": "CHOP_OBSERVED",
            "identity": {"checks": {"release": "MATCH", "cci_sha256": "MATCH"}},
            "camera_stack": {"classification": "private-0.7.2"},
            "errors": {"status": "NO_MATCH_IN_CAPTURED_LOG"},
            "videos": [{"status": "PROBED_GAPS", "sensor": "main", "file": "main-clip.mp4", "setting_match": False}],
            "watch": {"status": "OBSERVED", "flags": ["thermal_max_at_or_above_75C"]},
            "per_sensor": {
                "main": {"status": "CHOP_OBSERVED"},
                "tele": {"status": "MISSING"},
                "wide": {"status": "MISSING"},
            },
            "symptom": {"effective": "recording", "label": "CAPTURE_OR_ENCODE_SUSPECT"},
        }
        items = ap.build_triage(analysis)
        text = " ".join(item["statement"] + " " + item["next_step"] for item in items)
        self.assertIn("thermal", text.lower())
        self.assertIn("control", text.lower())
        self.assertIn("requested fps", text.lower())
        self.assertNotIn("flash", text.lower())
        self.assertNotIn("autofocus fix", text.lower())

    # ------------------------------------------------------------------- errors

    def test_observed_error_strings(self) -> None:
        sections = dict(BASE_SECTIONS)
        sections["DMESG"] = "i2c-qcom-cci 0c1c000.i2c: cci reset timeout\nOops: kernel NULL pointer"
        analysis = self.analyze(sections)
        self.assertEqual(analysis["errors"]["status"], "OBSERVED_ERRORS")
        self.assertIn("cci", analysis["errors"]["matches"])
        self.assertIn("oops", analysis["errors"]["matches"])

    def test_denied_log_is_unknown_not_clean(self) -> None:
        sections = dict(BASE_SECTIONS)
        sections["DMESG"] = "[DENIED] dmesg (kernel.dmesg_restrict or permission)\nhint: user-run: sudo dmesg"
        analysis = self.analyze(sections)
        self.assertEqual(analysis["errors"]["status"], "UNKNOWN")
        self.assertFalse(analysis["errors"]["captured"])

    # ------------------------------------------------------------------ permission

    @unittest.skipIf(os.geteuid() == 0, "chmod 000 does not deny root")
    def test_unreadable_snapshot_is_reported(self) -> None:
        path = self.write_snapshot()
        os.chmod(path, 0o000)
        self.addCleanup(os.chmod, path, 0o644)
        with self.assertRaises(PermissionError):
            ap.analyze(str(path))
        result = subprocess.run(
            [sys.executable, str(ANALYZER), "--snapshot", str(path), "--out", str(self.tmp / "out")],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot read input", result.stderr)

    def test_user_supplied_dmesg_resolves_denied_window(self) -> None:
        sections = dict(BASE_SECTIONS)
        sections["DMESG"] = "[DENIED] dmesg\nhint: user-run: sudo dmesg > dmesg.txt"
        snapshot = self.write_snapshot(sections)
        dmesg = self.tmp / "dmesg.txt"
        dmesg.write_text("i2c-qcom-cci 0c1c000.i2c: cci reset timeout\n")
        analysis = ap.analyze(str(snapshot), dmesg_path=str(dmesg))
        self.assertEqual(analysis["errors"]["status"], "OBSERVED_ERRORS")
        self.assertIn("cci", analysis["errors"]["matches"])
        self.assertEqual(analysis["inputs"]["dmesg"], str(dmesg))


if __name__ == "__main__":
    unittest.main()
