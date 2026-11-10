#!/usr/bin/env python3
"""Fail if the exact guacamole stock overlay changes anything outside quarantine."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


QUARANTINE = "/__abl_dtbo_quarantine__"
EXPECTED = {
    "dtbo": "cda70dd9e39279ff57fa9c911d7b7ee192ff810182fd3dea84edcb22da169970",
    "entry0": "5755b70bba844ae433a9c3fac84d6bd301540d27c8f7e1f020db415a21cde238",
    "clean_dtb": "2d6a792da847791a20ad1166bf2c8253981a2bfeb085bc7dbaaba113f67abef5",
    "clean_dts": "aa64b17e06d5f2f0663606894d800259f9c5e1f33f49a8e92bf8dff88bb44427",
}
ROOT_ADDED = {
    "qcom,msm-id",
    "qcom,board-id",
    "oplus,dtsi_no",
    "oplus,pcb_range",
}
ROOT_CHANGED = {"model", "compatible"}


def command(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        error = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(args)}: {error}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def node_exists(dtb: Path, path: str) -> bool:
    return command("fdtget", "-p", str(dtb), path, check=False).returncode == 0


def words(*args: str) -> list[str]:
    return command(*args).stdout.decode().split()


def properties(dtb: Path, path: str) -> list[str]:
    return words("fdtget", "-p", str(dtb), path) if node_exists(dtb, path) else []


def children(dtb: Path, path: str) -> list[str]:
    return words("fdtget", "-l", str(dtb), path) if node_exists(dtb, path) else []


def raw_property(dtb: Path, path: str, prop: str) -> bytes:
    data = command("fdtget", "-t", "r", str(dtb), path, prop).stdout
    return data[:-1] if data.endswith(b"\n") else data


def strings(dtb: Path, path: str, prop: str) -> list[str]:
    raw = raw_property(dtb, path, prop)
    if not raw.endswith(b"\0"):
        raise RuntimeError(f"not a string-list property: {path}:{prop}")
    return [item.decode() for item in raw.rstrip(b"\0").split(b"\0")]


def u32s(dtb: Path, path: str, prop: str) -> list[int]:
    raw = raw_property(dtb, path, prop)
    if len(raw) % 4:
        raise RuntimeError(f"not a u32 property: {path}:{prop}")
    return [int.from_bytes(raw[offset : offset + 4], "big") for offset in range(0, len(raw), 4)]


def walk(dtb: Path, path: str = "/"):
    yield path
    for child in children(dtb, path):
        child_path = path.rstrip("/") + "/" + child
        yield from walk(dtb, child_path)


def snapshot(dtb: Path, skip_quarantine: bool = False) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for path in walk(dtb):
        if skip_quarantine and (path == QUARANTINE or path.startswith(QUARANTINE + "/")):
            continue
        result[path] = {prop: raw_property(dtb, path, prop).hex() for prop in properties(dtb, path)}
    return result


def structural_diff(before: dict[str, dict[str, str]], after: dict[str, dict[str, str]]) -> dict[str, object]:
    before_nodes = set(before)
    after_nodes = set(after)
    added_nodes = sorted(after_nodes - before_nodes)
    removed_nodes = sorted(before_nodes - after_nodes)
    added_properties: list[str] = []
    removed_properties: list[str] = []
    changed_properties: list[dict[str, str]] = []
    for path in sorted(before_nodes & after_nodes):
        old = before[path]
        new = after[path]
        added_properties.extend(f"{path}:{prop}" for prop in sorted(set(new) - set(old)))
        removed_properties.extend(f"{path}:{prop}" for prop in sorted(set(old) - set(new)))
        for prop in sorted(set(old) & set(new)):
            if old[prop] != new[prop]:
                changed_properties.append(
                    {
                        "property": f"{path}:{prop}",
                        "before_sha256": hashlib.sha256(bytes.fromhex(old[prop])).hexdigest(),
                        "after_sha256": hashlib.sha256(bytes.fromhex(new[prop])).hexdigest(),
                    }
                )
    return {
        "added_nodes": added_nodes,
        "removed_nodes": removed_nodes,
        "added_properties": added_properties,
        "removed_properties": removed_properties,
        "changed_properties": changed_properties,
    }


def diff_is_empty(diff: dict[str, object]) -> bool:
    return not any(diff.values())


def phandles(dtb: Path) -> dict[int, str]:
    result: dict[int, str] = {}
    for path in walk(dtb):
        values = []
        for prop in ("phandle", "linux,phandle"):
            if prop in properties(dtb, path):
                values.extend(u32s(dtb, path, prop))
        for value in set(values):
            if value in result and result[value] != path:
                raise RuntimeError(f"duplicate phandle {value:#x}: {result[value]} and {path}")
            result[value] = path
    return result


def fixup_parts(reference: str) -> tuple[str, str, int]:
    path, prop, offset = reference.rsplit(":", 2)
    return path, prop, int(offset, 0)


def merged_path(overlay_path: str, fragment_destinations: dict[str, str]) -> str | None:
    parts = overlay_path.removeprefix("/").split("/", 2)
    if not parts or parts[0] not in fragment_destinations:
        return None
    if len(parts) == 1:
        return fragment_destinations[parts[0]]
    if parts[1] != "__overlay__":
        return None
    suffix = "/" + parts[2] if len(parts) == 3 else ""
    return fragment_destinations[parts[0]] + suffix


def clean_wrapper_diff_ok(diff: dict[str, object]) -> bool:
    expected_added = {f"/:{name}" for name in ROOT_ADDED}
    expected_changed = {f"/:{name}" for name in ROOT_CHANGED}
    return (
        set(diff["added_nodes"]) == {"/__symbols__"}
        and not diff["removed_nodes"]
        and set(diff["added_properties"]) == expected_added
        and not diff["removed_properties"]
        and {item["property"] for item in diff["changed_properties"]} == expected_changed
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dtbo", required=True, type=Path)
    parser.add_argument("--entry", required=True, type=Path)
    parser.add_argument("--clean-dtb", required=True, type=Path)
    parser.add_argument("--base", required=True, type=Path, help="pre-overlay quarantine DTB")
    parser.add_argument("--merged", required=True, type=Path, help="Qualcomm libufdt output")
    parser.add_argument("--clean-dts", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    failures: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    hashes = {
        "dtbo": sha256(args.dtbo),
        "entry0": sha256(args.entry),
        "clean_dtb": sha256(args.clean_dtb),
        "base": sha256(args.base),
        "merged": sha256(args.merged),
        "clean_dts": sha256(args.clean_dts),
    }
    for name in ("dtbo", "entry0", "clean_dtb", "clean_dts"):
        require(hashes[name] == EXPECTED[name], f"unexpected {name} SHA-256")

    fixup_symbols = properties(args.entry, "/__fixups__")
    base_symbols = set(properties(args.base, "/__symbols__"))
    require(len(fixup_symbols) == 245, "entry 0 does not have exactly 245 external fixup symbols")
    require(set(fixup_symbols) == base_symbols, "base symbol map is not exactly the external-fixup set")

    fixup_refs: dict[tuple[str, str, int], str] = {}
    target_paths: dict[str, str] = {}
    external_reference_count = 0
    for symbol in fixup_symbols:
        mapped = strings(args.base, "/__symbols__", symbol)
        require(len(mapped) == 1, f"symbol {symbol} does not map to exactly one path")
        if len(mapped) != 1:
            continue
        target_paths[symbol] = mapped[0]
        require(
            mapped[0] == QUARANTINE or mapped[0].startswith(QUARANTINE + "/"),
            f"external symbol {symbol} escapes quarantine: {mapped[0]}",
        )
        require(node_exists(args.base, mapped[0]), f"external symbol {symbol} targets a missing node")
        require("phandle" in properties(args.base, mapped[0]), f"external symbol {symbol} target lacks phandle")
        for reference in strings(args.entry, "/__fixups__", symbol):
            path, prop, offset = fixup_parts(reference)
            require(node_exists(args.entry, path), f"fixup {symbol} references missing node {path}")
            if not node_exists(args.entry, path) or prop not in properties(args.entry, path):
                failures.append(f"fixup {symbol} references missing property {path}:{prop}")
                continue
            raw = raw_property(args.entry, path, prop)
            require(offset + 4 <= len(raw), f"fixup {symbol} offset is out of bounds: {reference}")
            key = (path, prop, offset)
            require(key not in fixup_refs, f"duplicate external fixup location: {reference}")
            fixup_refs[key] = symbol
            external_reference_count += 1

    fragments = sorted(name for name in children(args.entry, "/") if name.startswith("fragment@"))
    fragment_destinations: dict[str, str] = {}
    require(len(fragments) == 128, "entry 0 does not have exactly 128 fragments")
    for fragment in fragments:
        path = "/" + fragment
        props = set(properties(args.entry, path))
        require("target" in props, f"{fragment} has no target property")
        require("target-path" not in props, f"{fragment} uses target-path")
        target_refs = [symbol for (node, prop, offset), symbol in fixup_refs.items() if node == path and prop == "target" and offset == 0]
        require(len(target_refs) == 1, f"{fragment} does not have exactly one external target fixup")
        if len(target_refs) == 1:
            destination = target_paths[target_refs[0]]
            fragment_destinations[fragment] = destination
            require(destination.startswith(QUARANTINE + "/"), f"{fragment} targets outside quarantine")

    local_reference_count = 0
    local_fixups: list[tuple[str, str, int]] = []
    if node_exists(args.entry, "/__local_fixups__"):
        for path in walk(args.entry, "/__local_fixups__"):
            for prop in properties(args.entry, path):
                actual_path = path.removeprefix("/__local_fixups__") or "/"
                require(actual_path.startswith("/fragment@"), f"local fixup is outside a fragment: {actual_path}")
                for offset in u32s(args.entry, path, prop):
                    local_fixups.append((actual_path, prop, offset))
                    local_reference_count += 1

    before = snapshot(args.base, skip_quarantine=True)
    after = snapshot(args.merged, skip_quarantine=True)
    outside_diff = structural_diff(before, after)
    require(diff_is_empty(outside_diff), "pre/post overlay trees differ outside quarantine")

    clean = snapshot(args.clean_dtb)
    wrapper = snapshot(args.base, skip_quarantine=True)
    wrapper_diff = structural_diff(clean, wrapper)
    require(clean_wrapper_diff_ok(wrapper_diff), "wrapper changes more than reviewed root identity/selection/symbol metadata")

    require(strings(args.base, QUARANTINE, "status") == ["disabled"], "pre-overlay quarantine is not disabled")
    require(strings(args.merged, QUARANTINE, "status") == ["disabled"], "post-overlay quarantine is not disabled")
    require("compatible" not in properties(args.base, QUARANTINE), "quarantine parent has a compatible property")
    require("compatible" not in properties(args.merged, QUARANTINE), "post-overlay quarantine parent can bind to a driver")

    merged_phandles = phandles(args.merged)
    inside_phandles = {value for value, path in merged_phandles.items() if path == QUARANTINE or path.startswith(QUARANTINE + "/")}
    outside_phandles = set(merged_phandles) - inside_phandles
    require(not (inside_phandles & outside_phandles), "quarantine and live tree share a phandle")
    for symbol, path in target_paths.items():
        values = u32s(args.base, path, "phandle")
        require(len(values) == 1 and values[0] in inside_phandles, f"external symbol {symbol} does not resolve to a quarantine phandle")

    final_reference_cells = {"inside_quarantine": 0, "overwritten_or_not_a_phandle": 0, "outside_quarantine": 0}
    reference_locations = list(fixup_refs) + local_fixups
    for overlay_path, prop, offset in reference_locations:
        if prop == "target" and overlay_path.count("/") == 1:
            continue
        final_path = merged_path(overlay_path, fragment_destinations)
        require(final_path is not None, f"cannot map overlay reference into final tree: {overlay_path}:{prop}:{offset}")
        if final_path is None or not node_exists(args.merged, final_path) or prop not in properties(args.merged, final_path):
            final_reference_cells["overwritten_or_not_a_phandle"] += 1
            continue
        raw = raw_property(args.merged, final_path, prop)
        if offset + 4 > len(raw):
            final_reference_cells["overwritten_or_not_a_phandle"] += 1
            continue
        value = int.from_bytes(raw[offset : offset + 4], "big")
        if value in outside_phandles:
            final_reference_cells["outside_quarantine"] += 1
            failures.append(f"final quarantine reference escapes to {merged_phandles[value]}: {final_path}:{prop}:{offset}")
        elif value in inside_phandles:
            final_reference_cells["inside_quarantine"] += 1
        else:
            final_reference_cells["overwritten_or_not_a_phandle"] += 1

    checks = {
        "exact_20260824_hashes": all(hashes[name] == EXPECTED[name] for name in ("dtbo", "entry0", "clean_dtb", "clean_dts")),
        "all_external_fixups_resolve_inside_quarantine": len(fixup_symbols) == 245 and set(fixup_symbols) == base_symbols,
        "all_fragments_target_quarantine": len(fragment_destinations) == 128 and all(path.startswith(QUARANTINE + "/") for path in fragment_destinations.values()),
        "no_target_path_fragments": all("target-path" not in properties(args.entry, "/" + fragment) for fragment in fragments),
        "no_phandle_namespace_overlap": not (inside_phandles & outside_phandles),
        "no_final_reference_cell_points_outside_quarantine": final_reference_cells["outside_quarantine"] == 0,
        "outside_quarantine_structural_diff_empty": diff_is_empty(outside_diff),
        "clean_dtb_only_has_reviewed_wrapper_changes": clean_wrapper_diff_ok(wrapper_diff),
        "quarantine_disabled_and_nonbinding": strings(args.merged, QUARANTINE, "status") == ["disabled"] and "compatible" not in properties(args.merged, QUARANTINE),
    }
    report = {
        "schema": 1,
        "status": "PASS" if not failures and all(checks.values()) else "FAIL",
        "inputs": {
            "dtbo": str(args.dtbo),
            "entry0": str(args.entry),
            "clean_dtb": str(args.clean_dtb),
            "base": str(args.base),
            "merged": str(args.merged),
            "clean_dts": str(args.clean_dts),
        },
        "sha256": hashes,
        "counts": {
            "external_fixup_symbols": len(fixup_symbols),
            "external_fixup_references": external_reference_count,
            "local_fixup_references": local_reference_count,
            "fragments": len(fragments),
            "unique_fragment_destinations": len(set(fragment_destinations.values())),
            "inside_quarantine_phandles": len(inside_phandles),
            "outside_quarantine_phandles": len(outside_phandles),
            "final_reference_cells": final_reference_cells,
        },
        "checks": checks,
        "outside_quarantine_diff": outside_diff,
        "clean_to_wrapper_diff": wrapper_diff,
        "fragment_destinations": fragment_destinations,
        "failures": failures,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    sys.stdout.write(encoded)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, UnicodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
