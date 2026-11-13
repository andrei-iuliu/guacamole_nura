#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Structural regression check between two compiled DTBs.

Adding device-tree nodes renumbers every phandle, so a `dtc -O dts` diff of a
"before" and "after" build is dominated by numeric noise.  This script parses
both FDTs, replaces every 32-bit cell whose value is a phandle *of either tree*
with the token `PH`, and then compares node/property/value triples.

`PH` normalisation is symmetric: a value that looks like a reference in one
tree is normalised in both, so phandle renumbering cannot produce a difference.
The trade-off is that a real change between two small values that both happen
to be phandles is invisible; cell counts, strings and byte arrays are still
compared exactly.

Usage:
  dtb_regression.py --base A.dtb --new B.dtb [--allow PATH:PROP]... \
                    [--allow-new-prefix PATH]...

Exit status 0 means every difference is covered by an explicit --allow or
--allow-new-prefix, no node disappeared, and no unexpected property appeared on
a pre-existing node.  Nothing is written.
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
    stack, root, cur = [], None, ""
    pos = off_struct
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


def phandle_values(roots):
    values = set()
    for root in roots:
        for node in walk(root):
            raw = node.props.get("phandle")
            if raw and len(raw) == 4:
                values.add(struct.unpack(">I", raw)[0])
    return values


def canonical(root, phandles):
    table = {}
    for node in walk(root):
        props = {}
        for name, raw in node.props.items():
            if name in ("phandle", "linux,phandle"):
                props[name] = "<self>"
            elif len(raw) % 4 == 0 and raw:
                cells = struct.unpack(">%dI" % (len(raw) // 4), raw)
                props[name] = " ".join(
                    "PH" if c in phandles else "%#x" % c for c in cells)
            else:
                props[name] = repr(raw)
        table[node.path] = props
    return table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--allow", action="append", default=[],
                    help="PATH:PROP allowed to change or disappear")
    ap.add_argument("--allow-new-prefix", action="append", default=[],
                    help="new nodes must live under this path")
    ap.add_argument("--assert-no-new-prefix", action="append", default=[],
                    help="any new node under this path is a failure, even if "
                         "an --allow-new-prefix would accept it")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    base_root, new_root = parse(args.base), parse(args.new)
    ph = phandle_values([base_root, new_root])
    base, new = canonical(base_root, ph), canonical(new_root, ph)

    allowed = set()
    for item in args.allow:
        path, _, prop = item.rpartition(":")
        allowed.add((path, prop))

    problems = []
    intended = []
    for path, props in base.items():
        if path not in new:
            problems.append("node disappeared: %s" % path)
            continue
        for name, value in props.items():
            if name not in new[path]:
                if (path, name) in allowed:
                    intended.append("removed   %s %s" % (path, name))
                else:
                    problems.append("property removed: %s %s" % (path, name))
            elif new[path][name] != value:
                if (path, name) in allowed:
                    intended.append("changed   %s %s: %s -> %s"
                                    % (path, name, value, new[path][name]))
                else:
                    problems.append("property changed: %s %s: %s -> %s"
                                    % (path, name, value, new[path][name]))
        for name in new[path]:
            if name in props:
                continue
            if (path, name) in allowed:
                intended.append("added     %s %s = %s"
                                % (path, name, new[path][name]))
            else:
                problems.append("property added to an existing node: %s %s"
                                % (path, name))

    new_nodes = [p for p in new if p not in base]
    for pre in args.assert_no_new_prefix:
        pre = pre.rstrip("/")
        for path in new_nodes:
            if path == pre or path.startswith(pre + "/"):
                problems.append(
                    "new node under a forbidden path (%s): %s" % (pre, path))
    for path in new_nodes:
        if not any(path == pre or path.startswith(pre.rstrip("/") + "/")
                   for pre in args.allow_new_prefix):
            problems.append("new node outside the allowlist: %s" % path)

    if not args.quiet:
        print("base nodes: %d, new nodes: %d, new: %d"
              % (len(base), len(new), len(new_nodes)))
        for line in intended:
            print("  intended: " + line)
        for path in sorted(new_nodes):
            print("  added:    " + path)
    for line in problems:
        print("PROBLEM: " + line, file=sys.stderr)
    if problems:
        return 1
    print("OK: %d new nodes, %d intended property changes, no other difference"
          % (len(new_nodes), len(intended)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
