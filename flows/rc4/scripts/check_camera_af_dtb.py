#!/usr/bin/env python3
"""Validate rc4n rear autofocus wiring in a compiled DTB; no hardware access."""

import sys

from dt_facts import parse, status, u32, walk


def check(path):
    nodes = list(walk(parse(path)))
    phandles = {u32(n.props["phandle"]): n for n in nodes if "phandle" in n.props}
    for sensor_compatible, lens_reg in ((b"sony,imx586\0", 0x72),
                                        (b"samsung,s5k3m5\0", 0x74)):
        name = sensor_compatible.rstrip(b"\0").decode()
        matches = [n for n in nodes
                   if sensor_compatible in n.props.get("compatible", b"")]
        assert len(matches) == 1, f"expected exactly one {name} sensor"
        sensor = matches[0]
        assert "lens-focus" in sensor.props, f"{sensor.path}: missing lens-focus"
        lens = phandles[u32(sensor.props["lens-focus"])]
        assert u32(lens.props["reg"]) == lens_reg, f"{sensor.path}: wrong lens"
        assert b"onnn,lc898217xc\0" in lens.props.get("compatible", b"")
        assert status(lens) == "okay"
        assert u32(lens.props["onnn,max-focus-position"]) == 400
        assert u32(lens.props["onnn,default-focus-position"]) == 400
    print(f"PASS: {path}: imx586+lens@72 and s5k3m5+lens@74, LC898217XC, 0-400")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: check_camera_af_dtb.py DTB [DTB ...]")
    for path in sys.argv[1:]:
        check(path)
