#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""List what the rc3 CAMSS async notifier will wait for, from a compiled DTB.

`camss_parse_ports()` adds one v4l2-async connection per endpoint under the
`camss` node, keyed on the *remote* endpoint's fwnode, and never checks whether
the remote device is available (drivers/media/v4l2-core/v4l2-async.c:
__v4l2_async_nf_add_fwnode_remote(), __v4l2_async_nf_add_fwnode();
drivers/media/platform/qcom/camss/camss.c: camss_parse_ports()).  A connection
whose remote node is disabled can therefore never be bound, and
v4l2_async_nf_try_complete() will not call the notifier's .complete callback
while the waiting list is non-empty.

This tool prints, per CAMSS endpoint: the port, the remote endpoint path, the
remote device node, its status, and whether a driver can bind it.  It also
prints the count, which is the number of entries on the notifier's waiting list.

Usage:
  camss_async_audit.py DTB
  camss_async_audit.py --base BASE.dtb --new NEW.dtb     # must be identical
"""

import argparse
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
    data = open(path, "rb").read()
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
            (stack[-1].children if stack else []).append(node) if stack else None
            if not stack:
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


def u32(raw, i=0):
    return struct.unpack(">I", raw[4 * i:4 * i + 4])[0]


def audit(dtb):
    root = parse(dtb)
    nodes = list(walk(root))
    phandles = {}
    for n in nodes:
        if "phandle" in n.props:
            phandles[u32(n.props["phandle"])] = n.path
    by_path = {n.path: n for n in nodes}

    camss = [p for p in by_path if p.endswith("/camss@acb3000")]
    if len(camss) != 1:
        raise SystemExit("%s: expected one camss node, found %d" % (dtb, len(camss)))
    base = camss[0]

    rows = []
    for n in sorted(nodes, key=lambda x: x.path):
        if not n.path.startswith(base + "/ports/"):
            continue
        if not n.path.endswith("/endpoint"):
            continue
        port = n.path.split("/ports/")[1].split("/")[0]
        if "remote-endpoint" not in n.props:
            rows.append((port, n.path, "<no remote-endpoint>", "-", "-"))
            continue
        remote = phandles.get(u32(n.props["remote-endpoint"]), "<unresolved>")
        dev = remote.rsplit("/port/", 1)[0] if "/port/" in remote else remote
        dev_node = by_path.get(dev)
        status = ("okay" if dev_node is None or "status" not in dev_node.props
                  else dev_node.props["status"].rstrip(b"\0").decode())
        # of_platform/i2c only create devices for available nodes, so a
        # "disabled" remote is never probed and never registers a subdev.
        bindable = "yes" if status == "okay" else "NO"
        rows.append((port, remote, dev, status, bindable))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dtb", nargs="?")
    ap.add_argument("--base")
    ap.add_argument("--new")
    args = ap.parse_args()

    if args.base and args.new:
        a, b = audit(args.base), audit(args.new)
        if a != b:
            print("MISMATCH: CAMSS async connection set changed", file=sys.stderr)
            for x in a:
                if x not in b:
                    print("  only in base: %s" % (x,), file=sys.stderr)
            for x in b:
                if x not in a:
                    print("  only in new:  %s" % (x,), file=sys.stderr)
            return 1
        print("OK: %d CAMSS async connections, identical to the baseline" % len(a))
        for row in a:
            print("  port %-2s %-70s status=%s bindable=%s"
                  % (row[0], row[1], row[3], row[4]))
        return 0

    if not args.dtb:
        raise SystemExit(__doc__)
    rows = audit(args.dtb)
    print("%d CAMSS async connection(s) on the notifier waiting list" % len(rows))
    for row in rows:
        print("  port %-2s remote=%-70s dev=%-52s status=%-9s bindable=%s"
              % row)
    unbindable = [r for r in rows if r[4] == "NO"]
    if unbindable:
        print("\nWARNING: %d connection(s) can never be bound; "
              "v4l2_async_nf_try_complete() will never run "
              "camss_subdev_notifier_complete(), so no pad links and no "
              "/dev/video* nodes are created." % len(unbindable))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
