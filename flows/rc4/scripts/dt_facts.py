#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Facts about an rc4 guacamole DTB that the release gates depend on.

Reads a compiled DTB (no kernel source, no dtc needed) and prints a JSON blob
plus a human summary:

  camss_connections  one entry per CAMSS endpoint = one v4l2-async connection.
                     camss_parse_ports() queues every endpoint without checking
                     whether the remote device is available, and
                     v4l2_async_nf_try_complete() only fires the notifier's
                     .complete callback (pad links + /dev/video* nodes) when all
                     of them are bound.  So `bindable` must be 3/3 in the
                     shipped image; a 4th entry with a disabled remote would
                     break every camera.
  sound_links        every child of /sound with its status; `available` is what
                     qcom_snd_parse_of() sees (of_get_available_child_count),
                     i.e. card->num_links.  rc4 must keep rc3's 11 available
                     links with dp-dai-link the only disabled one.
  fingerprint        node status (must be "disabled").
  popup              presence of the motor node, the front sensor and the
                     CAMSS port@2 (all must be absent-or-disabled).
  displayport        DP controller status, FSA4480 presence, dp-dai-link status
                     (the link must be "disabled").

Usage: dt_facts.py <file.dtb> [--json] [--require-rc4-gates]
Exit status is non-zero when --require-rc4-gates is given and a gate fails.
"""

import argparse
import json
import pathlib
import struct
import sys

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


class Node:
    __slots__ = ("path", "props", "children")

    def __init__(self, path):
        self.path = path
        self.props = {}
        self.children = []


def parse(path):
    data = pathlib.Path(path).read_bytes()
    magic, _total, off_struct, off_strings = struct.unpack(">IIII", data[:16])
    if magic != FDT_MAGIC:
        raise SystemExit("%s: not a DTB" % path)
    strings = data[off_strings:]
    stack, root, cur, pos = [], None, "", off_struct
    while True:
        (token,) = struct.unpack(">I", data[pos:pos + 4])
        pos += 4
        if token == FDT_BEGIN_NODE:
            end = data.index(b"\0", pos)
            name = data[pos:end].decode()
            pos = (end + 4) & ~3
            cur = (cur.rstrip("/") + "/" + name) if name else "/"
            node = Node(cur)
            if stack:
                stack[-1].children.append(node)
            else:
                root = node
            stack.append(node)
        elif token == FDT_END_NODE:
            stack.pop()
            cur = stack[-1].path if stack else ""
        elif token == FDT_PROP:
            length, nameoff = struct.unpack(">II", data[pos:pos + 8])
            pos += 8
            name = strings[nameoff:strings.index(b"\0", nameoff)].decode()
            stack[-1].props[name] = data[pos:pos + length]
            pos = (pos + length + 3) & ~3
        elif token == FDT_NOP:
            continue
        elif token == FDT_END:
            break
        else:
            raise SystemExit("%s: bad FDT token %#x" % (path, token))
    return root


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def u32(raw, index=0):
    return struct.unpack(">I", raw[4 * index:4 * index + 4])[0]


def status(node):
    raw = node.props.get("status")
    return "okay" if raw is None else raw.rstrip(b"\0").decode()


def facts(dtb):
    root = parse(dtb)
    nodes = list(walk(root))
    by_path = {n.path: n for n in nodes}
    phandles = {}
    for n in nodes:
        if "phandle" in n.props:
            phandles[u32(n.props["phandle"])] = n.path

    out = {"dtb": dtb, "camss_connections": [], "sound_links": [],
           "fingerprint": None, "popup": {}, "displayport": {}}

    camss = [p for p in by_path if p.endswith("/camss@acb3000")]
    if camss:
        base = camss[0]
        for node in nodes:
            if not (node.path.startswith(base + "/ports/") and
                    node.path.endswith("/endpoint")):
                continue
            port = node.path.split("/ports/")[1].split("/")[0]
            remote = ("<none>" if "remote-endpoint" not in node.props else
                      phandles.get(u32(node.props["remote-endpoint"]), "<unresolved>"))
            dev = remote.rsplit("/port/", 1)[0] if "/port/" in remote else remote
            dev_status = status(by_path[dev]) if dev in by_path else "missing"
            out["camss_connections"].append({
                "port": port, "remote": remote, "device": dev,
                "device_status": dev_status,
                "bindable": dev_status == "okay"})
    out["camss_connections"].sort(key=lambda c: c["port"])

    for node in nodes:
        path = node.path
        if path.count("/") == 2 and path.startswith("/sound/"):
            name = path.rsplit("/", 1)[1]
            out["sound_links"].append({"name": name, "status": status(node),
                                       "available": status(node) == "okay"})
    out["sound_links"].sort(key=lambda l: l["name"])

    fp = [p for p in by_path if p.endswith("/fingerprint")]
    out["fingerprint"] = {"present": bool(fp),
                          "status": status(by_path[fp[0]]) if fp else None}

    popup_paths = {
        "motor": [p for p in by_path if p.endswith("/camera-popup")],
        "front_sensor": [p for p in by_path if p.endswith("/camera@10") and
                         "i2c-bus@1" in p and "ac4b000" in p],
        "camss_port2": [p for p in by_path
                        if "/camss@acb3000/ports/port@2" in p],
    }
    for key, paths in popup_paths.items():
        out["popup"][key] = {
            "present": bool(paths),
            "status": status(by_path[paths[0]]) if paths else None}

    def compat(node):
        raw = node.props.get("compatible", b"")
        return [c.decode() for c in raw.split(b"\0") if c]

    dp = [p for p in by_path if "displayport-controller@ae90000" in p]
    out["displayport"]["controller"] = {
        "present": bool(dp), "status": status(by_path[dp[0]]) if dp else None}
    fsa = [p for p in by_path if "fcs,fsa4480" in compat(by_path[p])]
    out["displayport"]["fsa4480"] = {
        "present": bool(fsa), "path": fsa[0] if fsa else None}
    link = by_path.get("/sound/dp-dai-link")
    out["displayport"]["dp_dai_link"] = {
        "present": link is not None,
        "status": status(link) if link is not None else None}

    out["sound_child_count"] = sum(1 for n in nodes
                                   if n.path.count("/") == 2 and
                                   n.path.startswith("/sound/"))
    out["sound_available_links"] = sum(1 for l in out["sound_links"] if l["available"])
    return out


def require_gates(f):
    """The rc4 default gates.  Returns a list of failure strings."""
    bad = []
    conns = f["camss_connections"]
    if len(conns) != 3 or not all(c["bindable"] for c in conns):
        bad.append("CAMSS: expected 3 bindable async connections, got %d (%s)"
                   % (len(conns), ", ".join("%s:%s" % (c["port"], c["device_status"])
                                            for c in conns)))
    if f["sound_available_links"] != 11:
        bad.append("ALSA: expected 11 available sound links, got %d"
                   % f["sound_available_links"])
    dp = f["displayport"]["dp_dai_link"]
    if not dp["present"] or dp["status"] != "disabled":
        bad.append("DP audio link must be present and disabled, got %r" % (dp,))
    if not f["displayport"]["controller"]["present"] or \
            f["displayport"]["controller"]["status"] != "okay":
        bad.append("DP controller must be enabled (video candidate), got %r"
                   % (f["displayport"]["controller"],))
    if not f["displayport"]["fsa4480"]["present"]:
        bad.append("FSA4480 node missing")
    if not f["fingerprint"]["present"] or f["fingerprint"]["status"] != "disabled":
        bad.append("fingerprint node must be present and disabled, got %r"
                   % (f["fingerprint"],))
    if f["popup"]["front_sensor"]["present"]:
        bad.append("popup front sensor must not be emitted in the default DTB")
    if f["popup"]["camss_port2"]["present"]:
        bad.append("CAMSS port@2 must not be emitted in the default DTB")
    if f["popup"]["motor"]["present"] and f["popup"]["motor"]["status"] != "disabled":
        bad.append("popup motor node must be disabled when present")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dtb")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--require-rc4-gates", action="store_true")
    args = ap.parse_args()
    f = facts(args.dtb)
    if args.json:
        print(json.dumps(f, indent=2))
    else:
        print("%s: %d sound children, %d available links"
              % (args.dtb, f["sound_child_count"], f["sound_available_links"]))
        for c in f["camss_connections"]:
            print("  CAMSS %-7s %-70s device_status=%-9s bindable=%s"
                  % (c["port"], c["remote"], c["device_status"], c["bindable"]))
        print("  fingerprint=%s popup=%s" % (f["fingerprint"], f["popup"]))
        print("  displayport=%s" % (f["displayport"],))
        disabled = [l["name"] for l in f["sound_links"] if not l["available"]]
        print("  disabled sound links: %s" % (", ".join(disabled) or "none"))
    if args.require_rc4_gates:
        bad = require_gates(f)
        for line in bad:
            print("GATE FAIL: " + line, file=sys.stderr)
        if bad:
            return 1
        print("rc4 gates: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
