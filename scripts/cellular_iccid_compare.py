#!/usr/bin/env python3
"""Compare the ICCID views ModemManager uses, without printing the ICCID.

Why this exists
---------------
The current guacamole loop logs "SIM identifier has changed" and then disables/
reprobes the modem. In ModemManager 1.25.95 (commit 7f0bf17), a QMI SIM created
from UIM Get Slot Status gets its cached "sim-identifier" from the slot-status
ICCID. The later SIM-swap recheck reads EF2FE2 (UIM Read Transparent) instead.
Both MM paths call mm_3gpp_parse_iccid(), so an ordinary same-byte ICCID
compares equal; the legacy DMS path returns a string directly. This diagnostic
captures the slot-status, DMS and EF2FE2 views plus the cached property, and
reports only lengths, the industry prefix (89/98/non-standard) and salted
SHA-256 equality.

It never prints ICCID, IMSI, PIN, phone number or raw QMI output. It uses only
read-only public interfaces (mmcli and qmicli UIM get-slot-status,
get-card-status, DMS get-iccid and read-transparent). It does not stop/start
ModemManager, the modem, QRTR or provisioning sessions, and it does not change
the SIM slot.

`--slot-summary` runs only the slot-status, card-status and modem-property
queries and prints no identifier at all (not even a hash). Use it as the
shortest discriminator when the question is which physical slot is ACTIVE, its
logical slot, and which provisioning session EF2FE2 follows.

The USER runs it; a worker must not run live device commands.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import secrets
import subprocess
import sys
from dataclasses import dataclass, field

MM_ICCID_KEY = "sim.properties.iccid"
SLOT_ICCID_RE = re.compile(r"^\s*ICCID:\s*(\S+)\s*$")
HEX_TOKEN_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{2}(?![0-9A-Fa-f])")


class IccidError(ValueError):
    """Reason useful for a report; never contains the raw value."""


def mm_parse_iccid(raw: str | None) -> str:
    """Python port of mm_3gpp_parse_iccid() from ModemManager 7f0bf17."""
    if raw is None:
        raise IccidError("no data")

    # Skip leading spaces and quotes, as the C implementation does.
    start = 0
    while start < len(raw) and (raw[start].isspace() or raw[start] == '"'):
        start += 1
    text = raw[start:]

    buf = list(text)
    length = len(text)
    for i, ch in enumerate(text):
        if ch in "0123456789":
            continue
        if i >= 6 and ch.upper() in "ABCDEF":
            buf[i] = ch.upper()
            continue
        if ch == '"':
            length = i
            break
        raise IccidError(f"invalid character at index {i}")

    if length < 18 or length > 22:
        raise IccidError(f"length {length} is outside 18..22")

    prefix = "".join(buf[0:2])
    swap = False
    if prefix == "89":
        pass
    elif prefix == "98":
        if length != 20:
            raise IccidError(f"swapped ICCID length is {length}, expected 20")
        swap = True
    else:
        raise IccidError("industry prefix is neither 89 nor 98")

    out = buf[0:length]
    if swap:
        for i in range(0, length, 2):
            out[i], out[i + 1] = out[i + 1], out[i]
    if out and out[-1] == "F":
        out = out[:-1]
    return "".join(out)


def qmicli_decode_iccid(data: bytes) -> str:
    """Match qmicli's bcd_chars low-nibble-first decode and g_print truncation."""
    digits = "0123456789"
    out: list[str] = []
    for byte in data:
        for nibble in (byte & 0x0F, (byte >> 4) & 0x0F):
            if nibble >= len(digits):
                # qmicli appends NUL for A-F; g_print stops at the first NUL.
                return "".join(out)
            out.append(digits[nibble])
    return "".join(out)


def _clean_iccid(value: str) -> str | None:
    value = value.strip().strip("'\"")
    if value in {"", "unknown", "(null)", "none"}:
        return None
    return value


@dataclass
class SlotStatus:
    number: int
    card_status: str | None = None
    slot_status: str | None = None
    logical_slot: int | None = None
    iccid: str | None = None


def parse_slot_status_details(output: str) -> dict[int, SlotStatus]:
    """Parse qmicli's slot-status print, keeping status and logical-slot fields.

    qmicli prints, per physical slot: Card status, Slot status, an optional
    Logical slot (only for ACTIVE slots) and an optional ICCID.
    """
    slots: dict[int, SlotStatus] = {}
    current: SlotStatus | None = None
    for line in output.splitlines():
        header = re.match(r"^\s*Physical slot (\d+):", line)
        if header:
            current = SlotStatus(number=int(header.group(1)))
            slots[current.number] = current
            continue
        if current is None:
            continue
        match = re.match(r"^\s*Card status:\s*(\S+)\s*$", line)
        if match:
            current.card_status = match.group(1).lower()
            continue
        match = re.match(r"^\s*Slot status:\s*(\S+)\s*$", line)
        if match:
            current.slot_status = match.group(1).lower()
            continue
        match = re.match(r"^\s*Logical slot:\s*(\d+)\s*$", line)
        if match:
            current.logical_slot = int(match.group(1))
            continue
        match = SLOT_ICCID_RE.match(line)
        if match:
            current.iccid = _clean_iccid(match.group(1))
    return slots


def parse_slot_status(output: str) -> dict[int, str | None]:
    return {
        number: slot.iccid for number, slot in parse_slot_status_details(output).items()
    }


def parse_active_slots(output: str) -> list[int]:
    return [
        number
        for number, slot in parse_slot_status_details(output).items()
        if (slot.slot_status or "") == "active"
    ]


def parse_slot_status_active_slot(output: str) -> int | None:
    active = parse_active_slots(output)
    return active[0] if len(active) == 1 else None


def parse_dms_iccid(output: str) -> str | None:
    for line in output.splitlines():
        match = SLOT_ICCID_RE.match(line)
        if match:
            return _clean_iccid(match.group(1))
    return None


def parse_primary_gw_slot(output: str) -> int | None:
    match = re.search(r"^\s*Primary GW:\s+slot '(\d+)'", output, re.MULTILINE)
    if match:
        return int(match.group(1))
    return None


def primary_gw_session_exists(output: str) -> bool | None:
    match = re.search(r"^\s*Primary GW:\s+(.*)$", output, re.MULTILINE)
    if not match:
        return None
    return "session doesn't exist" not in match.group(1)


def parse_read_transparent(output: str) -> bytes | None:
    if "Read result:" not in output:
        return None
    block = output.split("Read result:", 1)[1]
    # Stop before a later section marker/error, but raw data is at the end.
    block = block.split("\n\n", 1)[0]
    tokens = HEX_TOKEN_RE.findall(block)
    if not tokens:
        return b""
    try:
        return bytes(int(token, 16) for token in tokens)
    except ValueError:
        return None


def extract_sections(output: str) -> dict[str, str]:
    markers = {"@@MODEM@@", "@@SIM@@", "@@SLOT@@", "@@CARD@@", "@@DMS@@", "@@READ@@", "@@MODEM_AFTER@@", "@@SIM_AFTER@@"}
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in output.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        if line.strip() in markers:
            current = line.strip()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {key: "\n".join(lines) for key, lines in sections.items()}


def parse_keyvalue(output: str, key: str) -> str | None:
    for line in output.splitlines():
        name, separator, value = line.partition(":")
        if separator and name.strip() == key:
            return value.strip()
    return None


def mm_key_prefix(value: str | None) -> str:
    if not value:
        return "EMPTY"
    prefix = value[:2]
    if prefix in {"89", "98"}:
        return prefix
    return "non-standard"


def make_hash(salt: str, value: str) -> str:
    return hashlib.sha256(f"{salt}\x00{value}".encode()).hexdigest()


def describe(value: str | None, salt: str) -> str:
    if value is None:
        return "UNAVAILABLE (property not exposed)"
    if value == "--":
        return "UNAVAILABLE (mmcli sentinel --)"
    if value == "":
        return "EMPTY (property exposed with zero length)"
    return (
        f"len={len(value)} prefix={mm_key_prefix(value)} "
        f"salted_sha256={make_hash(salt, value)}"
    )


def safe_normalize(raw: bytes) -> tuple[str | None, str | None]:
    try:
        return mm_parse_iccid(raw.hex()), None
    except IccidError as exc:
        return None, str(exc)


@dataclass
class ReportData:
    modem: str = ""
    sim: str = ""
    slot: str = ""
    card: str = ""
    dms: str = ""
    read: str = ""
    modem_after: str = ""
    sim_after: str = ""
    query_status: dict[str, str] = field(default_factory=dict)


def build_report(data: ReportData, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    lines = [
        "Nura cellular ICCID-source comparison",
        "read-only; raw identifiers/errors stay private; hashes share a per-run secret salt",
    ]
    for name, status in data.query_status.items():
        lines.append(f"query {name}: {status}")

    before = (parse_keyvalue(data.modem, "modem.dbus-path"),
              parse_keyvalue(data.modem, "modem.generic.sim"),
              parse_keyvalue(data.sim, "sim.dbus-path"))
    after = (parse_keyvalue(data.modem_after, "modem.dbus-path"),
             parse_keyvalue(data.modem_after, "modem.generic.sim"),
             parse_keyvalue(data.sim_after, "sim.dbus-path"))
    known = all(value not in (None, "", "--", "/") for value in before + after)
    stable = known and before == after and before[1] == before[2]
    identity = "UNCHANGED" if stable else ("CHANGED/INCONSISTENT" if known else "UNKNOWN")
    lines.append(f"Modem/SIM identity before/after: {identity}")
    lines.append("Sequential queries are not an atomic snapshot; unchanged paths cannot rule out transient state changes.")

    cached = parse_keyvalue(data.sim, MM_ICCID_KEY)
    lines.append(f"cached mmcli SIM identifier: {describe(cached, salt)}")
    details = parse_slot_status_details(data.slot)
    active_slots = [
        number for number, slot in details.items() if slot.slot_status == "active"
    ]
    if not details:
        lines.append("UIM slot-status: unavailable")
    for number, slot in details.items():
        logical = "none" if slot.logical_slot is None else str(slot.logical_slot)
        lines.append(
            f"  physical slot {number}: card={slot.card_status or 'unknown'} "
            f"slot={slot.slot_status or 'unknown'} logical={logical} "
            f"ICCID {describe(slot.iccid, salt)}"
        )
    if active_slots:
        lines.append(
            "UIM slot-status active physical slots: "
            + ", ".join(str(number) for number in active_slots)
            + f" ({len(active_slots)} active)"
        )
    else:
        lines.append(
            "UIM slot-status active physical slots: none parsed "
            "(check the query status / slot-status schema)"
        )
    active_slot = active_slots[0] if len(active_slots) == 1 else None
    active = details[active_slot].iccid if active_slot is not None else None
    lines.append(
        "Slot ICCIDs are qmicli decoded strings, possibly truncated at A-F; "
        "no raw slot bytes were captured."
    )
    primary = parse_primary_gw_slot(data.card)
    for session in ("Primary", "Secondary"):
        match = re.search(rf"^\s*{session} GW:\s+slot '(\d+)', application '(\d+)'", data.card, re.MULTILINE)
        if match:
            lines.append(f"{session} GW provisioning session: slot={int(match[1])}, application={int(match[2])}")
        else:
            absent = re.search(rf"^\s*{session} GW:\s+session doesn't exist", data.card, re.MULTILINE)
            lines.append(f"{session} GW provisioning session: {'absent' if absent else 'unavailable'}")
    dms = parse_dms_iccid(data.dms)
    lines.append(f"DMS UIM Get ICCID: {describe(dms, salt)}")
    raw = parse_read_transparent(data.read)
    mm_value = low_value = None
    if raw is None:
        lines.append("EF2FE2 Read Transparent: unavailable; this does not establish PIN lock")
    elif not raw:
        lines.append("EF2FE2 Read Transparent: empty response")
    else:
        mm_value, error = safe_normalize(raw)
        low_value = qmicli_decode_iccid(raw)
        lines.append(f"EF2FE2 Read Transparent: raw_bytes={len(raw)}")
        lines.append(f"  ModemManager normalization: {describe(mm_value, salt) if error is None else error}")
        lines.append(f"  qmicli low-nibble decode: {describe(low_value, salt)}")

    def compare(label: str, left: str | None, right: str | None, require_identity: bool = False) -> None:
        if require_identity and not stable:
            result = "UNKNOWN (modem/SIM identity not verified)"
        elif left in (None, "--") or right in (None, "--"):
            result = "UNKNOWN (one side unavailable)"
        else:
            result = "YES" if left == right else "NO"
        lines.append(f"  {label}: {result}")

    lines.append("comparisons (observed values; YES means equal):")
    if len(active_slots) > 1:
        ambiguous = f"UNKNOWN (ambiguous: {len(active_slots)} active slots)"
        lines.append(f"  cached == active-slot ICCID: {ambiguous}")
        lines.append(f"  active-slot ICCID == EF2FE2(MM normalization): {ambiguous}")
        lines.append(f"  active-slot ICCID == EF2FE2(qmicli decode): {ambiguous}")
    else:
        compare("cached == active-slot ICCID", cached, active, True)
        compare("active-slot ICCID == EF2FE2(MM normalization)", active, mm_value)
        compare("active-slot ICCID == EF2FE2(qmicli decode)", active, low_value)
    compare("cached == EF2FE2(MM normalization)", cached, mm_value, True)
    compare("cached == DMS Get ICCID", cached, dms, True)
    compare("DMS Get ICCID == EF2FE2(MM normalization)", dms, mm_value)
    if len(active_slots) == 1:
        compare("slot-status active slot == primary-GW provisioning slot",
                str(active_slot),
                str(primary) if primary is not None else None)
    elif active_slots:
        if primary is None:
            membership = "UNKNOWN (primary-GW session unavailable)"
        else:
            membership = "YES" if primary in active_slots else "NO"
        lines.append(
            "  primary-GW provisioning slot is among the active slots: "
            + membership
        )
    else:
        lines.append(
            "  slot-status active slot == primary-GW provisioning slot: "
            "UNKNOWN (no active slot parsed)"
        )
    lines.append("interpretation hints (hypotheses, not hardware proof):")
    if cached == "":
        lines.append("  - The cached identifier is EMPTY in the exposed property.")
    if stable and cached not in (None, "", "--") and active and mm_value:
        if cached == active and cached != mm_value:
            lines.append("  - Cached matches active slot but not EF2FE2: investigate slot-status decoding, session mapping and timing.")
        elif cached == mm_value and cached != active:
            lines.append("  - Cached matches EF2FE2 but not active slot: investigate initial SIM source and slot mapping.")
        elif cached == dms and cached != active:
            lines.append("  - Cached matches DMS UIM Get ICCID; equality alone does not prove a legacy DMS initialization path.")
        elif cached == active == mm_value:
            lines.append("  - All three agree at this sample; a transient mismatch remains possible.")
    if active_slot is not None and primary is not None and active_slot != primary:
        lines.append("  - The active slot differs from the primary-GW session slot; EF2FE2 may select another SIM.")
    if len(active_slots) > 1:
        lines.append(
            "  - More than one physical slot reports ACTIVE. ModemManager's "
            "uim_get_slot_status_ready() keeps the LAST active physical slot as the "
            "primary SIM, while EF2FE2 Read Transparent follows the PRIMARY-GW "
            "provisioning session. Compare the same-instant modem primary slot with "
            "these active slots before changing any code."
        )
    if primary is not None and active_slots and primary not in active_slots:
        lines.append(
            "  - The primary-GW session slot is not among the slots reported ACTIVE; "
            "EF2FE2 may select another SIM."
        )
    return "\n".join(lines)


def build_slot_summary(data: ReportData) -> str:
    """Identifier-free slot/session discriminator (no ICCID, IMSI or hash)."""
    details = parse_slot_status_details(data.slot)
    active_slots = [
        number for number, slot in details.items() if slot.slot_status == "active"
    ]
    lines = [
        "Nura cellular slot/session discriminator",
        "read-only; identifiers are not printed, not even hashed",
    ]
    for name, status in data.query_status.items():
        lines.append(f"query {name}: {status}")
    if not details:
        lines.append("UIM slot-status: unavailable")
    for number, slot in details.items():
        logical = "none" if slot.logical_slot is None else str(slot.logical_slot)
        lines.append(
            f"physical slot {number}: card={slot.card_status or 'unknown'} "
            f"slot={slot.slot_status or 'unknown'} logical={logical}"
        )
    if active_slots:
        lines.append(
            "active physical slots: "
            + ", ".join(str(number) for number in active_slots)
            + f" ({len(active_slots)} active)"
        )
    else:
        lines.append("active physical slots: none parsed (check the query status)")
    modem_primary = parse_keyvalue(data.modem, "modem.generic.primary-sim-slot")
    lines.append(f"modem primary sim slot: {modem_primary or 'unavailable'}")
    lines.append(
        "modem state: "
        + (parse_keyvalue(data.modem, "modem.generic.state") or "unavailable")
    )
    primary = parse_primary_gw_slot(data.card)
    for label in ("Primary", "Secondary"):
        match = re.search(
            rf"^\s*{label} GW:\s+slot '(\d+)', application '(\d+)'",
            data.card,
            re.MULTILINE,
        )
        if match:
            lines.append(
                f"{label.lower()} GW provisioning session: "
                f"slot {int(match[1])}, application {int(match[2])}"
            )
        else:
            absent = re.search(
                rf"^\s*{label} GW:\s+session doesn't exist",
                data.card,
                re.MULTILINE,
            )
            lines.append(
                f"{label.lower()} GW provisioning session: "
                + ("absent" if absent else "unavailable")
            )
    if len(active_slots) > 1:
        lines.append(
            "note: more than one ACTIVE physical slot; ModemManager keeps the last "
            "active physical slot as primary (uim_get_slot_status_ready), qmicli's "
            "switch-slot helper takes the first, and Read Transparent follows the "
            "primary-GW session."
        )
    if modem_primary is not None and primary is not None:
        try:
            same_slot = int(modem_primary) == primary
        except ValueError:
            same_slot = False
        lines.append(
            "same-instant modem primary slot vs primary-GW session: "
            + ("MATCH" if same_slot else "MISMATCH")
        )
    return "\n".join(lines)


def query_result(output: str) -> tuple[str, str]:
    """Separate private command output from a safe fixed-category summary."""
    body, marker, code = output.rpartition("@@EXIT@@ ")
    if not marker or not re.fullmatch(r"[0-9]{1,3}\s*", code):
        return output, "status unavailable"
    exit_code = int(code)
    if exit_code == 0:
        return body, "exit=0 success"
    qmi = re.search(r"QMI protocol error \(([0-9]{1,5})\)", body)
    if qmi:
        category = f"QMI-error code={int(qmi[1])}"
    elif re.search(r"permission denied|not authorized|authentication|sudo:", body, re.IGNORECASE):
        category = "permission/authentication"
    elif re.search(r"timed? out|timeout", body, re.IGNORECASE):
        category = "timeout"
    elif exit_code == 127:
        category = "command unavailable"
    else:
        category = "query failed"
    # Failed stdout is not usable as a property/file result either.
    return "", f"exit={exit_code} {category}"


def remote_script(use_sudo: bool) -> str:
    prefix = "sudo " if use_sudo else ""
    lines: list[str] = []
    if use_sudo:
        lines.append("if ! sudo -v; then echo '@@NO-SUDO@@'; exit 0; fi")
    queries = [
        ("MODEM", "mmcli -m any --output-keyvalue"),
        ("SIM", "mmcli -i any --output-keyvalue"),
        ("SLOT", "qmicli --silent -pd qrtr://0 --uim-get-slot-status"),
        ("CARD", "qmicli --silent -pd qrtr://0 --uim-get-card-status"),
        ("DMS", "qmicli --silent -pd qrtr://0 --dms-uim-get-iccid"),
        ("READ", "qmicli --silent -pd qrtr://0 --uim-read-transparent=0x3F00,0x2FE2"),
        ("MODEM_AFTER", "mmcli -m any --output-keyvalue"),
        ("SIM_AFTER", "mmcli -i any --output-keyvalue"),
    ]
    for name, command in queries:
        lines.extend([f"echo '@@{name}@@'", f"{prefix}{command} 2>&1", "printf '\n@@EXIT@@ %s\n' \"$?\""])

    return "; ".join(lines)


def slot_summary_script(use_sudo: bool) -> str:
    """Shortest read-only discriminator: slot status, card status, modem slot."""
    prefix = "sudo " if use_sudo else ""
    lines: list[str] = []
    if use_sudo:
        lines.append("if ! sudo -v; then echo '@@NO-SUDO@@'; exit 0; fi")
    queries = [
        ("MODEM", "mmcli -m any --output-keyvalue"),
        ("SLOT", "qmicli --silent -pd qrtr://0 --uim-get-slot-status"),
        ("CARD", "qmicli --silent -pd qrtr://0 --uim-get-card-status"),
    ]
    for name, command in queries:
        lines.extend([f"echo '@@{name}@@'", f"{prefix}{command} 2>&1", "printf '\n@@EXIT@@ %s\n' \"$?\""])

    return "; ".join(lines)


def run_ssh(host: str, use_sudo: bool, script: str) -> str:
    command = [
        "ssh",
        "-o",
        "BatchMode=no",
        "-t" if use_sudo else "-T",
        host,
        script,
    ]
    result = subprocess.run(command, text=True, capture_output=True, timeout=180)
    if result.returncode != 0 and "@@SLOT@@" not in result.stdout:
        raise RuntimeError(
            f"ssh command failed (exit {result.returncode}); no raw output was printed"
        )
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", nargs="?", default="user@172.16.42.1")
    parser.add_argument("--local", action="store_true", help="run on the phone directly")
    parser.add_argument(
        "--no-sudo",
        action="store_true",
        help="do not prefix remote commands with sudo (qmicli may fail)",
    )
    parser.add_argument(
        "--slot-summary",
        action="store_true",
        help="shortest read-only discriminator: slot/logical/GW sessions, no identifiers",
    )
    args = parser.parse_args()
    script = (
        slot_summary_script(not args.no_sudo)
        if args.slot_summary
        else remote_script(not args.no_sudo)
    )

    try:
        if args.local:
            if not args.no_sudo:
                subprocess.run(["sudo", "-v"], check=True)
            what = "slot/session" if args.slot_summary else "modem/SIM"
            print(f"Reading {what} information...", file=sys.stderr, flush=True)
            output = subprocess.run(
                ["sh", "-c", script],
                text=True, capture_output=True, timeout=180,
            ).stdout
        else:
            output = run_ssh(args.host, not args.no_sudo, script)
    except subprocess.TimeoutExpired:
        print("error: modem queries timed out; no raw output was printed", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError:
        print("error: sudo authentication failed", file=sys.stderr)
        return 2
    except (OSError, RuntimeError):
        print("error: query transport/execution failed; raw errors withheld", file=sys.stderr)
        return 2

    sections = extract_sections(output)
    if "@@NO-SUDO@@" in output:
        print("error: sudo authentication failed; rerun and enter your sudo password", file=sys.stderr)
        return 2
    if not any(key in sections for key in ("@@MODEM@@", "@@SIM@@", "@@SLOT@@", "@@READ@@")):
        print("error: unexpected remote output; no raw output was printed", file=sys.stderr)
        return 2

    statuses = {}
    for name, output in sections.items():
        sections[name], statuses[name.strip("@")] = query_result(output)

    if args.slot_summary:
        print(
            build_slot_summary(
                ReportData(
                    modem=sections.get("@@MODEM@@", ""),
                    slot=sections.get("@@SLOT@@", ""),
                    card=sections.get("@@CARD@@", ""),
                    query_status={
                        name: status
                        for name, status in statuses.items()
                        if name in {"MODEM", "SLOT", "CARD"}
                    },
                )
            )
        )
        return 0

    report = build_report(
        ReportData(
            modem=sections.get("@@MODEM@@", ""),
            sim=sections.get("@@SIM@@", ""),
            slot=sections.get("@@SLOT@@", ""),
            card=sections.get("@@CARD@@", ""),
            dms=sections.get("@@DMS@@", ""),
            read=sections.get("@@READ@@", ""),
            modem_after=sections.get("@@MODEM_AFTER@@", ""),
            sim_after=sections.get("@@SIM_AFTER@@", ""),
            query_status=statuses,
        )
    )
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
