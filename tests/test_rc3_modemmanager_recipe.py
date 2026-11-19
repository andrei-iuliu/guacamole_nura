#!/usr/bin/env python3
"""Rootless checks for the RC3 self-contained ModemManager r1 recipe.

No build, install, chroot, network or phone action. The suite verifies:

* the recipe is a complete aports package directory and every ``sha512sums``
  entry equals the recomputed hash of the file in the directory;
* the upstream delta is the three reviewed lines plus removal of the
  circular self-library build prerequisite and declaration of the D-Bus test daemon;
* the source patch is byte-identical to the reviewed opt-in patch, applies to
  the pinned ModemManager commit, and the superseded identifier workaround is
  absent;
* the libqmi declarations used by the patch are regenerated from the pinned
  libqmi commit (when the local pinned checkout is available);
* the package metadata keeps the exact ``libmm-glib`` pin and the
  ``-systemd``/``-udev``/``-lang`` subpackages, and no owned file suggests an
  untrusted local install.

Source-application and libqmi checks skip with a message when the pinned local
checkouts are missing.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RECIPE = REPO_ROOT / "device" / "testing" / "rc3" / "modemmanager"
OPT_IN = REPO_ROOT / "device" / "testing" / "modemmanager"

MM_COMMIT = "7f0bf17ab518cd78925cf7e53cc0d5c42f88b41a"
MM_PKGVER = "1.25.95_git20261005"
MM_PKGREL = "1"
APKBUILD_UPSTREAM_SHA256 = (
    "c952de6a4a9a7351904a575ae2ce8f4a683e841b572bb588e46e5b1159610516"
)
TARBALL_NAME = f"ModemManager-{MM_COMMIT}.tar.gz"
TARBALL_SHA512 = (
    "e049eecc104c989f516402ca7c21e3374c152e3840cf05c34ac91bc8e3305c83"
    "b53693bc0964f657664d4156baaac1a4e0fa35dd577733623dd669b67f1675f6"
)
RULES_SHA512 = (
    "6fab86c27502b29be0c6610d835a3249a4a81993eb986cff2c3ea9393fadd3f6"
    "93ba4bb0532ae0e50a83d359559511859dd05f7ea2f0cb52839f535a7e49f8a0"
)
INITD_SHA512 = (
    "6e234cebcdec6d1afbca164ee5c13c96f7132ef31231d5c043cc5750ebe99e3"
    "62a384d0463874b3c1c30799b433e0d3286add03af13c97fa406b4b5d2b78880c"
)
PATCH1 = "0001-intel-XMM7360-fix-big-endian-handling-of-ASN-integer.patch"
PATCH1_SHA512 = (
    "f06e9b89acab5b61c5965658d62551b0f168066384674f7c4dd56feb6f811c8"
    "2c640470e634e32d8c4ef62290588cf974aa5f213e5c67e797a06d33e61fa24ec"
)
PATCH2 = "0002-primary-sim-slot-selection.patch"
PATCH2_SHA256 = "7f8e42293383a0a5db637a23df7c2b26d5bc69a2b482aa6c064cd675a98339f1"
PATCH2_SHA512 = (
    "d58a53043bf5973a83fbe204bbbc29ec6852b9c573b95420fc4c299d2cda4e3c"
    "dccb14b960dde32af126ca5ce29e2620844124356dad3e7b1a3f322e75cb9b8c"
)
SUPERSEDED = "0002-active-sim-identifier-from-ef2fe2.patch"
R0_APKS = (
    "modemmanager-1.25.95_git20261005-r0.ab345f17.apk",
    "libmm-glib-1.25.95_git20261005-r0.93b55423.apk",
    "modemmanager-systemd-1.25.95_git20261005-r0.50382707.apk",
    "modemmanager-udev-1.25.95_git20261005-r0.d3a91c50.apk",
    "modemmanager-lang-1.25.95_git20261005-r0.7ac8401c.apk",
)

DEFAULT_MM_REPO = Path(
    "bringup-workspace/sources/modemmanager-current"
)
DEFAULT_LIBQMI_REPO = Path(
    "bringup-workspace/sources/libqmi-current"
)
LIBQMI_COMMIT = "1c972b8ab901565f26b9f95e1705269277bbfa1b"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha512(path: Path) -> str:
    return hashlib.sha512(path.read_bytes()).hexdigest()


def _repo_at(env: str, default: Path, commit: str) -> tuple[Path | None, str]:
    candidates = []
    if os.environ.get(env):
        candidates.append(Path(os.environ[env]))
    candidates.append(default)
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
        if head == commit:
            return candidate, ""
        return None, f"checkout {candidate} is at {head}, expected {commit}"
    return None, f"no pinned checkout found; set {env}"


def _repo_with_commit(env: str, default: Path, commit: str) -> tuple[Path | None, str]:
    """Return a local repo that contains ``commit`` (which need not be HEAD)."""
    candidates = []
    if os.environ.get(env):
        candidates.append(Path(os.environ[env]))
    candidates.append(default)
    for candidate in candidates:
        if not (candidate / ".git").exists():
            continue
        result = subprocess.run(
            ["git", "-C", str(candidate), "cat-file", "-e", f"{commit}^{{commit}}"],
            capture_output=True,
        )
        if result.returncode == 0:
            return candidate, ""
        return None, f"checkout {candidate} does not contain {commit}"
    return None, f"no pinned checkout found; set {env}"


def _sums(text: str) -> dict[str, str]:
    section = text.split('sha512sums="', 1)[1].split('"', 1)[0]
    sums = {}
    for line in section.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.fullmatch(r"([0-9a-f]{128})\s{2}(.+)", line)
        assert match, f"unparsable sha512sums line: {line!r}"
        sums[match.group(2)] = match.group(1)
    return sums


def _sources(text: str) -> list[str]:
    match = re.search(r'^source="(.*?)"', text, re.M | re.S)
    assert match, "no source= section"
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


class RecipeLayoutTest(unittest.TestCase):
    def test_recipe_is_a_complete_aports_directory(self) -> None:
        self.assertTrue(RECIPE.is_dir())
        self.assertEqual(
            sorted(p.name for p in RECIPE.iterdir()),
            sorted(
                [
                    "APKBUILD",
                    "APKBUILD.upstream",
                    PATCH1,
                    PATCH2,
                    "modemmanager.rules",
                    "modemmanager.initd",
                    "README.md",
                ]
            ),
        )
        self.assertTrue((RECIPE / "APKBUILD").is_file())

    def test_no_binary_or_prebuilt_package_is_vendored(self) -> None:
        for path in RECIPE.iterdir():
            self.assertFalse(
                path.suffix in {".apk", ".img", ".bin", ".gz", ".xz", ".zip"},
                f"unexpected binary artifact in the recipe: {path.name}",
            )

    def test_recipe_has_no_install_hook_or_trigger(self) -> None:
        text = (RECIPE / "APKBUILD").read_text()
        self.assertNotRegex(text, r"(?m)^install=")
        self.assertNotRegex(text, r"(?m)^triggers=")


class RecipeChecksumTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = (RECIPE / "APKBUILD").read_text()

    def test_every_checksum_is_recomputed_from_the_recipe_files(self) -> None:
        sums = _sums(self.text)
        self.assertEqual(
            set(sums),
            {
                TARBALL_NAME,
                "modemmanager.rules",
                "modemmanager.initd",
                PATCH1,
                PATCH2,
            },
        )
        self.assertEqual(TARBALL_SHA512, sums[TARBALL_NAME])
        self.assertEqual(RULES_SHA512, sha512(RECIPE / "modemmanager.rules"))
        self.assertEqual(INITD_SHA512, sha512(RECIPE / "modemmanager.initd"))
        self.assertEqual(PATCH1_SHA512, sha512(RECIPE / PATCH1))
        self.assertEqual(PATCH2_SHA512, sha512(RECIPE / PATCH2))
        for name in ("modemmanager.rules", "modemmanager.initd", PATCH1, PATCH2):
            self.assertEqual(sha512(RECIPE / name), sums[name], name)

    def test_source_list_and_checksum_list_agree(self) -> None:
        sources = _sources(self.text)
        self.assertEqual(5, len(sources))
        self.assertEqual(
            "https://gitlab.freedesktop.org/mobile-broadband/ModemManager/"
            "-/archive/$_commit/ModemManager-$_commit.tar.gz",
            sources[0],
        )
        self.assertEqual(
            ["$pkgname.rules", "$pkgname.initd", PATCH1, PATCH2],
            sources[1:],
        )
        expanded = [
            name.replace("$pkgname", "modemmanager").replace("$_commit", MM_COMMIT)
            for name in sources
        ]
        expanded = [name.rsplit("/", 1)[-1] for name in expanded]
        self.assertEqual(
            [TARBALL_NAME, "modemmanager.rules", "modemmanager.initd", PATCH1, PATCH2],
            expanded,
        )
        self.assertEqual(set(_sums(self.text)), set(expanded))

    def test_reviewed_delta_and_bootstrap_fix_differ_from_upstream(self) -> None:
        upstream = (RECIPE / "APKBUILD.upstream").read_text()
        self.assertEqual(APKBUILD_UPSTREAM_SHA256, sha256(RECIPE / "APKBUILD.upstream"))

        normalised = self.text.replace(f"pkgrel={MM_PKGREL}\n", "pkgrel=0\n", 1)
        normalised = normalised.replace(f"\t{PATCH2}\n", "", 1)
        normalised = normalised.replace(
            f"{PATCH2_SHA512}  {PATCH2}\n", "", 1
        )
        normalised = normalised.replace('makedepends="\n', 'makedepends="\n\t$depends_dev\n', 1)
        normalised = normalised.replace('checkdepends="dbus ', 'checkdepends="', 1)
        self.assertEqual(upstream, normalised)

        self.assertEqual(1, self.text.count(f"\t{PATCH2}\n"))
        self.assertEqual(
            1, self.text.count(f"{PATCH2_SHA512}  {PATCH2}\n")
        )
        self.assertIn(f"pkgver={MM_PKGVER}\n", self.text)
        self.assertIn(f"_commit={MM_COMMIT}\n", self.text)
        self.assertEqual(1, self.text.count(f"pkgrel={MM_PKGREL}\n"))

    def test_upstream_snapshot_matches_the_opt_in_snapshot(self) -> None:
        self.assertEqual(
            (OPT_IN / "APKBUILD.upstream").read_bytes(),
            (RECIPE / "APKBUILD.upstream").read_bytes(),
        )

    def test_reviewed_patch_bytes_and_superseded_workaround(self) -> None:
        self.assertEqual(PATCH2_SHA256, sha256(RECIPE / PATCH2))
        self.assertEqual(
            (OPT_IN / PATCH2).read_bytes(), (RECIPE / PATCH2).read_bytes()
        )
        recipe_text = self.text + (RECIPE / "README.md").read_text()
        self.assertNotIn("active-sim-identifier", recipe_text)
        self.assertFalse((RECIPE / SUPERSEDED).exists())
        self.assertTrue((OPT_IN / "superseded" / SUPERSEDED).is_file())


class RecipeMetadataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = (RECIPE / "APKBUILD").read_text()

    def test_version_and_package_architecture(self) -> None:
        self.assertIn(f"pkgname=modemmanager\n", self.text)
        self.assertIn(f'arch="all"\n', self.text)
        self.assertIn('builddir="$srcdir"/ModemManager-$_commit\n', self.text)
        self.assertIn(
            '-Dsystemdsystemunitdir=/usr/lib/systemd/system', self.text
        )

    def test_build_does_not_require_its_own_unbuilt_library(self) -> None:
        result = subprocess.run(
            ["sh", "-c", '. "$1"; printf "%s\n" "$makedepends"', "sh", str(RECIPE / "APKBUILD")],
            capture_output=True, text=True, check=True,
        )
        self.assertFalse(any(dep.split("=")[0] == "libmm-glib" for dep in result.stdout.split()))

    def test_dbus_test_daemon_is_declared_and_suite_stays_enabled(self) -> None:
        result = subprocess.run(
            ["sh", "-c", '. "$1"; printf "%s\n" "$checkdepends"', "sh", str(RECIPE / "APKBUILD")],
            capture_output=True, text=True, check=True,
        )
        self.assertIn("dbus", result.stdout.split())
        self.assertIn("meson test -C output", self.text)

    def test_exact_libmm_glib_pin_and_subpackages(self) -> None:
        self.assertIn("libmm-glib=$pkgver-r$pkgrel", self.text)
        self.assertIn("depends_dev=\"libmm-glib=$pkgver-r$pkgrel\"", self.text)
        for subpackage in (
            "$pkgname-lang",
            "$pkgname-doc",
            "libmm-glib:libmm",
            "$pkgname-dbg",
            "$pkgname-dev",
            "$pkgname-openrc",
            "$pkgname-bash-completion",
            "$pkgname-systemd",
            "$pkgname-udev",
        ):
            self.assertIn(subpackage, self.text)
        self.assertIn("libqmi-dev", self.text)
        self.assertIn("libmbim-dev", self.text)

    def test_docs_state_the_required_delivery_constraints(self) -> None:
        docs = (REPO_ROOT / "docs" / "NURA_RC3_MODEM.md").read_text()
        readme = (RECIPE / "README.md").read_text()
        for text in (docs, readme):
            for line in text.splitlines():
                if "--allow-untrusted" in line:
                    self.assertRegex(
                        line.lower(),
                        r"(never|not|no\b)",
                        f"evidence of a permitted untrusted install: {line!r}",
                    )
        self.assertIn("pmos@local-6af20cae.rsa.pub", docs)
        self.assertIn(TARBALL_SHA512, docs)
        self.assertIn("aarch64", docs)
        self.assertIn("--arch aarch64", docs)
        self.assertIn("must not fetch", docs)
        self.assertIn("1.25.95_git20261005-r1", docs)
        self.assertIn("modemmanager", docs)
        self.assertIn("libmm-glib", docs)
        self.assertIn("modemmanager-systemd", docs)
        self.assertIn("modemmanager-udev", docs)
        self.assertIn("modemmanager-lang", docs)
        for apk in R0_APKS:
            self.assertIn(apk, docs)

    def test_owned_files_do_not_modify_shared_package(self) -> None:
        device_apkbuild = (
            REPO_ROOT
            / "device"
            / "testing"
            / "device-oneplus-guacamole"
            / "APKBUILD"
        ).read_text()
        self.assertNotIn("rc3/modemmanager", device_apkbuild)
        self.assertNotIn(PATCH2, device_apkbuild)


class PinnedSourceApplicationTest(unittest.TestCase):
    def test_patch_applies_to_the_pinned_modemmanager(self) -> None:
        repo, reason = _repo_at(
            "MODEM_MANAGER_SOURCE_REPO", DEFAULT_MM_REPO, MM_COMMIT
        )
        if repo is None:
            self.skipTest(reason)
        with tempfile.TemporaryDirectory() as tmp_name:
            work = Path(tmp_name)
            target = work / "src" / "mm-shared-qmi.c"
            target.parent.mkdir(parents=True)
            target.write_bytes(
                subprocess.run(
                    ["git", "-C", str(repo), "show", "HEAD:src/mm-shared-qmi.c"],
                    capture_output=True,
                    check=True,
                ).stdout
            )
            archive = RECIPE / PATCH2
            check = subprocess.run(
                ["git", "apply", "--check", "-p1", str(archive)],
                cwd=work,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, check.returncode, check.stderr)
            subprocess.run(
                ["git", "apply", "-p1", str(archive)],
                cwd=work,
                check=True,
                capture_output=True,
            )
            patched = target.read_text()
            self.assertIn(
                "select_primary_sim_slot (physical_slots->len,", patched
            )
            self.assertIn("ctx->primary_slot_number", patched)
            self.assertIn(
                "qmi_client_uim_get_card_status (ctx->client_uim,", patched
            )
            self.assertIn(
                "((index_gw_primary & 0xFF00) >> 8) + 1", patched
            )
            self.assertNotIn("ctx->active_logical_id", patched)
            # git apply --numstat prints one line per touched file.
            numstat = subprocess.run(
                ["git", "apply", "--numstat", "-p1", str(archive)],
                cwd=work,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()
            self.assertEqual(1, len(numstat))
            self.assertTrue(numstat[0].endswith("src/mm-shared-qmi.c"), numstat)


class PinnedLibqmiApiTest(unittest.TestCase):
    DECLARATIONS = (
        "void qmi_client_uim_get_card_status (QmiClientUim *self, gpointer unused, "
        "guint timeout, GCancellable *cancellable, GAsyncReadyCallback callback, "
        "gpointer user_data);",
        "QmiMessageUimGetCardStatusOutput *qmi_client_uim_get_card_status_finish ("
        "QmiClientUim *self, GAsyncResult *res, GError **error);",
        "gboolean qmi_message_uim_get_card_status_output_get_card_status ("
        "QmiMessageUimGetCardStatusOutput *self, guint16 *value_card_status_index_gw_primary, "
        "guint16 *value_card_status_index_1x_primary, guint16 *value_card_status_index_gw_secondary, "
        "guint16 *value_card_status_index_1x_secondary, GArray **value_card_status_cards, "
        "GError **error);",
    )

    def test_generated_uim_declarations_match_the_patch_calls(self) -> None:
        repo, reason = _repo_with_commit(
            "MODEM_LIBQMI_SOURCE_REPO", DEFAULT_LIBQMI_REPO, LIBQMI_COMMIT
        )
        if repo is None:
            self.skipTest(reason)
        if not shutil.which("python3"):
            self.skipTest("no python3 for qmi-codegen")
        with tempfile.TemporaryDirectory() as tmp_name:
            work = Path(tmp_name)
            archive = work / "libqmi.tar"
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "archive",
                    "--format=tar",
                    "-o",
                    str(archive),
                    LIBQMI_COMMIT,
                ],
                check=True,
                capture_output=True,
            )
            source = work / "libqmi"
            source.mkdir()
            with tarfile.open(archive) as tar:
                tar.extractall(source, filter="data")
            output = work / "gen" / "qmi-uim"
            output.parent.mkdir()
            subprocess.run(
                [
                    sys.executable,
                    str(source / "build-aux" / "qmi-codegen" / "qmi-codegen"),
                    "--input",
                    str(source / "data" / "qmi-service-uim.json"),
                    "--include",
                    str(source / "data" / "qmi-common.json"),
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            header = (output.parent / "qmi-uim.h").read_text()
            flat = re.sub(r"\s+", " ", header).replace("( ", "(").replace(" )", ")")
            for declaration in self.DECLARATIONS:
                self.assertIn(declaration, flat)

            patch = (RECIPE / PATCH2).read_text()
            self.assertIn("qmi_client_uim_get_card_status_finish (client, res, &error)", patch)
            self.assertIn("&index_gw_primary", patch)


class DeliveryReadmeTest(unittest.TestCase):
    def test_readme_states_the_offline_prerequisite_and_no_build_claim(self) -> None:
        readme = (RECIPE / "README.md").read_text()
        self.assertIn(TARBALL_NAME, readme)
        self.assertIn(TARBALL_SHA512, readme)
        self.assertIn("not built, not installed", readme)
        self.assertIn("cache_distfiles", readme)
        for apk in R0_APKS:
            self.assertIn(apk, readme)


if __name__ == "__main__":
    unittest.main()
