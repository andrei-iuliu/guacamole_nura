#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build and verify the ABL DTBO quarantine wrapper for the Nura DTB.

The OnePlus ABL always applies the active slot's stock DTBO when Linux is
booted from a downloaded v2 boot image.  The Nura DTB is not a downstream
Android base and cannot accept the stock overlay directly.  This tool copies
the compiled Nura DTB, adds the exact stock entry-selection/root identity and
a generated /__symbols__ map that redirects every external stock fixup to one
disabled sink node.  The stock overlay then applies entirely below that sink.

Nothing in the live device tree is changed except the reviewed root identity
fields; the deep validator is vendored from the proven neighbouring bring-up
and is driven with this board's exact hashes.

Host-only.  This tool never touches a device, partition, bootloader or rootfs.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_BIN = ROOT / "local/bin"
LOCAL_LIB = ROOT / "local/lib"
FDTGET = LOCAL_BIN / "fdtget"
FDTPUT = LOCAL_BIN / "fdtput"
FDTOVERLAY = LOCAL_BIN / "fdtoverlay"
DTC = LOCAL_BIN / "dtc"
UFDT = ROOT / "local/tools/ufdt/ufdt_overlay_test_app"
CHECKER = ROOT / "scripts/vendor/check-boot-dtb-quarantine.py"

BASE_DTB_DEFAULT = ROOT / "build/custom_guacamole.dtb"
BASE_DTS_SOURCE = ROOT / "base_guacamole_working.dts"
BASE_DTS_BUILD = ROOT / "build/patched_guacamole.dts"
DTBO_DEFAULT = ROOT / "ref/lineageos/lineage-24.0-20261102/dtbo.img"
STOCK_BOOT_DEFAULT = ROOT / "ref/lineageos/lineage-24.0-20261102/boot.img"
OUT_DEFAULT = ROOT / "build/temporary-dtbo"

QUARANTINE = "/__abl_dtbo_quarantine__"
SINK = QUARANTINE + "/sink"
PROJECT = 0x4985
HARDWARE = 21
PCB_RANGE = (0x0, 0x37)
SELECTION = {
    "model": ["Qualcomm Technologies, Inc. SM8150 MTP 18821"],
    "compatible": ["qcom,sm8150-mtp", "qcom,sm8150", "qcom,mtp"],
    "qcom,msm-id": [0x153, 0x20000],
    "qcom,board-id": [0x8, 0x0],
    "oplus,dtsi_no": [PROJECT],
    "oplus,pcb_range": list(PCB_RANGE),
}
IDENTITY_PROPERTIES = {
    "model",
    "compatible",
    "qcom,msm-id",
    "qcom,board-id",
    "oplus,dtsi_no",
    "oplus,pcb_range",
}
ENTRY_SHA256 = "5755b70bba844ae433a9c3fac84d6bd301540d27c8f7e1f020db415a21cde238"
EXPECTED_FIXUPS = 245
EXPECTED_FRAGMENTS = 128

# Host tools copied into this workspace.  The hashes are provenance pins, not
# a claim that these are the newest versions.
TOOL_HASHES = {
    "local/bin/dtc": "49d483d0d0136e954bfb3b0ccb3120fcd30bef4ec9b8bc6fa3b7500c30451942",
    "local/bin/fdtget": "a7afb29f91d91c11bd4648d75fb72c799a4737768216d2c3c8b4de34c38da281",
    "local/bin/fdtput": "786e022bc9248ec898919558bdda19ca472d8a34825821270ecf5baf4f7b0e4b",
    "local/bin/fdtoverlay": "ab883c92b694264de04d66c8dd1f66990ded158e3dec0d2a403af5ebc51156cb",
    "local/lib/libfdt.so.1": "aff62061c4c2ea9d248304b87f12ee323d079b89e5a632e6f5ed1614adb3a5e8",
    "local/tools/ufdt/ufdt_overlay_test_app": "c7d9db055d5ecfb7771200ea176dd459e2164114f58f107e3c72754cced999fa",
    "scripts/vendor/check-boot-dtb-quarantine.py": "45ade635be2f8a93a1e7e3d5b5d6c49741a4af467353e1238fee828a3aee5357",
}
# Both supplied Lineage 24.0 references contain the same DTBO payload and the
# same entry 0; only their AVB footer differs.  Either verified image is
# accepted, but a caller cannot substitute an arbitrary same-looking file.
DTBO_HASHES = {
    "ref/lineageos/lineage-24.0-20261102/dtbo.img": "460f3ed5f1d7c1b6b6f5d0fdce6526e7d07ac12ad965b6f77f2ead80ffa414c6",
    "ref/lineageos/lineage-24.0-20261011/dtbo.img": "6c0fd54b06920f41e586ce2010503eb2cde40ba54a1993af53cb59ae221bf5a4",
}
STOCK_BOOT_HASHES = {
    "ref/lineageos/lineage-24.0-20261102/boot.img": "28a65e8453bc6236d604d0a6aa54e8a989ae71668f945b801845e7717b3c95ce",
    "ref/lineageos/lineage-24.0-20261011/boot.img": "c8265cd141da5a2603cb96d49a3bc8e434b970bcefbe5aab1accacc8391de2b4",
}


class PrepareError(RuntimeError):
    """A check failed; no unsafe fallback is attempted."""


def configure_tools() -> None:
    os.environ["PATH"] = str(LOCAL_BIN) + os.pathsep + os.environ.get("PATH", "")
    old_ld = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = str(LOCAL_LIB) + (os.pathsep + old_ld if old_ld else "")
    os.environ.setdefault("LC_ALL", "C")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def file_record(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise PrepareError(f"missing required file: {display_path(path)}")
    return {
        "path": display_path(path),
        "size": path.stat().st_size,
        "sha256": sha256(path),
    }


def run(args: list[object], check: bool = True) -> subprocess.CompletedProcess[bytes]:
    command = [str(item) for item in args]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise PrepareError(f"command failed ({result.returncode}): {' '.join(command)}: {detail}")
    return result


def verify_tools() -> dict[str, str]:
    configured = {}
    for relative, expected in TOOL_HASHES.items():
        path = ROOT / relative
        if not path.is_file():
            raise PrepareError(f"missing air-gapped host tool: {relative}")
        got = sha256(path)
        if got != expected:
            raise PrepareError(f"host tool hash mismatch: {relative}: {got} != {expected}")
        configured[relative] = got
    if not os.access(UFDT, os.X_OK):
        raise PrepareError(f"overlay test app is not executable: {display_path(UFDT)}")
    return configured


def fdt_raw(dtb: Path, node: str, prop: str) -> bytes | None:
    result = run([FDTGET, "-t", "r", dtb, node, prop], check=False)
    if result.returncode:
        return None
    data = result.stdout
    if data.endswith(b"\n"):
        data = data[:-1]
    return data


def fdt_strings(dtb: Path, node: str, prop: str) -> list[str] | None:
    raw = fdt_raw(dtb, node, prop)
    if raw is None or not raw.endswith(b"\0"):
        return None
    return [item.decode() for item in raw.rstrip(b"\0").split(b"\0")]


def fdt_u32s(dtb: Path, node: str, prop: str) -> list[int] | None:
    raw = fdt_raw(dtb, node, prop)
    if raw is None or len(raw) % 4:
        return None
    return [int.from_bytes(raw[index : index + 4], "big") for index in range(0, len(raw), 4)]


def fdt_properties(dtb: Path, node: str) -> list[str]:
    result = run([FDTGET, "-p", dtb, node], check=False)
    return result.stdout.decode().split() if result.returncode == 0 else []


def fdt_children(dtb: Path, node: str) -> list[str]:
    result = run([FDTGET, "-l", dtb, node], check=False)
    return result.stdout.decode().split() if result.returncode == 0 else []


def root_identity(dtb: Path) -> dict[str, list[object] | None]:
    return {
        "model": fdt_strings(dtb, "/", "model"),
        "compatible": fdt_strings(dtb, "/", "compatible"),
        "qcom,msm-id": fdt_u32s(dtb, "/", "qcom,msm-id"),
        "qcom,board-id": fdt_u32s(dtb, "/", "qcom,board-id"),
        "oplus,dtsi_no": fdt_u32s(dtb, "/", "oplus,dtsi_no"),
        "oplus,pcb_range": fdt_u32s(dtb, "/", "oplus,pcb_range"),
    }


def parse_dtbo_table(path: Path) -> tuple[bytes, list[dict[str, int]]]:
    data = path.read_bytes()
    if len(data) < 32:
        raise PrepareError(f"DTBO image too small: {display_path(path)}")
    magic, total, header_size, entry_size, count, entries_offset, page_size, version = (
        struct.unpack_from(">8I", data, 0)
    )
    if magic != 0xD7B7AB1E:
        raise PrepareError(f"not an Android DTBO image: {display_path(path)}")
    if version != 0:
        raise PrepareError(f"unsupported DTBO version {version}: {display_path(path)}")
    if header_size < 32 or entry_size < 32 or not (1 <= count <= 64):
        raise PrepareError(f"invalid DTBO table geometry: {display_path(path)}")
    if page_size < 512 or page_size & (page_size - 1):
        raise PrepareError(f"invalid DTBO page size: {display_path(path)}")
    table_end = entries_offset + count * entry_size
    if entries_offset < header_size or table_end > len(data) or total > len(data):
        raise PrepareError(f"DTBO table is out of bounds: {display_path(path)}")
    entries = []
    spans = []
    for index in range(count):
        size, offset, entry_id, revision = struct.unpack_from(">4I", data, entries_offset + index * entry_size)
        if size == 0:
            continue
        if offset < table_end or offset + size > total:
            raise PrepareError(f"DTBO entry {index} is out of bounds: {display_path(path)}")
        entries.append({"index": index, "size": size, "offset": offset, "id": entry_id, "revision": revision})
        spans.append((offset, offset + size, index))
    if not entries:
        raise PrepareError(f"DTBO image contains no entries: {display_path(path)}")
    spans.sort()
    for previous, current in zip(spans, spans[1:]):
        if current[0] < previous[1]:
            raise PrepareError(f"DTBO entries overlap: {previous[2]} and {current[2]}")
    return data, entries


def extract_entry(data: bytes, entry: dict[str, int], path: Path) -> Path:
    path.write_bytes(data[entry["offset"] : entry["offset"] + entry["size"]])
    return path


def select_stock_entry(dtbo: Path, work: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    data, entries = parse_dtbo_table(dtbo)
    inspected = []
    matches = []
    for entry in entries:
        path = extract_entry(data, entry, work / f"entry-{entry['index']:02d}.dtb")
        identity = root_identity(path)
        item = {"entry": entry, "path": path, "identity": identity, "sha256": sha256(path)}
        inspected.append(item)
        project = identity["oplus,dtsi_no"]
        pcb = identity["oplus,pcb_range"]
        board = identity["qcom,board-id"]
        compatible = identity["compatible"]
        if project != [PROJECT] or board is None or board[:2] != SELECTION["qcom,board-id"]:
            continue
        if pcb is None or len(pcb) != 2 or not (pcb[0] <= HARDWARE <= pcb[1]):
            continue
        if compatible is None or "qcom,sm8150-mtp" not in compatible:
            continue
        matches.append(item)
    if len(matches) != 1:
        raise PrepareError(f"expected exactly one compatible stock DTBO entry, found {len(matches)}")
    selected = matches[0]
    if selected["entry"]["index"] != 0:
        raise PrepareError(f"selected stock entry is {selected['entry']['index']}, expected entry 0")
    if selected["sha256"] != ENTRY_SHA256:
        raise PrepareError(f"stock entry 0 hash changed: {selected['sha256']}")
    identity = selected["identity"]
    for key in ("model", "compatible", "qcom,board-id", "oplus,dtsi_no", "oplus,pcb_range"):
        if identity[key] != SELECTION[key]:
            raise PrepareError(f"stock entry 0 {key} is not the reviewed identity: {identity[key]}")
    fixups = fdt_properties(selected["path"], "/__fixups__")
    if len(fixups) != EXPECTED_FIXUPS:
        raise PrepareError(f"stock entry 0 has {len(fixups)} external fixups, expected {EXPECTED_FIXUPS}")
    fragments = [name for name in fdt_children(selected["path"], "/") if name.startswith("fragment@")]
    if len(fragments) != EXPECTED_FRAGMENTS:
        raise PrepareError(f"stock entry 0 has {len(fragments)} fragments, expected {EXPECTED_FRAGMENTS}")
    selected["fixups"] = fixups
    selected["fragments"] = len(fragments)
    return selected, inspected


def align(value: int, page: int) -> int:
    return (value + page - 1) // page * page


def parse_stock_boot(path: Path, work: Path) -> dict[str, object]:
    data = path.read_bytes()
    if len(data) < 4096 or data[:8] != b"ANDROID!":
        raise PrepareError(f"not an Android boot image: {display_path(path)}")
    header = data[:4096]
    (kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size, second_addr,
     tags_addr, page_size, header_version, os_version) = struct.unpack_from("<10I", header, 8)
    if header_version != 2:
        raise PrepareError(f"stock boot is header v{header_version}, expected v2")
    if page_size != 4096:
        raise PrepareError(f"stock boot page size is {page_size}, expected 4096")
    recovery_dtbo_size, recovery_dtbo_offset = struct.unpack_from("<IQ", header, 1632)
    header_size, dtb_size = struct.unpack_from("<II", header, 1644)
    dtb_addr = struct.unpack_from("<Q", header, 1652)[0]
    if header_size < 1660 or dtb_size == 0:
        raise PrepareError(f"stock boot has no v2 DTB section: {display_path(path)}")
    if (kernel_addr, ramdisk_addr, tags_addr, dtb_addr) != (0x8000, 0x01000000, 0x100, 0x01F00000):
        raise PrepareError(f"stock boot use unexpected load addresses: {display_path(path)}")
    kernel_offset = page_size
    ramdisk_offset = kernel_offset + align(kernel_size, page_size)
    second_offset = ramdisk_offset + align(ramdisk_size, page_size)
    recovery_offset = second_offset + align(second_size, page_size)
    dtb_offset = recovery_offset + (align(recovery_dtbo_size, page_size) if recovery_dtbo_size else 0)
    pack = data[dtb_offset : dtb_offset + dtb_size]
    if len(pack) != dtb_size:
        raise PrepareError(f"stock boot DTB section is truncated: {display_path(path)}")
    bases = []
    position = 0
    while position + 8 <= len(pack):
        magic, size = struct.unpack_from(">II", pack, position)
        if magic != 0xD00DFEED:
            break
        if size < 8 or position + size > len(pack):
            raise PrepareError(f"stock boot DTB at {position:#x} is out of bounds: {display_path(path)}")
        blob = pack[position : position + size]
        base_path = work / f"base-{len(bases):02d}.dtb"
        base_path.write_bytes(blob)
        identity = root_identity(base_path)
        bases.append({"index": len(bases), "path": base_path, "size": size,
                      "sha256": sha256(base_path), "identity": identity})
        position += align(size, page_size)
    matches = [item for item in bases
               if item["identity"]["qcom,msm-id"] == SELECTION["qcom,msm-id"]
               and item["identity"]["qcom,board-id"] == SELECTION["qcom,board-id"]]
    if len(matches) != 1:
        raise PrepareError(f"stock boot DTB pack has {len(matches)} matching bases, expected one")
    selected = matches[0]
    if selected["index"] != 0:
        raise PrepareError(f"stock base index is {selected['index']}, expected 0")
    return {
        "path": display_path(path),
        "size": len(data),
        "sha256": sha256(path),
        "header_version": header_version,
        "page_size": page_size,
        "kernel_size": kernel_size,
        "ramdisk_size": ramdisk_size,
        "dtb_size": dtb_size,
        "dtb_offset": dtb_offset,
        "dtb_pack_sha256": hashlib.sha256(pack).hexdigest(),
        "selected_base_index": selected["index"],
        "selected_base_sha256": selected["sha256"],
        "selected_base_size": selected["size"],
        "selected_base_identity": selected["identity"],
        "base_count": len(bases),
    }


def build_wrapper(base: Path, entry: Path, wrapper: Path) -> None:
    shutil.copy2(base, wrapper)
    fixups = fdt_properties(entry, "/__fixups__")
    if len(fixups) != EXPECTED_FIXUPS:
        raise PrepareError(f"cannot build wrapper: entry has {len(fixups)} fixups")
    run([FDTPUT, "-c", wrapper, QUARANTINE, SINK, "/__symbols__"])
    run([FDTPUT, "-ts", wrapper, QUARANTINE, "status", "disabled"])
    run([FDTPUT, "-ti", wrapper, SINK, "phandle", "65536"])
    for symbol in fixups:
        run([FDTPUT, "-ts", wrapper, "/__symbols__", symbol, SINK])
    run([FDTPUT, "-ts", wrapper, "/", "model", *SELECTION["model"]])
    run([FDTPUT, "-ts", wrapper, "/", "compatible", *SELECTION["compatible"]])
    for prop in ("qcom,msm-id", "qcom,board-id", "oplus,dtsi_no", "oplus,pcb_range"):
        run([FDTPUT, "-tx", wrapper, "/", prop, *[hex(value) for value in SELECTION[prop]]])


def load_checker():
    spec = importlib.util.spec_from_file_location("_nura_dtbo_quarantine", CHECKER)
    if spec is None or spec.loader is None:
        raise PrepareError(f"cannot load vendored validator: {display_path(CHECKER)}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wrapper_root(q, dtb: Path) -> dict[str, list[object]]:
    return {
        "model": q.strings(dtb, "/", "model"),
        "compatible": q.strings(dtb, "/", "compatible"),
        "qcom,msm-id": q.u32s(dtb, "/", "qcom,msm-id"),
        "qcom,board-id": q.u32s(dtb, "/", "qcom,board-id"),
        "oplus,dtsi_no": q.u32s(dtb, "/", "oplus,dtsi_no"),
        "oplus,pcb_range": q.u32s(dtb, "/", "oplus,pcb_range"),
    }


def identity_delta_ok(diff: dict[str, object], allow_symbols_node: bool) -> bool:
    allowed_nodes = {"/__symbols__"} if allow_symbols_node else set()
    if set(diff["added_nodes"]) != allowed_nodes:
        return False
    if diff["removed_nodes"] or diff["removed_properties"]:
        return False
    for prop in diff["added_properties"]:
        if not prop.startswith("/:") or prop[2:] not in IDENTITY_PROPERTIES:
            return False
    for item in diff["changed_properties"]:
        prop = item["property"]
        if not prop.startswith("/:") or prop[2:] not in IDENTITY_PROPERTIES:
            return False
    return True


def wrapper_delta_ok(q, clean: Path, wrapper: Path) -> bool:
    before = q.snapshot(clean)
    after = q.snapshot(wrapper, skip_quarantine=True)
    diff = q.structural_diff(before, after)
    if not identity_delta_ok(diff, allow_symbols_node=True):
        return False
    try:
        return wrapper_root(q, wrapper) == SELECTION
    except (OSError, RuntimeError, ValueError):
        return False


def run_validator(base: Path, base_dts: Path, entry: Path, dtbo: Path,
                  wrapper: Path, merged: Path, report_path: Path) -> dict[str, object]:
    q = load_checker()
    q.EXPECTED.update({
        "dtbo": sha256(dtbo),
        "entry0": sha256(entry),
        "clean_dtb": sha256(base),
        "clean_dts": sha256(base_dts),
    })
    q.clean_wrapper_diff_ok = lambda diff: wrapper_delta_ok(q, base, wrapper)
    argv = [
        "check-boot-dtb-quarantine.py",
        "--dtbo", str(dtbo),
        "--entry", str(entry),
        "--clean-dtb", str(base),
        "--base", str(wrapper),
        "--merged", str(merged),
        "--clean-dts", str(base_dts),
        "--output", str(report_path),
    ]
    old_argv = sys.argv
    sys.argv = argv
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            code = q.main()
    except (OSError, RuntimeError, UnicodeError, ValueError) as error:
        raise PrepareError(f"reused DTBO quarantine validator failed to run: {error}") from error
    finally:
        sys.argv = old_argv
    if code != 0 or not report_path.is_file():
        raise PrepareError(f"reused DTBO quarantine validator FAILED: {display_path(report_path)}")
    report = json.loads(report_path.read_text())
    if report.get("status") != "PASS":
        raise PrepareError(f"reused DTBO quarantine validator status is {report.get('status')}")
    return report


def subtree_snapshot(q, dtb: Path, root: str) -> dict[str, dict[str, str]]:
    if not q.node_exists(dtb, root):
        return {}
    result = {}
    for path in q.walk(dtb, root):
        result[path] = {prop: q.raw_property(dtb, path, prop).hex()
                        for prop in q.properties(dtb, path)}
    return result


def reserved_regions(q, dtb: Path) -> list[tuple[int, int, str]]:
    parent = "/reserved-memory"
    if not q.node_exists(dtb, parent):
        return []
    properties = q.properties(dtb, parent)
    addr_cells = q.u32s(dtb, parent, "#address-cells")[0] if "#address-cells" in properties else 2
    size_cells = q.u32s(dtb, parent, "#size-cells")[0] if "#size-cells" in properties else 2
    stride = addr_cells + size_cells
    regions = []
    for path in q.walk(dtb, parent):
        if path == parent or "reg" not in q.properties(dtb, path):
            continue
        raw = q.raw_property(dtb, path, "reg")
        cells = [int.from_bytes(raw[index : index + 4], "big") for index in range(0, len(raw), 4)]
        if stride == 0 or len(cells) % stride:
            continue
        for index in range(0, len(cells), stride):
            address = 0
            for cell in cells[index : index + addr_cells]:
                address = (address << 32) | cell
            size = 0
            for cell in cells[index + addr_cells : index + stride]:
                size = (size << 32) | cell
            if size:
                regions.append((address, address + size, path))
    return regions


def reserved_conflicts(regions: list[tuple[int, int, str]]) -> list[list[str]]:
    conflicts = []
    for left in range(len(regions)):
        for right in range(left + 1, len(regions)):
            a_start, a_end, a_path = regions[left]
            b_start, b_end, b_path = regions[right]
            if a_start < b_end and b_start < a_end:
                conflicts.append([a_path, b_path, hex(max(a_start, b_start)), hex(min(a_end, b_end))])
    return sorted(conflicts)


def verify_reference_dtbo(path: Path, work: Path) -> dict[str, object]:
    record = file_record(path)
    expected = DTBO_HASHES.get(display_path(path))
    if expected is None or record["sha256"] != expected:
        raise PrepareError(f"untrusted DTBO reference: {display_path(path)}")
    selected, inspected = select_stock_entry(path, work)
    return {
        **record,
        "entry_index": selected["entry"]["index"],
        "entry_sha256": selected["sha256"],
        "entry_size": selected["entry"]["size"],
        "fixup_symbols": len(selected["fixups"]),
        "fragments": selected["fragments"],
        "entry_count": len(inspected),
    }


def verify_reference_boot(path: Path, work: Path) -> dict[str, object]:
    record = file_record(path)
    expected = STOCK_BOOT_HASHES.get(display_path(path))
    if expected is None or record["sha256"] != expected:
        raise PrepareError(f"untrusted stock boot reference: {display_path(path)}")
    parsed = parse_stock_boot(path, work)
    return {**record, **{key: value for key, value in parsed.items() if key != "path"}}


def prepare(args: argparse.Namespace) -> int:
    configure_tools()
    tools = verify_tools()
    base = Path(args.base_dtb).resolve()
    base_source_dts = Path(
        getattr(args, "base_source_dts", str(BASE_DTS_SOURCE))
    ).resolve()
    patched_dts = Path(getattr(args, "patched_dts", str(BASE_DTS_BUILD))).resolve()
    out_dir = Path(args.out_dir).resolve()
    dtbo = Path(args.dtbo).resolve()
    stock_boot = Path(args.stock_boot).resolve()
    if not base.is_file():
        raise PrepareError(f"missing base DTB: {display_path(base)}; run scripts/patch_dtb.py first")
    if not base_source_dts.is_file():
        raise PrepareError(f"missing base source DTS: {display_path(base_source_dts)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".work-") as temporary:
        work = Path(temporary)
        # Reject any untrusted or changed stock reference before parsing it.
        if sha256(dtbo) not in DTBO_HASHES.values():
            raise PrepareError(f"DTBO image hash is not an accepted reference: {sha256(dtbo)}")
        if sha256(stock_boot) not in STOCK_BOOT_HASHES.values():
            raise PrepareError(f"stock boot hash is not an accepted reference: {sha256(stock_boot)}")
        selected, inspected = select_stock_entry(dtbo, work)
        stock = parse_stock_boot(stock_boot, work)

        entry = out_dir / "dtbo-entry-00.dtb"
        shutil.copy2(selected["path"], entry)
        if sha256(entry) != ENTRY_SHA256:
            raise PrepareError(f"copied stock entry hash changed: {sha256(entry)}")

        base_dts = out_dir / "base.dts"
        run([DTC, "-I", "dtb", "-O", "dts", base, "-o", base_dts])

        wrapper = out_dir / "custom_guacamole-abl-quarantine.dtb"
        build_wrapper(base, entry, wrapper)
        merged_ufdt = out_dir / "post-stock-merged-libufdt.dtb"
        merged_libfdt = out_dir / "post-stock-merged-libfdt.dtb"
        run([UFDT, wrapper, entry, merged_ufdt])
        run([FDTOVERLAY, "-i", wrapper, "-o", merged_libfdt, entry])

        report_path = out_dir / "quarantine-report.json"
        report = run_validator(base, base_dts, entry, dtbo, wrapper, merged_ufdt, report_path)

        q = load_checker()
        wrapper_values = wrapper_root(q, wrapper)
        merged_ufdt_values = wrapper_root(q, merged_ufdt)
        merged_libfdt_values = wrapper_root(q, merged_libfdt)
        root_exact = wrapper_values == merged_ufdt_values == merged_libfdt_values == SELECTION
        if not root_exact:
            raise PrepareError(f"root selection/identity mismatch: {wrapper_values}")
        symbols = q.properties(wrapper, "/__symbols__")
        symbols_exact = (
            set(symbols) == set(selected["fixups"])
            and all(q.strings(wrapper, "/__symbols__", symbol) == [SINK] for symbol in symbols)
        )
        quarantine_disabled = (
            q.strings(wrapper, QUARANTINE, "status") == ["disabled"]
            and "compatible" not in q.properties(wrapper, QUARANTINE)
        )
        if not symbols_exact or not quarantine_disabled:
            raise PrepareError("generated symbol map or disabled quarantine is malformed")

        ufdt_tree = q.snapshot(merged_ufdt)
        libfdt_tree = q.snapshot(merged_libfdt)
        for tree in (ufdt_tree, libfdt_tree):
            tree.pop("/__symbols__", None)
        appliers_agree = q.diff_is_empty(q.structural_diff(ufdt_tree, libfdt_tree))
        if not appliers_agree:
            raise PrepareError("Qualcomm libufdt and libfdt post-overlay live trees disagree")
        lib_wrapper = q.snapshot(wrapper, skip_quarantine=True)
        lib_after = q.snapshot(merged_libfdt, skip_quarantine=True)
        lib_wrapper.pop("/__symbols__", None)
        lib_after.pop("/__symbols__", None)
        libfdt_live_noop = q.diff_is_empty(q.structural_diff(lib_wrapper, lib_after))
        if not libfdt_live_noop:
            raise PrepareError("libfdt stock-DTBO application changed the live tree")

        # Direct base -> post-overlay check: only the reviewed root identity
        # may differ; /__symbols__ metadata is deliberately ignored.
        base_live = q.snapshot(base)
        base_live.pop("/__symbols__", None)
        merged_live = q.snapshot(merged_ufdt, skip_quarantine=True)
        merged_live.pop("/__symbols__", None)
        base_to_merged = q.structural_diff(base_live, merged_live)
        base_to_merged_ok = identity_delta_ok(base_to_merged, allow_symbols_node=False)
        if not base_to_merged_ok:
            raise PrepareError("post-overlay live tree differs from the base outside the reviewed identity delta")

        base_reserved = subtree_snapshot(q, base, "/reserved-memory")
        wrapper_reserved = subtree_snapshot(q, wrapper, "/reserved-memory")
        ufdt_reserved = subtree_snapshot(q, merged_ufdt, "/reserved-memory")
        libfdt_reserved = subtree_snapshot(q, merged_libfdt, "/reserved-memory")
        reserved_unchanged = base_reserved == wrapper_reserved == ufdt_reserved == libfdt_reserved
        if not reserved_unchanged:
            raise PrepareError("reserved-memory changed in the wrapper or post-overlay tree")
        base_conflicts = reserved_conflicts(reserved_regions(q, base))
        wrapper_conflicts = reserved_conflicts(reserved_regions(q, wrapper))
        ufdt_conflicts = reserved_conflicts(reserved_regions(q, merged_ufdt))
        libfdt_conflicts = reserved_conflicts(reserved_regions(q, merged_libfdt))
        if not (base_conflicts == wrapper_conflicts == ufdt_conflicts == libfdt_conflicts):
            raise PrepareError("wrapper or overlay introduced a new reserved-memory conflict")

        counts = report["counts"]
        checks = report["checks"]
        phandle_ok = (
            checks.get("no_phandle_namespace_overlap") is True
            and checks.get("no_final_reference_cell_points_outside_quarantine") is True
            and checks.get("all_external_fixups_resolve_inside_quarantine") is True
            and counts.get("inside_quarantine_phandles", 0) >= 1
            and q.u32s(wrapper, SINK, "phandle") == [65536]
            and not report.get("failures")
        )
        if not phandle_ok:
            raise PrepareError("quarantine phandle/reference containment check failed")
        if not wrapper_delta_ok(q, base, wrapper):
            raise PrepareError("wrapper changed live nodes/properties outside the reviewed identity delta")

        references_dtbo = [verify_reference_dtbo(path, work) for relative, expected in DTBO_HASHES.items()
                           if (path := ROOT / relative).is_file()]
        references_boot = [verify_reference_boot(path, work) for relative, expected in STOCK_BOOT_HASHES.items()
                           if (path := ROOT / relative).is_file()]
        if len({item["entry_sha256"] for item in references_dtbo}) != 1:
            raise PrepareError("supplied DTBO references disagree on entry 0")
        if len({item["selected_base_sha256"] for item in references_boot}) != 1:
            raise PrepareError("supplied stock boot references disagree on the selected base DTB")
        selected_dtbo = next(item for item in references_dtbo if item["sha256"] == sha256(dtbo))
        selected_boot = next(item for item in references_boot if item["sha256"] == sha256(stock_boot))

        checks_summary = {
            "deep_validator": report["status"],
            "outside_quarantine_diff_empty": q.diff_is_empty(report["outside_quarantine_diff"]),
            "clean_to_wrapper_only_reviewed_identity": wrapper_delta_ok(q, base, wrapper),
            "base_to_post_overlay_only_reviewed_identity": base_to_merged_ok,
            "libufdt_libfdt_live_trees_equal": appliers_agree,
            "libfdt_application_live_tree_noop": libfdt_live_noop,
            "root_selection_exact": root_exact,
            "symbol_map_and_disabled_quarantine_exact": symbols_exact and quarantine_disabled,
            "reserved_memory_unchanged": reserved_unchanged,
            "reserved_memory_no_new_conflicts": True,
            "phandle_reference_containment": phandle_ok,
            "entry_selection_exact": selected_dtbo["entry_sha256"] == ENTRY_SHA256,
            "stock_base_selection_exact": selected_boot["selected_base_index"] == 0,
            "host_tool_hashes": True,
        }
        if checks_summary["deep_validator"] != "PASS" or not all(
                value is True for key, value in checks_summary.items() if key != "deep_validator"):
            raise PrepareError(f"one or more reviewed checks failed: {checks_summary}")

        manifest = {
            "schema": 1,
            "status": "PASS",
            "hardware": "UNTESTED",
            "purpose": "ABL stock-DTBO quarantine wrapper around the unmodified Nura hardware DTB",
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "selection": SELECTION,
            "phone_identity": {
                "model": "GM1911",
                "project": PROJECT,
                "hardware": HARDWARE,
                "pcb_range": list(PCB_RANGE),
                "slot_at_intake": "b",
                "android": 17,
                "avb": "orange",
            },
            "inputs": {
                "base_dtb": file_record(base),
                "base_dts": file_record(base_dts),
                "base_source_dts": file_record(base_source_dts),
                "patched_build_dts": file_record(patched_dts) if patched_dts.is_file() else None,
                "dtbo_image": {**file_record(dtbo), "entry_index": selected["entry"]["index"],
                               "entry_sha256": selected["sha256"]},
                "stock_boot": {**file_record(stock_boot), **{key: value for key, value in stock.items() if key != "path"}},
            },
            "dtbo_references": references_dtbo,
            "stock_boot_references": references_boot,
            "tools": tools,
            "artifacts": {
                "wrapper": file_record(wrapper),
                "entry": file_record(entry),
                "merged_ufdt": file_record(merged_ufdt),
                "merged_libfdt": file_record(merged_libfdt),
                "quarantine_report": file_record(report_path),
            },
            "checks": checks_summary,
            "reserved_memory": {
                "changed": False,
                "pre_existing_conflicts": base_conflicts,
                "post_overlay_conflicts": ufdt_conflicts,
            },
            "limitations": [
                "Hardware is UNTESTED; this host-only wrapper has not been booted.",
                "The copied Lineage DTBO is not proven identical to the phone's installed dtbo_b; a read-only preflight is still required and the partition must not be erased.",
                "No boot.img is created here; the initramfs/rootfs are separate pending inputs.",
                "ABL ARM64 entry layout, AVB envelope and full image placement are outside this DTBO-only check.",
            ],
        }
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        print(f"TEMPORARY_DTB=PASS wrapper={display_path(wrapper)} "
              f"sha256={sha256(wrapper)} entry_sha256={ENTRY_SHA256} hardware=UNTESTED")
    return 0


def verify_manifest(args: argparse.Namespace) -> int:
    configure_tools()
    tools = verify_tools()
    manifest_path = Path(args.manifest).resolve()
    if not manifest_path.is_file():
        raise PrepareError(f"missing manifest: {display_path(manifest_path)}; run prepare_temporary_dtb.py first")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != 1 or manifest.get("status") != "PASS":
        raise PrepareError("manifest is not a PASS schema-1 result")
    out_dir = manifest_path.parent
    for section in ("inputs", "artifacts"):
        for name, item in manifest.get(section, {}).items():
            if not item:
                continue
            path = resolve_path(str(item["path"]))
            if not path.is_file():
                raise PrepareError(f"manifest {section}.{name} is missing: {item['path']}")
            if path.stat().st_size != item["size"] or sha256(path) != item["sha256"]:
                raise PrepareError(f"manifest {section}.{name} hash/size mismatch: {item['path']}")

    base = resolve_path(manifest["inputs"]["base_dtb"]["path"])
    base_dts = resolve_path(manifest["inputs"]["base_dts"]["path"])
    dtbo = resolve_path(manifest["inputs"]["dtbo_image"]["path"])
    stock_boot = resolve_path(manifest["inputs"]["stock_boot"]["path"])
    wrapper = resolve_path(manifest["artifacts"]["wrapper"]["path"])
    entry = resolve_path(manifest["artifacts"]["entry"]["path"])
    merged_ufdt = resolve_path(manifest["artifacts"]["merged_ufdt"]["path"])
    merged_libfdt = resolve_path(manifest["artifacts"]["merged_libfdt"]["path"])

    # A regenerated base must be re-wrapped and the manifest refreshed.
    if sha256(base) != manifest["inputs"]["base_dtb"]["sha256"]:
        raise PrepareError("base DTB changed since prepare; rerun prepare_temporary_dtb.py")
    if sha256(dtbo) not in DTBO_HASHES.values() or sha256(dtbo) != manifest["inputs"]["dtbo_image"]["sha256"]:
        raise PrepareError("DTBO reference changed or is untrusted")

    q = load_checker()
    if not wrapper_delta_ok(q, base, wrapper):
        raise PrepareError("wrapper no longer satisfies the reviewed identity delta")
    wrapper_values = wrapper_root(q, wrapper)
    if wrapper_values != SELECTION:
        raise PrepareError(f"wrapper root selection/identity mismatch: {wrapper_values}")

    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".verify-") as temporary:
        work = Path(temporary)
        selected, inspected = select_stock_entry(dtbo, work)
        if selected["sha256"] != manifest["inputs"]["dtbo_image"]["entry_sha256"]:
            raise PrepareError("selected stock DTBO entry changed")
        if sha256(entry) != selected["sha256"]:
            raise PrepareError("stored DTBO entry does not match the current reference")
        stock = parse_stock_boot(stock_boot, work)
        if stock["selected_base_index"] != 0 or stock["sha256"] != manifest["inputs"]["stock_boot"]["sha256"]:
            raise PrepareError("stock boot base selection changed")

        test_ufdt = work / "verify-merged-ufdt.dtb"
        test_libfdt = work / "verify-merged-libfdt.dtb"
        run([UFDT, wrapper, entry, test_ufdt])
        run([FDTOVERLAY, "-i", wrapper, "-o", test_libfdt, entry])
        if sha256(test_ufdt) != manifest["artifacts"]["merged_ufdt"]["sha256"]:
            raise PrepareError("Qualcomm libufdt reproduction hash changed")
        if sha256(test_libfdt) != manifest["artifacts"]["merged_libfdt"]["sha256"]:
            raise PrepareError("libfdt reproduction hash changed")

        report_path = work / "verify-report.json"
        report = run_validator(base, base_dts, entry, dtbo, wrapper, test_ufdt, report_path)
        if not q.diff_is_empty(report["outside_quarantine_diff"]):
            raise PrepareError("reproduced overlay changes live nodes outside quarantine")
        if wrapper_root(q, test_ufdt) != SELECTION:
            raise PrepareError("reproduced post-overlay root identity changed")

        base_reserved = subtree_snapshot(q, base, "/reserved-memory")
        if base_reserved != subtree_snapshot(q, wrapper, "/reserved-memory"):
            raise PrepareError("wrapper reserved-memory tree changed")
        base_conflicts = reserved_conflicts(reserved_regions(q, base))
        if base_conflicts != reserved_conflicts(reserved_regions(q, test_ufdt)):
            raise PrepareError("reproduced overlay introduced a reserved-memory conflict")

    if tools != manifest.get("tools"):
        raise PrepareError("host tool set/hash changed since prepare")
    print(f"TEMPORARY_DTB_VERIFY=PASS wrapper={display_path(wrapper)} "
          f"sha256={sha256(wrapper)} entry_sha256={ENTRY_SHA256} hardware=UNTESTED")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dtb", default=str(BASE_DTB_DEFAULT),
                        help="compiled unmodified Nura DTB (default: build/custom_guacamole.dtb)")
    parser.add_argument("--base-source-dts", default=str(BASE_DTS_SOURCE),
                        help="board DTS the base DTB was built from")
    parser.add_argument("--patched-dts", default=str(BASE_DTS_BUILD),
                        help="optional patched/flattened DTS recorded in the manifest")
    parser.add_argument("--dtbo", default=str(DTBO_DEFAULT),
                        help="stock Lineage guacamole dtbo.img reference")
    parser.add_argument("--stock-boot", default=str(STOCK_BOOT_DEFAULT),
                        help="matching stock Lineage boot.img reference")
    parser.add_argument("--out-dir", default=str(OUT_DEFAULT),
                        help="artifact directory (default: build/temporary-dtbo)")
    parser.add_argument("--verify", action="store_true",
                        help="verify an existing manifest/artifacts without rebuilding")
    parser.add_argument("--manifest", default=None,
                        help="manifest used by --verify (default: <out-dir>/manifest.json)")
    args = parser.parse_args(argv)
    if args.verify and args.manifest is None:
        args.manifest = str(Path(args.out_dir) / "manifest.json")
    try:
        return verify_manifest(args) if args.verify else prepare(args)
    except PrepareError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
