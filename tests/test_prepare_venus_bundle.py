"""Unit tests for scripts/prepare_venus_bundle.py (synthetic firmware only)."""

import struct
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import prepare_venus_bundle as pvb  # noqa: E402


def make_mdt(segs):
    """segs: list of (p_type, paddr, filesz, memsz, flags)."""
    phoff = 0x34
    hdr = bytearray(phoff)
    hdr[:6] = b"\x7fELF\x01\x01"
    struct.pack_into("<I", hdr, 0x1C, phoff)
    struct.pack_into("<HH", hdr, 0x2A, 32, len(segs))
    ph = b"".join(struct.pack("<8I", t, 0, pa, pa, fs, ms, fl, 0x1000)
                  for t, pa, fs, ms, fl in segs)
    return bytes(hdr) + ph


SEGS = [
    (0, 0, 0xD4, 0, 0x07000000),            # ELF header copy
    (0, 0x0FA00000, 0x10, 0x2000, 0x02200000),  # hash segment
    (1, 0x0F500000, 0x20, 0x101FA4, 0x08000005),
    (1, 0x0F9FF000, 0x08, 0x20, 0x08000006),
]


def good_files():
    files = {"venus.mdt": make_mdt(SEGS)}
    for i, (_t, _pa, fs, _ms, _fl) in enumerate(SEGS):
        if fs:
            files["venus.b%02d" % i] = bytes(fs)
    return files


def expect_fail(test, fn, *args):
    with test.assertRaises(SystemExit) as cm:
        fn(*args)
    test.assertEqual(cm.exception.code, 1)


class ValidateTest(unittest.TestCase):
    def test_good_split_firmware(self):
        self.assertEqual(pvb.validate(good_files()), 0x0F9FF020 - 0x0F500000)

    def test_hash_segment_is_not_in_the_load_span(self):
        span = pvb.validate(good_files())
        self.assertLess(span, 0x0FA00000 - 0x0F500000)

    def test_missing_blob(self):
        files = good_files()
        del files["venus.b02"]
        expect_fail(self, pvb.validate, files)

    def test_wrong_blob_size(self):
        files = good_files()
        files["venus.b03"] = bytes(7)
        expect_fail(self, pvb.validate, files)

    def test_not_an_elf(self):
        files = good_files()
        files["venus.mdt"] = b"not an elf" + bytes(64)
        expect_fail(self, pvb.validate, files)

    def test_only_the_hash_segment_is_left_out_of_the_span(self):
        # A PT_LOAD with Qualcomm segment type 7 still counts, as in
        # qcom_mdt_get_size(); here it pushes the span past venus_mem.
        segs = SEGS + [(1, 0x0FA00000, 0x08, 0x20, 0x07000000)]
        files = {"venus.mdt": make_mdt(segs)}
        for i, (_t, _pa, fs, _ms, _fl) in enumerate(segs):
            if fs:
                files["venus.b%02d" % i] = bytes(fs)
        expect_fail(self, pvb.validate, files)

    def test_span_larger_than_venus_mem(self):
        segs = SEGS[:2] + [(1, 0x0F500000, 0x20, 0x600000, 0x08000005)]
        files = {"venus.mdt": make_mdt(segs)}
        for i, (_t, _pa, fs, _ms, _fl) in enumerate(segs):
            if fs:
                files["venus.b%02d" % i] = bytes(fs)
        expect_fail(self, pvb.validate, files)


class BundleTest(unittest.TestCase):
    def test_reproducible_and_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            for name, data in good_files().items():
                (src / name).write_bytes(data)
            (src / "notes.txt").write_text("ignored\n")
            files = pvb.read_source(src)
            self.assertNotIn("notes.txt", files)
            a = pvb.write_bundle(files, Path(tmp) / "a.tar.gz")
            b = pvb.write_bundle(files, Path(tmp) / "b.tar.gz")
            self.assertEqual(a, b)
            with tarfile.open(Path(tmp) / "a.tar.gz") as tar:
                names = tar.getnames()
                self.assertEqual(names[0], "guacamole-venus")
                self.assertIn("guacamole-venus/venus.mdt", names)
                self.assertTrue(all(m.mtime == 0 and m.uid == 0 for m in tar.getmembers()))


if __name__ == "__main__":
    unittest.main()
