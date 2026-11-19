#!/usr/bin/env python3
"""Validate rc4k NFC wiring in a compiled DTB; no hardware access."""

import sys

from dt_facts import parse, status, u32, walk


def check(path):
    nodes = list(walk(parse(path)))
    by_path = {node.path: node for node in nodes}
    phandles = {u32(n.props["phandle"]): n for n in nodes if "phandle" in n.props}
    matches = [n for n in nodes
               if b"nxp,nxp-nci-i2c\0" in n.props.get("compatible", b"")]
    assert len(matches) == 1, "expected exactly one NXP NCI controller"
    nfc = matches[0]
    bus = by_path[nfc.path.rsplit("/", 1)[0]]
    assert bus.path.endswith("/i2c@a84000"), "NFC must use QUP SE9"
    parent = bus
    while True:
        assert status(parent) == "okay", f"disabled ancestor: {parent.path}"
        if parent.path == "/":
            break
        parent = by_path[parent.path.rsplit("/", 1)[0] or "/"]
    assert status(nfc) == "okay"
    assert u32(bus.props["clock-frequency"]) == 100000
    assert "dmas" not in bus.props and "dma-names" not in bus.props
    assert u32(nfc.props["reg"]) == 0x28
    tlmm = phandles[u32(nfc.props["interrupt-parent"])]
    assert tlmm.path.endswith("/pinctrl@3100000")
    assert [u32(nfc.props["interrupts"], i) for i in range(2)] == [47, 4]
    for prop, pin in (("enable-gpios", 41), ("firmware-gpios", 48)):
        raw = nfc.props[prop]
        assert len(raw) == 12
        assert phandles[u32(raw)] is tlmm
        assert [u32(raw, i) for i in (1, 2)] == [pin, 0]
    pins = set()
    raw = nfc.props["pinctrl-0"]
    for i in range(len(raw) // 4):
        group = phandles[u32(raw, i)]
        assert group.path.startswith(tlmm.path + "/")
        assert group.props["function"] == b"gpio\0"
        group_pins = group.props["pins"].rstrip(b"\0").split(b"\0")
        if group_pins == [b"gpio47"]:
            # nxp-nci holds VEN low from probe; a pull-up on the level-high
            # IRQ storms (~226k IRQ/s) and breaks UFS clock scaling (-110).
            assert "bias-pull-down" in group.props, "IRQ47 must be pulled down"
            assert "bias-pull-up" not in group.props, "IRQ47 must not be pulled up"
        else:
            assert "bias-pull-up" in group.props
        pins.update(group_pins)
    assert pins == {b"gpio41", b"gpio47", b"gpio48", b"gpio113"}, pins
    assert "firmware-name" not in nfc.props, "first trial uses existing ROM firmware"
    print(f"PASS: {path}: NFC on SE9, 100 kHz, IRQ47 high pulled down, VEN41/DWL48, no eSE pin")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: check_nfc_dtb.py DTB [DTB ...]")
    for path in sys.argv[1:]:
        check(path)
