#!/usr/bin/env python3
"""Rootless validation for the opt-in ModemManager primary-SIM-slot candidate.

No build, install or ModemManager run. The suite checks:

* the vendored APKBUILD snapshot is the exact pinned aports file and the delta
  changes only pkgrel/source/sha512sums (no invented checksum);
* the superseded identifier patch is archived byte-identically and is not in the
  recipe, so it cannot ship as a default;
* the source patch applies to the exact pinned ModemManager blobs and changes only
  ``src/mm-shared-qmi.c``;
* the extracted pure ``select_primary_sim_slot()`` helper is compiled with the
  host C compiler and behaves correctly for single/dual ACTIVE slots, Primary GW
  on either slot, unknown session, SIM-less session, reversed order and no card;
* the integration uses the card-status Primary GW index and the helper, while
  swap detection, the slot-status indication path and the inactive-slot path
  remain byte-identical to upstream.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_ROOT / "device" / "testing" / "modemmanager"
SOURCE_PATCH = PKG_DIR / "0002-primary-sim-slot-selection.patch"
APKBUILD_PATCH = PKG_DIR / "apkbuild.patch"
APKBUILD_UPSTREAM = PKG_DIR / "APKBUILD.upstream"
OLD_PATCH = PKG_DIR / "superseded" / "0002-active-sim-identifier-from-ef2fe2.patch"

MM_COMMIT = "7f0bf17ab518cd78925cf7e53cc0d5c42f88b41a"
MM_PKGVER = "1.25.95_git20261005"
UPSTREAM_APKBUILD_SHA256 = (
    "c952de6a4a9a7351904a575ae2ce8f4a683e841b572bb588e46e5b1159610516"
)
TARBALL_SHA512 = (
    "e049eecc104c989f516402ca7c21e3374c152e3840cf05c34ac91bc8e3305c83"
    "b53693bc0964f657664d4156baaac1a4e0fa35dd577733623dd669b67f1675f6"
)
OLD_PATCH_SHA256 = (
    "eb05c7a28b8f6ec07cd6d6fd3aa57a94f62c61f6ef625cb83e718670ddaff309"
)
PATCH_NAME = "0002-primary-sim-slot-selection.patch"

DEFAULT_MM_REPO = Path(
    "bringup-workspace/sources/modemmanager-current"
)

SOURCE_FILES = (
    "src/mm-shared-qmi.c",
    "src/mm-broadband-modem.c",
    "src/mm-base-sim.c",
    "src/mm-sim-qmi.c",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha512(path: Path) -> str:
    return hashlib.sha512(path.read_bytes()).hexdigest()


def _pinned_repo() -> tuple[Path | None, str]:
    candidates = []
    env = os.environ.get("MODEM_MANAGER_SOURCE_REPO")
    if env:
        candidates.append(Path(env))
    candidates.append(DEFAULT_MM_REPO)
    for candidate in candidates:
        if not (candidate / ".git").exists():
            continue
        try:
            head = subprocess.run(
                ["git", "-C", str(candidate), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            continue
        if head == MM_COMMIT:
            return candidate, ""
        return None, f"checkout {candidate} is at {head}, expected {MM_COMMIT}"
    return None, "no pinned ModemManager checkout found; set MODEM_MANAGER_SOURCE_REPO"


SOURCE_REPO, SOURCE_REPO_SKIP_REASON = _pinned_repo()


def _git_show(repo: Path, path: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "show", f"HEAD:{path}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _patched_tree(repo: Path, tmp: Path) -> dict[str, str]:
    for path in SOURCE_FILES:
        target = tmp / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_git_show(repo, path))
    subprocess.run(
        ["git", "apply", "--check", "-p1", str(SOURCE_PATCH)],
        cwd=tmp,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "apply", "-p1", str(SOURCE_PATCH)],
        cwd=tmp,
        check=True,
        capture_output=True,
    )
    return {path: (tmp / path).read_text() for path in SOURCE_FILES}


# --- small C scanners (comments and string literals are ignored) -------------


def _skip_c_construct(text: str, index: int) -> int:
    if text.startswith("/*", index):
        end = text.find("*/", index + 2)
        return len(text) if end < 0 else end + 2
    if text.startswith("//", index):
        end = text.find("\n", index + 2)
        return len(text) if end < 0 else end
    if text[index] in "\"'":
        quote = text[index]
        index += 1
        while index < len(text):
            if text[index] == "\\":
                index += 2
                continue
            if text[index] == quote:
                return index + 1
            index += 1
        return len(text)
    return index


def _matching_close(
    text: str, open_index: int, opening: str = "(", closing: str = ")"
) -> int:
    depth = 0
    index = open_index
    while index < len(text):
        if (
            text.startswith("/*", index)
            or text.startswith("//", index)
            or text[index] in "\"'"
        ):
            index = _skip_c_construct(text, index)
            continue
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise AssertionError(f"unbalanced {opening!r} at index {open_index}")


def _function_body(text: str, signature: str) -> str:
    start = text.index(signature)
    open_paren = text.index("(", start)
    close_paren = _matching_close(text, open_paren)
    open_brace = text.index("{", close_paren)
    close_brace = _matching_close(text, open_brace, "{", "}")
    return text[open_brace : close_brace + 1]


def _function_definition(text: str, signature: str) -> str:
    """Return the full function text starting at ``signature`` (decl. + body)."""
    start = text.index(signature)
    open_paren = text.index("(", start)
    close_paren = _matching_close(text, open_paren)
    open_brace = text.index("{", close_paren)
    close_brace = _matching_close(text, open_brace, "{", "}")
    return text[start : close_brace + 1]


class PackagingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.upstream = APKBUILD_UPSTREAM.read_text()

    def test_upstream_apkbuild_is_the_recorded_pin(self) -> None:
        self.assertEqual(UPSTREAM_APKBUILD_SHA256, _sha256(APKBUILD_UPSTREAM))
        self.assertIn(f"pkgver={MM_PKGVER}\n", self.upstream)
        self.assertIn(f"_commit={MM_COMMIT}\n", self.upstream)
        self.assertIn(TARBALL_SHA512, self.upstream)

    def test_delta_changes_only_three_lines_and_keeps_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            work = Path(tmp_name)
            (work / "APKBUILD").write_text(self.upstream)
            subprocess.run(
                ["git", "apply", "-p1", str(APKBUILD_PATCH)],
                cwd=work,
                check=True,
                capture_output=True,
            )
            patched = (work / "APKBUILD").read_text()

        self.assertIn(f"pkgver={MM_PKGVER}\n", patched)
        self.assertIn(f"_commit={MM_COMMIT}\n", patched)
        self.assertIn("pkgrel=1\n", patched)
        self.assertIn(f"\t{PATCH_NAME}\n", patched)
        self.assertIn(_sha512(SOURCE_PATCH), patched)
        self.assertNotIn("active-sim-identifier-from-ef2fe2", patched)

        normalised = patched.replace("pkgrel=1\n", "pkgrel=0\n")
        normalised = normalised.replace(f"\t{PATCH_NAME}\n", "", 1)
        normalised = normalised.replace(
            f"{_sha512(SOURCE_PATCH)}  {PATCH_NAME}\n", "", 1
        )
        self.assertEqual(self.upstream, normalised)

        for checksum in (
            TARBALL_SHA512,
            "6fab86c27502b29be0c6610d835a3249a4a81993eb986cff2c3ea9393fadd3f6"
            "93ba4bb0532ae0e50a83d359559511859dd05f7ea2f0cb52839f535a7e49f8a0",
            "6e234cebcdec6d1afbca164ee5c13c96f7132ef31231d5c043cc5750ebe99e3"
            "62a384d0463874b3c1c30799b433e0d3286add03af13c97fa406b4b5d2b78880c",
            "f06e9b89acab5b61c5965658d62551b0f168066384674f7c4dd56feb6f811c8"
            "2c640470e634e32d8c4ef62290588cf974aa5f213e5c67e797a06d33e61fa24ec",
        ):
            self.assertEqual(1, patched.count(checksum))
        self.assertEqual(1, patched.count(_sha512(SOURCE_PATCH)))

    def test_superseded_identifier_patch_is_archived_and_unused(self) -> None:
        self.assertTrue(OLD_PATCH.is_file())
        self.assertEqual(OLD_PATCH_SHA256, _sha256(OLD_PATCH))
        self.assertNotIn("active-sim-identifier", APKBUILD_PATCH.read_text())
        self.assertNotIn("active-sim-identifier", self.upstream)

    def test_candidate_is_not_referenced_by_build_or_install_lists(self) -> None:
        self.assertFalse((PKG_DIR / "APKBUILD").exists())
        tracked = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        watched = [
            path
            for path in tracked
            if path.startswith(("scripts/", "device/testing/device-oneplus-guacamole/"))
        ]
        hits = []
        for path in watched:
            text = (REPO_ROOT / path).read_text(errors="replace")
            if "testing/modemmanager" in text or PATCH_NAME in text:
                hits.append(path)
        self.assertEqual([], hits)


@unittest.skipUnless(SOURCE_REPO, SOURCE_REPO_SKIP_REASON)
class PinnedSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert SOURCE_REPO is not None
        cls.upstream = {path: _git_show(SOURCE_REPO, path) for path in SOURCE_FILES}
        cls._tmp = tempfile.TemporaryDirectory()
        cls.patched = _patched_tree(SOURCE_REPO, Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_patch_applies_and_touches_one_file(self) -> None:
        changed = [
            path for path in SOURCE_FILES if self.upstream[path] != self.patched[path]
        ]
        self.assertEqual(["src/mm-shared-qmi.c"], changed)

    def test_selection_uses_card_status_primary_gw_slot(self) -> None:
        qmi = self.patched["src/mm-shared-qmi.c"]
        callback = _function_body(
            qmi, "load_sim_slots_get_card_status_ready (QmiClientUim *client,"
        )
        self.assertIn("qmi_client_uim_get_card_status_finish", callback)
        self.assertIn("index_gw_primary", callback)
        self.assertIn("((index_gw_primary & 0xFF00) >> 8) + 1", callback)
        self.assertIn("ctx->gw_primary_slot", callback)
        self.assertIn("qmi_client_uim_get_slot_status (ctx->client_uim", callback)

        load = _function_body(qmi, "mm_shared_qmi_load_sim_slots (MMIfaceModem        *self,")
        self.assertIn("qmi_client_uim_get_card_status (ctx->client_uim", load)
        self.assertIn("load_sim_slots_get_card_status_ready", load)
        self.assertNotIn("qmi_client_uim_get_slot_status (ctx->client_uim", load)

        slot_ready = _function_body(
            qmi, "uim_get_slot_status_ready (QmiClientUim *client,"
        )
        self.assertIn("select_primary_sim_slot (physical_slots->len,", slot_ready)
        self.assertIn("ctx->primary_slot_number", slot_ready)
        self.assertIn(
            "slot_sim_constructed[i] = (g_ptr_array_index (ctx->sim_slots, i) != NULL)",
            slot_ready,
        )
        self.assertNotIn("slot_has_sim", slot_ready)
        self.assertNotIn("ctx->active_logical_id", slot_ready)
        self.assertIn(
            "slot_active[i] = (slot_status->physical_slot_status == QMI_UIM_SLOT_STATE_ACTIVE)",
            slot_ready,
        )
        finish = _function_body(
            qmi, "mm_shared_qmi_load_sim_slots_finish (MMIfaceModem  *self,"
        )
        self.assertIn("*primary_sim_slot = ctx->primary_slot_number", finish)

        # The obsolete context field must be gone entirely; the switch-slot
        # helper's own local active_logical_id is a separate path and stays.
        struct_start = qmi.index("typedef struct {")
        struct_end = qmi.index("} LoadSimSlotsContext;", struct_start)
        load_ctx = qmi[struct_start:struct_end]
        self.assertNotIn("active_logical_id", load_ctx)
        self.assertIn("gw_primary_slot", load_ctx)
        self.assertIn("primary_slot_number", load_ctx)

        helper = _function_body(
            qmi, "select_primary_sim_slot (guint          n_slots,"
        )
        self.assertIn("slot_active[gw_primary_slot - 1]", helper)
        self.assertIn("slot_sim_constructed[gw_primary_slot - 1]", helper)

    def test_swap_detection_and_indicators_are_byte_identical(self) -> None:
        for path, signatures in (
            (
                "src/mm-broadband-modem.c",
                (
                    "complete_sim_swap_check (GTask       *task,",
                    "modem_check_for_sim_swap (MMIfaceModem        *self,",
                ),
            ),
            (
                "src/mm-shared-qmi.c",
                (
                    "uim_slot_status_indication_cb (QmiClientUim                     *client,",
                    "update_sim_from_slot_status (MMSharedQmi               *self,",
                ),
            ),
        ):
            for signature in signatures:
                self.assertEqual(
                    _function_body(self.upstream[path], signature),
                    _function_body(self.patched[path], signature),
                    f"{path}: {signature}",
                )
        self.assertNotIn(
            "mm_gdbus_sim_set_sim_identifier", self.patched["src/mm-broadband-modem.c"]
        )
        self.assertNotIn("mm_gdbus_sim_set_imsi", self.patched["src/mm-broadband-modem.c"])

    def test_card_status_callback_matches_existing_api_use(self) -> None:
        qmi = self.patched["src/mm-shared-qmi.c"]
        callback = _function_definition(
            qmi, "static void\nload_sim_slots_get_card_status_ready (QmiClientUim *client,"
        )
        # Same callback shape and libqmi calls as the existing in-tree use in
        # mm-broadband-modem-qmi.c (unlock_required_uim_get_card_status_ready).
        self.assertIn("QmiClientUim *client", callback)
        self.assertIn("GAsyncResult *res", callback)
        self.assertIn("GTask        *task", callback)
        self.assertIn("qmi_client_uim_get_card_status_finish (client, res, &error)", callback)
        self.assertIn("qmi_message_uim_get_card_status_output_get_result (output, &error)", callback)
        self.assertIn(
            "qmi_message_uim_get_card_status_output_get_card_status (", callback
        )
        self.assertEqual(1, callback.count("&index_gw_primary"))
        self.assertIn("&cards", callback)
        load = _function_body(qmi, "mm_shared_qmi_load_sim_slots (MMIfaceModem        *self,")
        # (client, input=NULL, timeout, cancellable, callback, user_data)
        self.assertIn("qmi_client_uim_get_card_status (ctx->client_uim,", load)
        self.assertIn("load_sim_slots_get_card_status_ready", load)

    def test_compiled_selection_helper_behaviour(self) -> None:
        compiler = shutil.which("cc")
        if not compiler:
            self.skipTest("no host C compiler")
        helper = _function_definition(
            self.patched["src/mm-shared-qmi.c"],
            "static guint\nselect_primary_sim_slot (guint          n_slots,",
        )
        harness = (
            "#include <stdio.h>\n"
            "typedef unsigned int guint;\n"
            "typedef int gboolean;\n"
            "\n" + helper + "\n"
            "\n"
            "static int failures;\n"
            "static void check(const char *name, guint n, const gboolean *active,\n"
            "                  const gboolean *sims, guint gw, guint expected)\n"
            "{\n"
            "    guint got = select_primary_sim_slot(n, active, sims, gw);\n"
            "    if (got != expected) {\n"
            "        printf(\"FAIL %s: got %u expected %u\\n\", name, got, expected);\n"
            "        failures++;\n"
            "    }\n"
            "}\n"
            "int main(void)\n"
            "{\n"
            "    const gboolean a1[] = {1, 0}, s1[] = {1, 0};\n"
            "    const gboolean a2[] = {0, 1}, s2[] = {0, 1};\n"
            "    const gboolean ab[] = {1, 1}, sb[] = {1, 1};\n"
            "    const gboolean only2[] = {0, 1}, only0[] = {0, 0};\n"
            "    const gboolean an[] = {0, 0};\n"
            "    check(\"single-1\", 2, a1, s1, 0, 1);\n"
            "    check(\"single-2\", 2, a2, s2, 0, 2);\n"
            "    check(\"dual-gw1\", 2, ab, sb, 1, 1);\n"
            "    check(\"dual-gw2\", 2, ab, sb, 2, 2);\n"
            "    check(\"dual-gw-unknown\", 2, ab, sb, 0, 2);\n"
            "    check(\"stale-gw-inactive\", 2, only2, sb, 1, 2);\n"
            "    check(\"gw-other-inactive\", 2, a1, s1, 2, 1);\n"
            "    check(\"gw-no-constructed-sim\", 2, ab, only2, 1, 2);\n"
            "    check(\"gw2-no-constructed-sim\", 2, ab, s1, 2, 2);\n"
            "    check(\"dual-gw-out-of-range\", 2, ab, sb, 3, 2);\n"
            "    check(\"none-active\", 2, an, sb, 0, 0);\n"
            "    check(\"active-no-constructed-sim\", 2, ab, only0, 0, 2);\n"
            "    check(\"no-slots\", 0, NULL, NULL, 0, 0);\n"
            "    return failures ? 1 : 0;\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as tmp_name:
            work = Path(tmp_name)
            source = work / "harness.c"
            binary = work / "harness"
            source.write_text(harness)
            subprocess.run(
                [
                    compiler,
                    "-std=c99",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(source),
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                [str(binary)], capture_output=True, text=True, check=False
            )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stdout)


if __name__ == "__main__":
    unittest.main()
