#!/usr/bin/env python3
"""Analyze a read-only Nura phone snapshot and user-supplied recordings.

This tool never contacts the phone.  It parses the @@SECTION@@ output of
nura-probe.sh, optionally probes short user-recorded clips with the host's
ffprobe, and writes machine-readable plus human-readable results.

It deliberately does not emit a PASS verdict.  Missing tools, permissions or
data become UNKNOWN/MISSING/PARTIAL, never a synthetic success.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA = 1
SENSORS = ("main", "tele", "wide")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
SYMPTOMS = ("unknown", "preview", "recording", "playback", "both", "mixed")
PROBED_STATUSES = {"PROBED_REGULAR", "PROBED_GAPS", "PROBED_LOW_RATE"}

SENSOR_ALIASES = {
    "main": "main",
    "primary": "main",
    "wide": "wide",
    "ultrawide": "wide",
    "uw": "wide",
    "tele": "tele",
    "telephoto": "tele",
}

THRESHOLDS = {
    "thermal_attention_c": 75.0,
    "cpu_psi_attention": 50.0,
    "cpu_freq_drop_ratio": 0.5,
    "battery_low_percent": 15.0,
    "wall_clock_shortfall_ratio": 0.90,
    "fps_regular_ratio": 0.95,
    "gap_median_multiplier": 1.5,
}

MARKER_RE = re.compile(r"^@@([A-Z0-9_]+)@@\s*$")
FRAME_RE = re.compile(r"libcamera(?:-base)?\.so\.([0-9][0-9.]*)", re.IGNORECASE)
META_LINE_PREFIXES = (
    "[MISSING]",
    "[DENIED]",
    "[NO_MATCH]",
    "[EXIT",
    "[SKIPPED]",
    "[NOT_OBSERVED]",
    "[UNAVAILABLE]",
    "hint:",
)


# --------------------------------------------------------------------- input

def parse_snapshot(text: str) -> dict[str, list[str]]:
    """Split @@SECTION@@ output into ordered section bodies."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        match = MARKER_RE.match(line)
        if match:
            if current is not None:
                sections.setdefault(current, []).append("\n".join(buf).strip("\n"))
            current = match.group(1)
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections.setdefault(current, []).append("\n".join(buf).strip("\n"))
    return sections


def section(sections: dict[str, list[str]], name: str) -> str:
    return "\n".join(sections.get(name, []))


def parse_kv(body: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("$"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            if key and " " not in key:
                values[key] = value.strip()
    return values


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return not text or text in {"NA", "UNKNOWN"} or text.startswith("[")


def as_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() in {"", "NA"}:
            return None
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() in {"", "NA"}:
            return None
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def read_json(path: str | Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, PermissionError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(data, dict):
        return None, "manifest is not a JSON object"
    return data, None


def find_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = find_key(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_key(value, key)
            if found is not None:
                return found
    return None


def manifest_expectations(data: dict[str, Any] | None) -> dict[str, Any]:
    """Read the conventional DELIVERY.json keys without assuming one schema."""
    if not data:
        return {}
    release = data.get("release") or data.get("kernel_release")
    kernel = data.get("kernel")
    if not release and isinstance(kernel, dict):
        release = kernel.get("release")
    if not release:
        release = find_key(data, "kernel_release")
    if not release:
        release = find_key(data, "release")
    if not isinstance(release, str):
        release = None

    cci = data.get("i2c_qcom_cci")
    cci_sha = cci_size = None
    cci_path = None
    if isinstance(cci, dict):
        cci_sha = cci.get("sha256")
        cci_size = cci.get("size")
        cci_path = cci.get("path")
    elif isinstance(cci, str):
        cci_sha = cci
    if not cci_sha:
        for key in ("i2c_qcom_cci_sha256", "cci_sha256"):
            if data.get(key):
                cci_sha = data[key]
                break
    if not cci_sha:
        found = find_key(data, "i2c_qcom_cci")
        if isinstance(found, dict):
            cci_sha = found.get("sha256")
            cci_size = cci_size or found.get("size")
            cci_path = cci_path or found.get("path")
        elif isinstance(found, str):
            cci_sha = found

    boot = data.get("boot_img")
    boot_sha = boot.get("sha256") if isinstance(boot, dict) else None
    if not isinstance(cci_sha, str):
        cci_sha = None
    return {
        "release": release,
        "cci_sha256": cci_sha,
        "cci_size": cci_size,
        "cci_path": cci_path,
        "boot_sha256": boot_sha,
    }


# ------------------------------------------------------------------ identity

def compare_expected(observed: Any, expected: Any) -> str:
    if is_missing(observed):
        return "UNKNOWN"
    if is_missing(expected):
        return "UNKNOWN"
    return "MATCH" if str(observed).strip() == str(expected).strip() else "MISMATCH"


def identity_block(
    meta: dict[str, str],
    sections: dict[str, list[str]],
    manifest: dict[str, Any] | None,
    manifest_error: str | None,
) -> dict[str, Any]:
    expected = manifest_expectations(manifest)
    cci = parse_kv(section(sections, "CCI_MODULE"))
    observed_release = meta.get("kernel_release")
    observed_sha = cci.get("sha256")
    observed_size = cci.get("size")
    checks = {
        "release": compare_expected(observed_release, expected.get("release")),
        "cci_sha256": compare_expected(observed_sha, expected.get("cci_sha256")),
    }
    if expected.get("cci_size") is not None:
        checks["cci_size"] = compare_expected(observed_size, expected.get("cci_size"))
    packages = [
        line.strip()
        for line in section(sections, "PACKAGES").splitlines()
        if line.strip() and not line.startswith(("[", "$"))
    ]
    return {
        "observed_release": observed_release,
        "observed_kernel_version": meta.get("kernel_version"),
        "observed_boot_cmdline": meta.get("boot_cmdline"),
        "observed_cci_path": cci.get("path"),
        "observed_cci_size": observed_size,
        "observed_cci_sha256": observed_sha,
        "expected": expected,
        "checks": checks,
        "manifest_error": manifest_error,
        "packages": packages,
    }


# -------------------------------------------------------------------- camera

def parse_processes(sections: dict[str, list[str]]) -> list[dict[str, Any]]:
    body = section(sections, "CAMERA_PROCESSES")
    processes: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in body.splitlines():
        if line.startswith("PROCESS "):
            fields = dict(re.findall(r"(\w+)=(\S+)", line[len("PROCESS "):]))
            current = {
                "pid": fields.get("pid"),
                "comm": fields.get("comm"),
                "exe": fields.get("exe"),
                "devices": fields.get("devices"),
                "cmdline": "",
                "env": [],
                "maps": [],
            }
            processes.append(current)
        elif current is not None and line.startswith("CMDLINE "):
            current["cmdline"] = line[len("CMDLINE "):]
        elif current is not None and line.startswith("ENV "):
            current["env"].append(line[len("ENV "):])
        elif current is not None and line.startswith("MAP "):
            mapped = line[len("MAP "):].strip()
            if mapped.startswith("/"):
                current["maps"].append(mapped)
    return processes


def parse_loaded(sections: dict[str, list[str]]) -> list[dict[str, Any]]:
    body = section(sections, "LIBCAMERA_LIBS")
    loaded: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in body.splitlines():
        if line.startswith("LOADED "):
            current = {"path": line[len("LOADED "):].strip()}
            loaded.append(current)
        elif current is not None and line.startswith("  "):
            key, _, value = line.strip().partition("=")
            if key and value:
                current[key] = value
    return loaded


def camera_paths(path: str) -> bool:
    lower = path.lower()
    return "libcamera" in lower or "spa-libcamera" in lower


def classify_paths(paths: list[str]) -> tuple[str, dict[str, list[str]]]:
    private: list[str] = []
    system: list[str] = []
    other: list[str] = []
    for path in paths:
        if not camera_paths(path):
            continue
        lower = path.lower()
        if "/opt/nura-rear-camera/" in lower:
            private.append(path)
        elif lower.startswith("/usr/lib/"):
            system.append(path)
        else:
            other.append(path)

    versions = sorted({m.group(1).rstrip(".") for p in paths for m in FRAME_RE.finditer(p)})
    if private and system:
        classification = "mixed"
    elif private:
        classification = "private"
    elif system:
        classification = "system"
    elif other:
        classification = "other"
    else:
        classification = "unknown"

    if classification == "private" and any(v.startswith("0.7.2") for v in versions):
        classification = "private-0.7.2"
    elif classification == "system" and any(v.startswith("0.8") for v in versions):
        classification = "system-0.8"
    return classification, {"private": private, "system": system, "other": other, "versions": versions}


def camera_stack_block(sections: dict[str, list[str]]) -> dict[str, Any]:
    processes = parse_processes(sections)
    loaded = parse_loaded(sections)
    loaded_paths = [item["path"] for item in loaded]
    process_paths = [path for process in processes for path in process.get("maps", [])]
    all_paths = sorted(set(loaded_paths + process_paths))
    classification, groups = classify_paths(all_paths)

    for process in processes:
        stack, _ = classify_paths(process.get("maps", []))
        process["stack"] = stack

    app_processes = [
        p for p in processes
        if not any(token in (p.get("comm") or "").lower() for token in ("pipewire", "wireplumber"))
    ]
    if not processes:
        note = "no camera process or device holder observed; installed packages alone do not identify the live stack"
    elif not all_paths:
        note = "camera process observed but no libcamera/spa-libcamera mapping found"
    elif classification == "mixed":
        note = "both private /opt/nura-rear-camera and system /usr/lib camera libraries are mapped"
    else:
        note = "classification is from live process maps, not from installed package names"
    return {
        "classification": classification,
        "groups": groups,
        "loaded": loaded,
        "processes": processes,
        "app_processes": app_processes,
        "all_paths": all_paths,
        "note": note,
    }


# -------------------------------------------------------------------- errors

ERROR_PATTERNS = {
    "cci": re.compile(r"i2c-qcom-cci.*(timeout|reset|error|fail)|cci.*reset timeout", re.I),
    "oops": re.compile(r"\bOops\b|kernel BUG|BUG:|Unable to handle kernel|watchdog: BUG", re.I),
    "camss": re.compile(r"camss.*(overflow|underrun|error|fail|timeout)", re.I),
    "venus": re.compile(r"venus.*(error|fail|timeout)", re.I),
    "thermal": re.compile(r"thermal.*(throttl|critical)|temperature.*critical", re.I),
}


def scan_errors(sections: dict[str, list[str]]) -> dict[str, Any]:
    text = section(sections, "DMESG") + "\n" + section(sections, "JOURNAL")
    lines = [line for line in text.splitlines() if line.strip()]
    captured = any(
        not line.strip().startswith(META_LINE_PREFIXES) and not line.strip().startswith(("#", "$"))
        for line in lines
    )
    matches: dict[str, list[str]] = {}
    for name, pattern in ERROR_PATTERNS.items():
        found = [line for line in lines if pattern.search(line)]
        if found:
            matches[name] = found[:10]
    if matches:
        status = "OBSERVED_ERRORS"
    elif captured:
        status = "NO_MATCH_IN_CAPTURED_LOG"
    else:
        status = "UNKNOWN"
    return {
        "status": status,
        "captured": captured,
        "matches": matches,
        "window_note": (
            "absence of matching lines is not proof of health: the probe only captures a filtered "
            "dmesg/journal window, and [DENIED] means no log was captured"
        ),
    }


# --------------------------------------------------------------- regressions

DOMAINS = (
    ("speakers_mic", "AUDIO"),
    ("wifi", "WIFI"),
    ("bluetooth", "BLUETOOTH"),
    ("charging", "CHARGING"),
    ("suspend", "SUSPEND"),
)


def collection_status(body: str) -> str:
    lines = [
        line.strip() for line in body.splitlines()
        if line.strip() and not line.strip().startswith(("#", "$"))
    ]
    if not lines:
        return "MISSING"
    meta_only = all(
        line.startswith(("[MISSING]", "[DENIED]", "[SKIPPED]", "[NO_MATCH]", "hint:"))
        for line in lines
    )
    if meta_only:
        if any(line.startswith("[DENIED]") for line in lines):
            return "DENIED"
        if any(line.startswith("[SKIPPED]") for line in lines):
            return "SKIPPED"
        return "MISSING"
    return "OBSERVED"


def regression_notes(domain: str, body: str) -> list[str]:
    notes: list[str] = []
    if domain in {"wifi", "bluetooth"} and re.search(r"(Soft|Hard) blocked:\s*yes", body, re.I):
        notes.append("rfkill reports a blocked radio; user observation must confirm the functional state")
    if domain == "charging":
        values = parse_kv(body)
        limit = values.get("input_current_limit")
        if limit is not None and limit.strip() == "0":
            notes.append("an input_current_limit of 0 was observed; the charger daemon may be suspending input")
        if re.search(r"status=Discharging", body) and re.search(r"online=1", body):
            notes.append("a supply is online while a battery reports Discharging; check charger policy")
    if domain == "suspend":
        values = parse_kv(body)
        fail = as_int(values.get("fail"))
        if fail is not None and fail > 0:
            notes.append(f"suspend_stats.fail is {fail} (cumulative; not necessarily this session)")
        if values.get("last_failed_dev"):
            notes.append(f"last_failed_dev={values['last_failed_dev']}")
    if domain == "speakers_mic":
        if "no soundcards" in body.lower():
            notes.append("ALSA reports no soundcards")
        if "AUDIO_I2C" not in body:
            notes.append("no speaker/amp codec I2C entry was listed")
    return notes


def regressions_block(sections: dict[str, list[str]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for domain, name in DOMAINS:
        body = section(sections, name)
        status = collection_status(body)
        result[domain] = {
            "section": name,
            "collection": status,
            "functional": "NOT_TESTED",
            "notes": regression_notes(domain, body),
        }
    return result


# --------------------------------------------------------------------- watch

WATCH_COLUMNS = (
    "epoch", "load1", "load5", "psi_cpu_some", "psi_io_some",
    "cpu_max_khz", "cpu_gov", "gpu_hz", "gpu_gov", "thermal_max_c",
    "batt_status", "batt_capacity", "usb_online", "input_limit_ua",
)


def parse_watch(sections: dict[str, list[str]]) -> list[dict[str, Any]]:
    body = section(sections, "WATCH")
    rows: list[dict[str, Any]] = []
    for line in body.splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        parts = text.split()
        if len(parts) != len(WATCH_COLUMNS) or not parts[0].isdigit():
            continue
        row: dict[str, Any] = {}
        for key, value in zip(WATCH_COLUMNS, parts):
            if key in {"cpu_gov", "gpu_gov", "batt_status"}:
                row[key] = None if value == "NA" else value
            elif key in {"epoch", "usb_online", "batt_capacity"}:
                row[key] = as_int(value)
            else:
                row[key] = as_float(value)
        rows.append(row)
    return rows


def watch_block(sections: dict[str, list[str]]) -> dict[str, Any]:
    rows = parse_watch(sections)
    if not rows:
        return {
            "status": "MISSING",
            "samples": 0,
            "thresholds": THRESHOLDS,
            "flags": [],
            "note": "no --watch samples; CPU/GPU/thermal correlation is not available",
        }
    temps = [r["thermal_max_c"] for r in rows if r["thermal_max_c"] is not None]
    psi = [r["psi_cpu_some"] for r in rows if r["psi_cpu_some"] is not None]
    cpu = [r["cpu_max_khz"] for r in rows if r["cpu_max_khz"] and r["cpu_max_khz"] > 0]
    gpu = [r["gpu_hz"] for r in rows if r["gpu_hz"] and r["gpu_hz"] > 0]
    caps = [r["batt_capacity"] for r in rows if r["batt_capacity"] is not None]
    limits = {r["input_limit_ua"] for r in rows if r["input_limit_ua"] is not None}
    charging = sum(1 for r in rows if r["usb_online"] == 1)
    flags: list[str] = []
    if temps and max(temps) >= THRESHOLDS["thermal_attention_c"]:
        flags.append(f"thermal_max_at_or_above_{THRESHOLDS['thermal_attention_c']:g}C")
    if psi and max(psi) >= THRESHOLDS["cpu_psi_attention"]:
        flags.append(f"cpu_psi_some_at_or_above_{THRESHOLDS['cpu_psi_attention']:g}")
    if cpu and min(cpu) / max(cpu) < THRESHOLDS["cpu_freq_drop_ratio"]:
        flags.append("cpu_freq_dropped_below_half_of_window_max")
    if caps and min(caps) <= THRESHOLDS["battery_low_percent"]:
        flags.append("battery_at_or_below_15_percent")
    if 0.0 in {float(x) for x in limits}:
        flags.append("input_current_limit_zero_observed")
    if charging:
        flags.append("usb_online_during_watch")
    return {
        "status": "OBSERVED",
        "samples": len(rows),
        "duration_s": (rows[-1]["epoch"] - rows[0]["epoch"]) if rows[-1]["epoch"] and rows[0]["epoch"] else None,
        "thermal_max_c": max(temps) if temps else None,
        "thermal_median_c": statistics.median(temps) if temps else None,
        "psi_cpu_max": max(psi) if psi else None,
        "psi_cpu_median": statistics.median(psi) if psi else None,
        "cpu_max_khz_min": min(cpu) if cpu else None,
        "cpu_max_khz_max": max(cpu) if cpu else None,
        "gpu_hz_min": min(gpu) if gpu else None,
        "gpu_hz_max": max(gpu) if gpu else None,
        "battery_capacity_min": min(caps) if caps else None,
        "charging_samples": charging,
        "input_limit_values": sorted(limits),
        "flags": flags,
        "thresholds": THRESHOLDS,
        "note": "thresholds are observation aids, not device limits",
    }


# -------------------------------------------------------------------- videos

def discover_videos(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if Path(name).suffix.lower() in VIDEO_EXTENSIONS:
                found.append(Path(dirpath) / name)
    return found


def sidecar_for(path: Path) -> Path | None:
    candidates = []
    for suffix in (".txt", ".md", ".json"):
        candidates.append(path.with_suffix(suffix))
        candidates.append(Path(str(path) + suffix))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def parse_sidecar(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:8192]
    except (OSError, PermissionError):
        return {}
    values: dict[str, str] = {}
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            for key, value in data.items():
                values[str(key).strip().lower()] = str(value).strip()
            return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            continue
        key = key.strip().lower()
        if key and " " not in key:
            values[key] = value.strip()
    return values


def sensor_from_text(text: str) -> str:
    tokens = re.findall(r"[a-z]+", text.lower())
    labels = {SENSOR_ALIASES[token] for token in tokens if token in SENSOR_ALIASES}
    if len(labels) == 1:
        return labels.pop()
    if len(labels) > 1:
        return "ambiguous"
    return "unlabeled"


def video_sensor(path: Path, sidecar: dict[str, str]) -> str:
    explicit = sidecar.get("sensor") or sidecar.get("lens") or sidecar.get("camera")
    if explicit:
        mapped = SENSOR_ALIASES.get(explicit.strip().lower())
        if mapped:
            return mapped
    return sensor_from_text(path.stem)


def normalize_symptom(value: str | None) -> str:
    text = (value or "").strip().lower()
    return text if text in SYMPTOMS else "unknown"


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def analyze_frame_times(times: list[float], nominal_fps: float = 0.0) -> dict[str, Any]:
    ordered = sorted(float(t) for t in times)
    if len(ordered) < 5:
        return {
            "status": "INSUFFICIENT_FRAMES",
            "frame_count": len(ordered),
            "detail": "fewer than five frame timestamps",
        }
    intervals = [b - a for a, b in zip(ordered, ordered[1:])]
    median = statistics.median(intervals)
    if median <= 0:
        return {"status": "INSUFFICIENT_FRAMES", "frame_count": len(ordered), "detail": "non-positive frame interval"}
    span = ordered[-1] - ordered[0]
    effective = (len(ordered) - 1) / span if span > 0 else 0.0
    nominal = nominal_fps if nominal_fps and nominal_fps > 0 else 1.0 / median
    ratio = effective / nominal if nominal > 0 else None
    gap_threshold = max(THRESHOLDS["gap_median_multiplier"] * median, median + 0.005)
    gap_intervals = [(index, delta) for index, delta in enumerate(intervals) if delta > gap_threshold]
    inferred_missing = sum(max(0, round(delta / median) - 1) for _, delta in gap_intervals)
    largest = sorted(gap_intervals, key=lambda item: item[1], reverse=True)[:5]
    if ratio is None:
        status = "INSUFFICIENT_FRAMES"
    elif gap_intervals:
        status = "PROBED_GAPS"
    elif ratio < THRESHOLDS["fps_regular_ratio"]:
        status = "PROBED_LOW_RATE"
    else:
        status = "PROBED_REGULAR"
    return {
        "status": status,
        "frame_count": len(ordered),
        "window_s": span,
        "nominal_fps": nominal,
        "effective_fps": effective,
        "fps_ratio": ratio,
        "interval_median_ms": median * 1000.0,
        "interval_p95_ms": (percentile(intervals, 0.95) or 0.0) * 1000.0,
        "interval_p99_ms": (percentile(intervals, 0.99) or 0.0) * 1000.0,
        "interval_max_ms": max(intervals) * 1000.0,
        "gap_count": len(gap_intervals),
        "gap_threshold_ms": gap_threshold * 1000.0,
        "inferred_missing_frames": inferred_missing,
        "largest_gaps": [
            {
                "at_s": ordered[index],
                "gap_ms": delta * 1000.0,
                "inferred_missing": max(0, round(delta / median) - 1),
            }
            for index, delta in largest
        ],
    }


def parse_rate(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"0/0", "N/A"}:
        return None
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        try:
            num = float(numerator)
            den = float(denominator)
        except ValueError:
            return None
        return num / den if den else None
    return as_float(text)


def ffprobe_available(binary: str) -> bool:
    if not binary:
        return False
    path = Path(binary)
    if path.is_absolute() or os.sep in binary:
        return path.is_file() and os.access(path, os.X_OK)
    return shutil.which(binary) is not None


def run_ffprobe(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout, result.stderr
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"


def parse_frame_timestamps(stdout: str) -> list[float]:
    times: list[float] = []
    for line in stdout.splitlines():
        fields = line.strip().split(",")
        for field in fields[:2]:
            if not field:
                continue
            try:
                times.append(float(field))
                break
            except ValueError:
                continue
    return times


def probe_video(path: Path, ffprobe_bin: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "file": path.name,
        "path": str(path),
        "status": "UNKNOWN",
        "detail": "",
    }
    try:
        size = path.stat().st_size
    except (OSError, PermissionError) as exc:
        result.update(status="UNREADABLE", detail=f"stat failed: {exc}")
        return result
    result["size_bytes"] = size
    if size == 0:
        result.update(status="UNREADABLE", detail="zero-byte file")
        return result
    if not ffprobe_available(ffprobe_bin):
        result.update(status="TOOL_MISSING", detail=f"ffprobe not found: {ffprobe_bin}")
        return result

    meta_cmd = [
        ffprobe_bin, "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate,r_frame_rate,nb_frames,duration,bit_rate:format=duration,size,bit_rate",
        "-of", "json", str(path),
    ]
    rc, stdout, stderr = run_ffprobe(meta_cmd)
    if rc != 0:
        result.update(status="ERROR", detail=f"ffprobe metadata rc={rc}: {stderr.strip()[:300]}")
        return result
    try:
        metadata = json.loads(stdout)
    except json.JSONDecodeError as exc:
        result.update(status="ERROR", detail=f"ffprobe JSON: {exc}")
        return result
    streams = metadata.get("streams") or []
    if not streams:
        result.update(status="ERROR", detail="no video stream")
        return result
    stream = streams[0]
    fmt = metadata.get("format") or {}
    result.update(
        codec=stream.get("codec_name"),
        width=as_int(stream.get("width")),
        height=as_int(stream.get("height")),
        nominal_fps=parse_rate(stream.get("avg_frame_rate")) or parse_rate(stream.get("r_frame_rate")),
        reported_frames=as_int(stream.get("nb_frames")),
        duration_s=as_float(stream.get("duration")) or as_float(fmt.get("duration")),
        bit_rate=as_int(stream.get("bit_rate")) or as_int(fmt.get("bit_rate")),
    )

    frame_cmd = [
        ffprobe_bin, "-v", "error", "-select_streams", "v:0",
        "-read_intervals", "%+15",
        "-show_entries", "frame=best_effort_timestamp_time,pkt_dts_time,pkt_duration_time",
        "-of", "csv=p=0", str(path),
    ]
    rc, stdout, stderr = run_ffprobe(frame_cmd)
    if rc != 0:
        retry_cmd = [
            ffprobe_bin, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "frame=best_effort_timestamp_time,pkt_dts_time,pkt_duration_time",
            "-of", "csv=p=0", str(path),
        ]
        rc, stdout, stderr = run_ffprobe(retry_cmd)
    if rc != 0:
        result.update(status="ERROR", detail=f"ffprobe frames rc={rc}: {stderr.strip()[:300]}")
        return result

    times = parse_frame_timestamps(stdout)
    frames = analyze_frame_times(times, result.get("nominal_fps") or 0.0)
    result.update(frames)
    result["analyzed_frames"] = len(times)
    result["analysis_window_s"] = frames.get("window_s")
    result["analysis_covers_full_clip"] = bool(
        result.get("duration_s") and frames.get("window_s") is not None
        and frames["window_s"] >= result["duration_s"] - 0.5
    )
    if result.get("nominal_fps") is None:
        result["nominal_fps"] = frames.get("nominal_fps")
    return result


def decorate_video(video: dict[str, Any], sidecar_path: Path | None, sidecar: dict[str, str], default_symptom: str) -> None:
    video["sensor"] = video_sensor(Path(video["path"]), sidecar)
    video["symptom"] = normalize_symptom(sidecar.get("symptom") or default_symptom)
    video["sidecar"] = sidecar_path.name if sidecar_path else None
    video["notes"] = sidecar.get("notes") or sidecar.get("note") or ""
    video["wall_seconds"] = as_float(sidecar.get("wall_seconds") or sidecar.get("wall_s"))
    setting_fps = as_float(sidecar.get("fps") or sidecar.get("fps_setting") or sidecar.get("framerate"))
    video["setting_fps"] = setting_fps
    if setting_fps and video.get("nominal_fps"):
        video["setting_match"] = abs(setting_fps - video["nominal_fps"]) <= 0.5
    else:
        video["setting_match"] = None
    if setting_fps and video.get("effective_fps"):
        requested_ratio = video["effective_fps"] / setting_fps
        video["requested_fps_ratio"] = requested_ratio
        video["below_requested_fps"] = requested_ratio < THRESHOLDS["fps_regular_ratio"]
        if video["below_requested_fps"] and video.get("status") == "PROBED_REGULAR":
            video["status"] = "PROBED_LOW_RATE"
    else:
        video["requested_fps_ratio"] = None
        video["below_requested_fps"] = None
    if video.get("wall_seconds") and video.get("duration_s"):
        ratio = video["duration_s"] / video["wall_seconds"]
        video["capture_ratio"] = ratio
        video["wall_clock_shortfall"] = ratio < THRESHOLDS["wall_clock_shortfall_ratio"]
    else:
        video["capture_ratio"] = None
        video["wall_clock_shortfall"] = None
    video["sidecar_keys"] = sorted(sidecar.keys())


def videos_block(recordings: Path | None, ffprobe_bin: str, default_symptom: str) -> list[dict[str, Any]]:
    if recordings is None:
        return []
    videos: list[dict[str, Any]] = []
    for path in discover_videos(recordings):
        sidecar_path = sidecar_for(path)
        sidecar = parse_sidecar(sidecar_path)
        video = probe_video(path, ffprobe_bin)
        video["file"] = str(path.relative_to(recordings)) if path.is_relative_to(recordings) else path.name
        decorate_video(video, sidecar_path, sidecar, default_symptom)
        videos.append(video)
    return videos


# --------------------------------------------------------------- aggregation

def aggregate_sensors(videos: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for sensor in SENSORS:
        clips = [v for v in videos if v.get("sensor") == sensor]
        result[sensor] = aggregate_clip_set(clips)
    result["unlabeled"] = aggregate_clip_set([v for v in videos if v.get("sensor") == "unlabeled"])
    result["ambiguous"] = aggregate_clip_set([v for v in videos if v.get("sensor") == "ambiguous"])
    return result


def aggregate_clip_set(clips: list[dict[str, Any]]) -> dict[str, Any]:
    if not clips:
        return {"status": "MISSING", "clips": []}
    statuses = [clip.get("status") for clip in clips]
    if any(status in {"PROBED_GAPS", "PROBED_LOW_RATE"} for status in statuses):
        status = "CHOP_OBSERVED"
    elif all(status == "PROBED_REGULAR" for status in statuses):
        status = "REGULAR"
    else:
        status = "PARTIAL"
    fps = [clip["effective_fps"] for clip in clips if clip.get("effective_fps") is not None]
    gaps = [clip.get("gap_count") or 0 for clip in clips]
    longest = [clip.get("interval_max_ms") for clip in clips if clip.get("interval_max_ms") is not None]
    return {
        "status": status,
        "clips": [clip.get("file") for clip in clips],
        "effective_fps_min": min(fps) if fps else None,
        "effective_fps_median": statistics.median(fps) if fps else None,
        "gap_count_total": sum(gaps),
        "longest_interval_ms": max(longest) if longest else None,
    }


def overall_verdict(videos: list[dict[str, Any]], per_sensor: dict[str, Any]) -> str:
    labeled = [v for v in videos if v.get("sensor") in SENSORS]
    if not labeled:
        return "INSUFFICIENT_DATA"
    if any(v.get("status") in {"PROBED_GAPS", "PROBED_LOW_RATE"} for v in labeled):
        return "CHOP_OBSERVED"
    if any(v.get("status") not in PROBED_STATUSES for v in labeled):
        return "PARTIAL"
    present = sum(1 for sensor in SENSORS if per_sensor.get(sensor, {}).get("status") != "MISSING")
    if present < len(SENSORS):
        return "PARTIAL"
    return "NO_CHOP_DETECTED_IN_ANALYZED_WINDOW"


def symptom_block(videos: list[dict[str, Any]], default_symptom: str) -> dict[str, Any]:
    labeled = [v for v in videos if v.get("sensor") in SENSORS]
    symptoms = sorted({v.get("symptom") for v in labeled if v.get("symptom") and v.get("symptom") != "unknown"})
    if len(symptoms) > 1:
        effective = "mixed"
    elif len(symptoms) == 1:
        effective = symptoms[0]
    else:
        effective = normalize_symptom(default_symptom)
    probed = [v for v in labeled if v.get("status") in PROBED_STATUSES]
    has_gaps = any(v.get("status") == "PROBED_GAPS" for v in probed)
    if not probed:
        label = "INSUFFICIENT_DATA"
        statement = "no probed recording is available to relate the reported symptom to timestamps"
    elif effective == "unknown":
        label = "UNKNOWN"
        statement = "the user-reported symptom (preview, recording, playback) was not recorded"
    elif effective == "preview":
        if all(v["status"] == "PROBED_REGULAR" for v in probed):
            label = "PREVIEW_PATH_SUSPECT"
            statement = "recorded timestamps are regular while preview is reported choppy; preview/display/GPU composition is the first path to check"
        elif has_gaps:
            label = "CAPTURE_OR_PREVIEW_SHARED"
            statement = "recorded files show timestamp gaps; capture/ISP is shared by preview and recording"
        else:
            label = "CAPTURE_RATE_BELOW_REQUEST"
            statement = "recorded files are below the requested/expected rate without timestamp gaps; capture/ISP or encode throughput is the first path to check"
    elif effective == "recording":
        if all(v["status"] == "PROBED_REGULAR" for v in probed):
            label = "FILE_REGULAR_CHECK_PLAYBACK"
            statement = "recorded timestamps are regular although recording seemed choppy; compare playback on host and phone before blaming capture"
        elif has_gaps:
            label = "CAPTURE_OR_ENCODE_SUSPECT"
            statement = "recorded files show timestamp gaps; capture/ISP or encoder throughput is implicated"
        else:
            label = "CAPTURE_RATE_BELOW_REQUEST"
            statement = "recorded files are below the requested/expected rate without gaps; capture/ISP or encoder throughput is implicated"
    elif effective == "playback":
        if all(v["status"] == "PROBED_REGULAR" for v in probed):
            label = "PLAYBACK_DECODE_SUSPECT"
            statement = "recorded timestamps are regular; playback/decode or display is the supported suspect"
        else:
            label = "FILE_AND_PLAYBACK"
            statement = "the file itself has irregular timestamps or a low rate, so playback is not the only issue"
    elif effective == "both":
        if all(v["status"] == "PROBED_REGULAR" for v in probed):
            label = "PREVIEW_AND_PLAYBACK_SUSPECT"
            statement = "recorded timestamps are regular; preview and playback paths share display/GPU concerns"
        else:
            label = "CAPTURE_ENCODE_SUSPECT"
            statement = "both symptoms and irregular file timestamps point first to capture/encode throughput"
    else:
        label = "MIXED_SYMPTOMS"
        statement = "different clips report different symptoms; analyze each sensor separately"
    return {"effective": effective, "per_clip": symptoms, "label": label, "statement": statement}


# --------------------------------------------------------------------- triage

def build_triage(analysis: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if analysis["verdict"] == "INSUFFICIENT_DATA":
        items.append({
            "confidence": "high",
            "statement": "No fix recommendation is supported: there is no labeled, probed recording to analyze.",
            "next_step": "Record one short comparable clip per rear sensor (main/tele/wide) and re-run this analysis; do not change AF, rails or calibration.",
        })
    identity = analysis["identity"]
    checks = identity["checks"]
    if "MISMATCH" in checks.values():
        items.append({
            "confidence": "high",
            "statement": "The running kernel/module identity does not match the supplied build manifest.",
            "next_step": "Refresh the served image/rootfs from the intended manifest and reboot before profiling; do not interpret FPS from this boot.",
        })
    stack = analysis["camera_stack"]["classification"]
    if stack in {"unknown", "other"}:
        items.append({
            "confidence": "medium",
            "statement": "The live libcamera stack was not identified from process maps; installed package names are not evidence of what the app loaded.",
            "next_step": "Open the camera app, re-run the probe, and keep the process-map table; if PipeWire owns the camera, inspect the pipewire process maps too.",
        })
    elif stack == "mixed":
        items.append({
            "confidence": "high",
            "statement": "Both the private /opt/nura-rear-camera stack and the system /usr/lib stack are mapped by running processes.",
            "next_step": "Identify which process owns the camera device and which stack it maps before changing either path; do not downgrade or mix system libcamera.",
        })
    errors = analysis["errors"]
    if errors["status"] == "OBSERVED_ERRORS":
        items.append({
            "confidence": "high",
            "statement": "The captured logs contain camera/CCI/oops/thermal error strings.",
            "next_step": "Inspect the full timestamped dmesg/journal and verify the manifest identity first; do not change rails or AF.",
        })
    mismatched = [v for v in analysis["videos"] if v.get("setting_match") is False]
    if mismatched:
        names = ", ".join(str(v.get("file")) for v in mismatched[:4])
        items.append({
            "confidence": "medium",
            "statement": f"The container rate differs from the app-requested fps for: {names}.",
            "next_step": "Confirm the exact app mode/setting and repeat the clip; the analyzer does not assume the app honored its requested fps.",
        })
    choppy = [v for v in analysis["videos"] if v.get("status") in {"PROBED_GAPS", "PROBED_LOW_RATE"}]
    if choppy:
        watch = analysis["watch"]
        flags = set(watch.get("flags") or [])
        if "thermal_max_at_or_above_75C" in flags:
            items.append({
                "confidence": "medium",
                "statement": "Recording timestamps show gaps/low rate and the watch samples reached the thermal attention threshold.",
                "next_step": "Repeat a short clip at a lower resolution/fps as a control, with the same scene and charging state; record the watch log. Do not change governors or power settings.",
            })
        elif "cpu_psi_some_at_or_above_50" in flags or "cpu_freq_dropped_below_half_of_window_max" in flags:
            items.append({
                "confidence": "medium",
                "statement": "Recording timestamps show gaps/low rate while CPU pressure/frequency indicates a host throughput limit.",
                "next_step": "Repeat a lower-resolution/fps control clip and capture the same watch log; determine whether software ISP/encode is the limiter before proposing a code change.",
            })
        else:
            items.append({
                "confidence": "low",
                "statement": "Recording timestamps show gaps or a low effective rate without a clear CPU/thermal signal in the watch samples.",
                "next_step": "Collect app/libcamera and kernel logs around the clip, and compare all three sensors with the same scene; no source fix is supported yet.",
            })
    elif analysis["videos"]:
        symptom = analysis["symptom"]["label"]
        if symptom == "PREVIEW_PATH_SUSPECT":
            items.append({
                "confidence": "medium",
                "statement": "Files are timestamp-regular while preview is reported choppy.",
                "next_step": "Compare a still preview with a recorded file and inspect display/GPU composition; do not change the sensor or actuator path.",
            })
        elif symptom == "PLAYBACK_DECODE_SUSPECT":
            items.append({
                "confidence": "medium",
                "statement": "Files are timestamp-regular; playback/decode is the supported suspect.",
                "next_step": "Play the same file on the host and the phone and record both observations; no capture-side fix is supported yet.",
            })
        elif symptom == "FILE_REGULAR_CHECK_PLAYBACK":
            items.append({
                "confidence": "low",
                "statement": "The file is timestamp-regular although recording seemed choppy.",
                "next_step": "Play the file on the host and phone; if host playback is smooth, treat this as playback/decode rather than capture.",
            })
    missing = [s for s in SENSORS if analysis["per_sensor"].get(s, {}).get("status") == "MISSING"]
    if missing:
        items.append({
            "confidence": "high",
            "statement": "No labeled recording was analyzed for: " + ", ".join(missing) + ".",
            "next_step": "Record one short, comparable scene per sensor and name the files main/tele/wide; do not infer all-sensor behavior from the available clips.",
        })
    if analysis["watch"]["status"] == "MISSING":
        items.append({
            "confidence": "medium",
            "statement": "No CPU/GPU/thermal watch samples were supplied.",
            "next_step": "Start `sh nura-probe.sh --watch 60` before a recording, then record; the watch is read-only and does not start the camera.",
        })
    if analysis["symptom"]["effective"] == "unknown" and analysis["videos"]:
        items.append({
            "confidence": "medium",
            "statement": "The preview/recording/playback symptom was not recorded.",
            "next_step": "Add a sidecar next to each clip with sensor=, symptom= and wall_seconds= so the symptom matrix can separate paths.",
        })
    if not items:
        items.append({
            "confidence": "low",
            "statement": "No source or configuration fix is supported by this snapshot.",
            "next_step": "Collect the missing labeled clips/watch/log data; keep the current boot and do not change AF, rails or calibration.",
        })
    return items[:6]


# --------------------------------------------------------------------- report

def md(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value).replace("|", "\\|").replace("\n", " ")


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(md(value) for value in row) + " |")
    return "\n".join(lines)


LIMITATIONS = [
    "Read-only analysis of user-collected text and files; this tool did not touch the phone.",
    "No hardware, AF, calibration, suspend, FPS or playback validation is claimed.",
    "Timestamp gaps are inferred from container timestamps; they are not an encoder's dropped-frame counter.",
    "The analyzed frame window is capped at 15 s per clip; longer clips are reported as partial coverage.",
    "A regular file does not prove visual smoothness; a choppy file does not alone identify capture vs encode.",
    "Package presence is not used to identify the live libcamera stack; only process maps are.",
]


def render_report(analysis: dict[str, Any]) -> str:
    identity = analysis["identity"]
    stack = analysis["camera_stack"]
    errors = analysis["errors"]
    watch = analysis["watch"]
    regressions = analysis["regressions"]
    symptom = analysis["symptom"]
    lines: list[str] = [
        "# Nura recording / regression diagnostics",
        "",
        f"Generated: `{analysis['generated_utc']}`  ",
        f"Snapshot: `{analysis['inputs'].get('snapshot')}`  ",
        f"Manifest: `{analysis['inputs'].get('manifest') or 'not supplied'}`  ",
        f"Recordings: `{analysis['inputs'].get('recordings') or 'not supplied'}`",
        "",
        f"**Verdict: `{analysis['verdict']}`**",
        "",
        "> Read-only analysis of user-collected data. This is not hardware validation. "
        "No `PASS` verdict is emitted; missing tools, permissions or data remain `UNKNOWN`, "
        "`MISSING` or `PARTIAL`.",
        "",
        "## Identity",
        "",
        table(
            ["Item", "Observed", "Expected (manifest)", "Check"],
            [
                ["kernel release", identity["observed_release"], identity["expected"].get("release"), identity["checks"].get("release")],
                ["CCI sha256", identity["observed_cci_sha256"], identity["expected"].get("cci_sha256"), identity["checks"].get("cci_sha256")],
                ["CCI size", identity["observed_cci_size"], identity["expected"].get("cci_size"), identity["checks"].get("cci_size", "—")],
                ["CCI path", identity["observed_cci_path"], identity["expected"].get("cci_path"), "—"],
            ],
        ),
        "",
    ]
    if identity.get("manifest_error"):
        lines += [f"Manifest error: `{identity['manifest_error']}`", ""]
    if identity.get("packages"):
        lines += ["Installed camera/pipewire packages (context only):", "", "```", *identity["packages"][:20], "```", ""]
    lines += [
        "## Live camera stack",
        "",
        f"Classification: **`{stack['classification']}`** — {stack['note']}.",
        "",
    ]
    if stack["processes"]:
        rows = []
        for process in stack["processes"]:
            maps = "<br>".join(process.get("maps") or []) or "—"
            rows.append([
                process.get("pid"), process.get("comm"), process.get("devices"),
                maps, process.get("stack"),
            ])
        lines += [table(["PID", "Process", "Devices", "Mapped camera library paths", "Stack"], rows), ""]
    else:
        lines += ["No camera process or camera-device holder was observed. Open the camera app and re-run the probe.", ""]
    if stack["loaded"]:
        lines += ["Loaded library identity:", ""]
        for item in stack["loaded"]:
            lines.append(f"- `{item.get('path')}` size={md(item.get('size'))} sha256={md(item.get('sha256'))} owner={md(item.get('owner'))}")
        lines.append("")

    lines += [
        "## Recordings",
        "",
    ]
    if not analysis["videos"]:
        lines += ["No recordings were supplied. **No FPS or choppiness conclusion is possible.**", ""]
    else:
        rows = []
        for video in analysis["videos"]:
            rows.append([
                video.get("file"), video.get("sensor"), video.get("symptom"), video.get("status"),
                f"{video.get('width') or '—'}x{video.get('height') or '—'}",
                video.get("nominal_fps"), video.get("setting_fps"), video.get("setting_match"),
                video.get("effective_fps"), video.get("fps_ratio"),
                video.get("gap_count"), video.get("inferred_missing_frames"),
                video.get("interval_max_ms"), video.get("wall_clock_shortfall"),
                video.get("analysis_covers_full_clip"),
            ])
        lines += [table(
            ["File", "Sensor", "Symptom", "Status", "Size", "Nominal fps", "Setting fps",
             "Setting match", "Effective fps", "Ratio", "Gaps", "Inferred missing",
             "Max interval ms", "Wall shortfall", "Full clip"],
            rows,
        ), ""]
        for video in analysis["videos"]:
            if video.get("detail"):
                lines.append(f"- `{video['file']}`: {video['detail']}")
            if video.get("largest_gaps"):
                gaps = ", ".join(f"{gap['at_s']:.3f}s/{gap['gap_ms']:.0f}ms" for gap in video["largest_gaps"])
                lines.append(f"- `{video['file']}` largest gaps: {gaps}")
        lines.append("")
        lines += ["Per-sensor summary:", ""]
        rows = []
        for sensor in SENSORS:
            item = analysis["per_sensor"].get(sensor, {})
            rows.append([sensor, item.get("status"), ", ".join(item.get("clips") or []) or "—",
                         item.get("effective_fps_min"), item.get("effective_fps_median"),
                         item.get("gap_count_total"), item.get("longest_interval_ms")])
        lines += [table(["Sensor", "Status", "Clips", "Min fps", "Median fps", "Gaps", "Longest interval ms"], rows), ""]
        if analysis["per_sensor"].get("unlabeled", {}).get("clips"):
            lines.append(f"Unlabeled clips (not used for per-sensor conclusions): {', '.join(analysis['per_sensor']['unlabeled']['clips'])}")
            lines.append("")

    lines += [
        "## CPU / GPU / thermal watch",
        "",
    ]
    if watch["status"] == "MISSING":
        lines += [watch["note"], ""]
    else:
        lines += [
            table(
                ["Samples", "Duration s", "Thermal max C", "Thermal median C", "CPU PSI max",
                 "CPU max kHz min/max", "GPU Hz min/max", "Charging samples"],
                [[
                    watch["samples"], watch["duration_s"], watch["thermal_max_c"], watch["thermal_median_c"],
                    watch["psi_cpu_max"],
                    f"{md(watch['cpu_max_khz_min'])}/{md(watch['cpu_max_khz_max'])}",
                    f"{md(watch['gpu_hz_min'])}/{md(watch['gpu_hz_max'])}",
                    watch["charging_samples"],
                ]],
            ),
            "",
        ]
        if watch["flags"]:
            lines += ["Watch flags: " + ", ".join(f"`{flag}`" for flag in watch["flags"]), ""]
        lines += [f"_{watch['note']}_", ""]

    lines += [
        "## Regression snapshot",
        "",
        "Collection status only; functional behaviour was not tested by this tool.",
        "",
        table(
            ["Domain", "Collected", "Functional", "Notes"],
            [[
                domain,
                item["collection"],
                item["functional"],
                "; ".join(item["notes"]) or "—",
            ] for domain, item in regressions.items()],
        ),
        "",
        "## Error scan",
        "",
        f"Status: **`{errors['status']}`**",
        "",
    ]
    if errors["matches"]:
        for name, found in errors["matches"].items():
            lines.append(f"- `{name}`: {found[0][:300]}")
    elif errors["status"] == "UNKNOWN":
        lines.append("No log window was captured (`dmesg`/`journalctl` denied or empty); error status is `UNKNOWN`.")
    else:
        lines.append("No matching error strings in the captured window.")
    lines += ["", f"_{errors['window_note']}_", ""]

    lines += [
        "## Symptom matrix",
        "",
        f"- Effective symptom: `{symptom['effective']}`",
        f"- Assessment: **`{symptom['label']}`** — {symptom['statement']}",
        "",
        "## Triage (no code fix is supported by this snapshot alone)",
        "",
    ]
    for item in analysis["triage"]:
        lines.append(f"- **[{item['confidence']}]** {item['statement']}  ")
        lines.append(f"  Next: {item['next_step']}")
    lines += [
        "",
        "## What this does not establish",
        "",
    ]
    lines += [f"- {item}" for item in LIMITATIONS]
    lines += [
        "",
        "## Data still needed",
        "",
    ]
    needed: list[str] = []
    missing_sensors = [s for s in SENSORS if analysis["per_sensor"].get(s, {}).get("status") == "MISSING"]
    if not analysis["videos"]:
        needed.append("Three short, comparable clips named `main-*.mp4`, `tele-*.mp4`, `wide-*.mp4`, one sensor at a time.")
    elif missing_sensors:
        needed.append(
            "A labeled clip for each missing sensor (" + ", ".join(missing_sensors) + "); "
            "do not merge scenes or switch lenses during a clip."
        )
    if watch["status"] == "MISSING":
        needed.append("A `--watch 60` log started before the user presses record.")
    if symptom["effective"] == "unknown" and analysis["videos"]:
        needed.append("Sidecar `sensor=`, `symptom=` (`preview|recording|playback|both`) and `wall_seconds=` for each clip.")
    if not analysis["inputs"].get("manifest"):
        needed.append("The build manifest used for the boot, so release/CCI identity can be checked without hardcoded hashes.")
    if not needed:
        needed.append("No additional data is required for this snapshot; repeat after any delivery change.")
    lines += [f"- {item}" for item in needed]
    lines += [
        "",
        "---",
        "",
        "Generated by `scripts/analyze_profile.py`. The tool never contacts the phone and never writes outside `--out`.",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------------ CLI

def build_analysis(
    sections: dict[str, list[str]],
    *,
    snapshot_path: str,
    snapshot_sha256: str,
    recordings: Path | None,
    manifest: dict[str, Any] | None,
    manifest_path: str | None,
    manifest_error: str | None,
    ffprobe_bin: str,
    symptom: str,
) -> dict[str, Any]:
    meta = parse_kv(section(sections, "META"))
    identity = identity_block(meta, sections, manifest, manifest_error)
    camera_stack = camera_stack_block(sections)
    errors = scan_errors(sections)
    regressions = regressions_block(sections)
    watch = watch_block(sections)
    videos = videos_block(recordings, ffprobe_bin, symptom)
    per_sensor = aggregate_sensors(videos)
    verdict = overall_verdict(videos, per_sensor)
    symptom_info = symptom_block(videos, symptom)
    analysis: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inputs": {
            "snapshot": snapshot_path,
            "snapshot_sha256": snapshot_sha256,
            "recordings": str(recordings) if recordings else None,
            "manifest": manifest_path,
            "manifest_sha256": None,
            "ffprobe": ffprobe_bin,
        },
        "meta": meta,
        "identity": identity,
        "camera_stack": camera_stack,
        "errors": errors,
        "regressions": regressions,
        "watch": watch,
        "videos": videos,
        "per_sensor": per_sensor,
        "symptom": symptom_info,
        "verdict": verdict,
        "limitations": LIMITATIONS,
        "thresholds": THRESHOLDS,
    }
    analysis["triage"] = build_triage(analysis)
    return analysis


def analyze(
    snapshot_path: str,
    recordings: str | None = None,
    manifest_path: str | None = None,
    ffprobe_bin: str = "ffprobe",
    symptom: str = "unknown",
    dmesg_path: str | None = None,
    journal_path: str | None = None,
) -> dict[str, Any]:
    path = Path(snapshot_path)
    text = path.read_text(encoding="utf-8", errors="replace")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    sections = parse_snapshot(text)
    for name, extra_path in (("DMESG", dmesg_path), ("JOURNAL", journal_path)):
        if extra_path:
            extra = Path(extra_path).read_text(encoding="utf-8", errors="replace")
            sections.setdefault(name, []).append(extra)
    manifest, manifest_error = (None, None)
    manifest_digest = None
    if manifest_path:
        manifest, manifest_error = read_json(manifest_path)
        try:
            manifest_digest = hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()
        except (OSError, PermissionError):
            manifest_digest = None
    analysis = build_analysis(
        sections,
        snapshot_path=str(path),
        snapshot_sha256=digest,
        recordings=Path(recordings) if recordings else None,
        manifest=manifest,
        manifest_path=manifest_path,
        manifest_error=manifest_error,
        ffprobe_bin=ffprobe_bin,
        symptom=normalize_symptom(symptom),
    )
    analysis["inputs"]["manifest_sha256"] = manifest_digest
    analysis["inputs"]["dmesg"] = dmesg_path
    analysis["inputs"]["journal"] = journal_path
    if manifest:
        analysis["inputs"]["manifest_keys"] = sorted(manifest.keys())
    return analysis


def write_outputs(analysis: dict[str, Any], out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "analysis.json").write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "report.md").write_text(render_report(analysis), encoding="utf-8")
    rows = [
        ["file", "sensor", "symptom", "status", "width", "height", "nominal_fps",
         "setting_fps", "setting_match", "requested_fps_ratio", "below_requested_fps",
         "effective_fps", "fps_ratio", "gap_count", "inferred_missing", "interval_max_ms",
         "wall_seconds", "capture_ratio", "analysis_covers_full_clip"],
    ]
    for video in analysis["videos"]:
        rows.append([
            video.get("file"), video.get("sensor"), video.get("symptom"), video.get("status"),
            video.get("width"), video.get("height"), video.get("nominal_fps"),
            video.get("setting_fps"), video.get("setting_match"),
            video.get("requested_fps_ratio"), video.get("below_requested_fps"),
            video.get("effective_fps"), video.get("fps_ratio"), video.get("gap_count"),
            video.get("inferred_missing_frames"), video.get("interval_max_ms"),
            video.get("wall_seconds"), video.get("capture_ratio"),
            video.get("analysis_covers_full_clip"),
        ])
    with (out / "videos.tsv").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write("\t".join("" if value is None else str(value) for value in row) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze a read-only Nura phone snapshot and user-supplied recordings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--snapshot", required=True, help="nura-probe.sh output file")
    parser.add_argument("--out", required=True, help="directory for analysis.json, report.md, videos.tsv")
    parser.add_argument("--recordings", help="directory containing short user-recorded clips")
    parser.add_argument("--manifest", help="build manifest (for example DELIVERY.json) used for this boot")
    parser.add_argument("--dmesg", help="optional user-run `sudo dmesg` text capture")
    parser.add_argument("--journal", help="optional user-collected journal text capture")
    parser.add_argument("--ffprobe", default="ffprobe", help="ffprobe binary (default: ffprobe)")
    parser.add_argument("--symptom", choices=SYMPTOMS, default="unknown",
                        help="user-observed symptom if not present in sidecars")
    args = parser.parse_args(argv)

    try:
        analysis = analyze(
            args.snapshot,
            recordings=args.recordings,
            manifest_path=args.manifest,
            ffprobe_bin=args.ffprobe,
            symptom=args.symptom,
            dmesg_path=args.dmesg,
            journal_path=args.journal,
        )
    except (OSError, PermissionError) as exc:
        print(f"analyze_profile: cannot read input: {exc}", file=sys.stderr)
        return 2
    try:
        write_outputs(analysis, args.out)
    except OSError as exc:
        print(f"analyze_profile: cannot write output: {exc}", file=sys.stderr)
        return 2
    print(f"analyze_profile: verdict={analysis['verdict']}")
    print(f"analyze_profile: stack={analysis['camera_stack']['classification']}")
    print(f"analyze_profile: wrote {Path(args.out) / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
