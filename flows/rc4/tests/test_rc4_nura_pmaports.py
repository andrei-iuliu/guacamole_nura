#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Rootless tests for --pmaports-ref (Nura pmaports), the local recipes and the
Plasma opt-in.

Nothing here clones Nura's pmaports, runs pmbootstrap, sudo, a mount or a phone
command.  The overlay tests use two throw-away git repositories.

  * unset --pmaports-ref keeps today's paths, content and receipt fields;
  * --pmaports-ref gets the same paths with the -nura suffix (plasma:
    .work/local-userdata-nura, build/local-userdata-nura; phosh keeps -phosh);
  * a ref that is not a plain branch/tag/sha is refused before anything runs;
  * the candidate receipt records the Nura pmaports URL, ref and sha next to
    the pinned identity;
  * `nura-overlay` reproduces the baseline's local overlay (deletions and
    copies) on a fresh checkout, clears the local-recipe paths, and refuses a
    drifted or half-applied tree;
  * every Plasma build carries the one local plasma-mobile recipe (the upstream
    quick-settings fix) and verifies the image installs 6.8.3-r101; Phosh never
    builds or installs it;
  * NURA_PLASMA_NO_XWAYLAND stays the only Plasma opt-in, off by default, so
    the package set and exclusions are today's.
The build itself is user-run; these tests never clone Nura's pmaports.
"""

import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
RC4 = TESTS.parent
REPO = RC4.parents[1]
FLOW = RC4 / "local-storage"
SCRIPTS = FLOW / "scripts"
ENTRY = SCRIPTS / "local_storage_build.sh"
PREPARE = SCRIPTS / "rootfs_prepare_local.sh"
CHECKER = SCRIPTS / "check_local_candidate.py"
DEVICE = RC4 / "device/testing"
LINK_FARM = FLOW / "device/testing"
DEBUG_IMAGE = SCRIPTS / "local_storage_debug_bootimg.sh"
INSTALLER = SCRIPTS / "install_userdata.py"
NURA = REPO / "nura"
DOCS = REPO / "docs/BUILD_ENVIRONMENT.md"
NURA_URL = "https://gitlab.nura.eco/nura/pmaports.git"

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
sys.dont_write_bytecode = True


def run(cmd, env=None, **kw):
    # HOME keeps ./nura's build-env.local.sh (ignored, machine specific) happy.
    full = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": os.environ.get("HOME", "/tmp")}
    full.update(env or {})
    return subprocess.run(cmd, capture_output=True, text=True, env=full, **kw)


def git(*args, cwd=None):
    """git with a throw-away identity, so no test touches the real config."""
    return run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                *args], cwd=str(cwd) if cwd else None)


def load_checker(ui="plasma"):
    old = os.environ.get("NURA_UI")
    os.environ["NURA_UI"] = ui
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(f"clc_nura_{ui}", CHECKER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))
        if old is None:
            os.environ.pop("NURA_UI", None)
        else:
            os.environ["NURA_UI"] = old


def kernel_fixture(root: pathlib.Path) -> dict:
    """The minimum a dry run reads: the rc4 kernel release in build and state."""
    build = root / "kernel-output"
    state = root / "kernel-state"
    release = "7.0.0-sm8150-guacamole-rc4"
    (build / "kernel-package/usr/share/kernel/postmarketos-qcom-sm8150").mkdir(parents=True)
    (state / "output/include/config").mkdir(parents=True)
    (build / "kernel-inputs.json").write_text("{}\n")
    (build / "kernel-package/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release").write_text(
        release + "\n")
    (state / "output/include/config/kernel.release").write_text(release + "\n")
    source = root / "src"
    source.mkdir()
    return {
        "NURA_SOURCE_ROOT": str(source),
        "NURA_KERNEL_BUILD": str(build),
        "NURA_KERNEL_SOURCE_STATE": str(state),
    }


def plan(root: pathlib.Path, args=(), env=None) -> subprocess.CompletedProcess:
    full = kernel_fixture(root)
    full.update(env or {})
    return run(["bash", str(ENTRY), *args], env=full)


def json_loads(text: str) -> dict:
    """The script prints its human note first, then the JSON summary."""
    return json.loads(text[text.index("{"):])


def remove_tree(path: pathlib.Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def shell_function(text: str, name: str) -> str:
    body = text.split(f"\n{name}() {{", 1)[1]
    return body.split("\n}\n", 1)[0]


def ui_build_branches(prepare: str) -> tuple[str, str]:
    """The phosh and plasma branches of run_pmbootstrap_builds()."""
    body = prepare.split('local -a ui_builds=("$UI_PKG")', 1)[1]
    phosh_branch, rest = body.split("\n    else\n", 1)
    return phosh_branch, rest.split("\n    fi\n", 1)[0]


class TestBuildPlanSuffix(unittest.TestCase):
    """Unset keeps today's paths; --pmaports-ref gets its own -nura pair."""

    def test_unset_keeps_the_pinned_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = plan(pathlib.Path(tmp), ["--ui", "plasma"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"isolated state:     {FLOW}/.work/local-userdata\n", result.stdout)
            self.assertIn(f"isolated output:    {FLOW}/build/local-userdata\n", result.stdout)
            self.assertNotIn("-nura", result.stdout)
            self.assertIn("pmaports source:    pinned baseline copy", result.stdout)
            # The only local Plasma recipe is named, and no opt-in is on.
            self.assertIn("local Plasma recipe: plasma-mobile 6.8.3-r101", result.stdout)
            self.assertIn("Plasma exclusions:  none (default", result.stdout)

    def test_ref_moves_state_and_output_to_nura(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = plan(pathlib.Path(tmp), ["--ui", "plasma", "--pmaports-ref", "main"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"isolated state:     {FLOW}/.work/local-userdata-nura\n", result.stdout)
            self.assertIn(f"isolated output:    {FLOW}/build/local-userdata-nura\n", result.stdout)
            self.assertIn("pmaports source:    Nura pmaports main", result.stdout)

    def test_environment_variable_is_equivalent(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = plan(pathlib.Path(tmp), ["--ui", "plasma"],
                          {"NURA_PMAPORTS_REF": "main"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("local-userdata-nura\n", result.stdout)
            self.assertIn("Nura pmaports main", result.stdout)

    def test_phosh_keeps_its_own_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = plan(pathlib.Path(tmp), ["--ui", "phosh", "--pmaports-ref", "main"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"{FLOW}/.work/local-userdata-phosh-nura\n", result.stdout)
            self.assertIn(f"{FLOW}/build/local-userdata-phosh-nura\n", result.stdout)

    def test_an_odd_ref_is_refused(self):
        for ref in ("main; rm -rf /", "--upload-pack=x", "a b", "main\\x00"):
            with self.subTest(ref=ref), tempfile.TemporaryDirectory() as tmp:
                result = plan(pathlib.Path(tmp), ["--ui", "plasma", "--pmaports-ref", ref])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("not a plain ref", result.stderr)

    def test_entry_points_share_the_suffix_rule(self):
        """The build, the rootfs script, the debug image and the installer."""
        self.assertIn('UI_SUFFIX="$UI_SUFFIX-nura"', ENTRY.read_text())
        self.assertIn('UI_SUFFIX="$UI_SUFFIX-nura"', PREPARE.read_text())
        self.assertIn('SUFFIX="$SUFFIX-nura"', DEBUG_IMAGE.read_text())
        self.assertIn('ui_suffix += "-nura"', INSTALLER.read_text())

    def test_usage_and_docs_mention_the_option(self):
        usage = run([str(NURA), "help"])
        self.assertEqual(usage.returncode, 0, usage.stderr)
        self.assertIn("--pmaports-ref REF", usage.stdout)
        self.assertIn("NURA_PMAPORTS_REF", usage.stdout)
        docs = DOCS.read_text()
        self.assertIn("./nura --ui plasma build --pmaports-ref main", docs)
        self.assertIn("NURA_PMAPORTS_REF", docs)


class TestReceiptFields(unittest.TestCase):
    """The receipt records the Nura URL, ref and resolved sha."""

    def make_repo(self, root: pathlib.Path) -> pathlib.Path:
        repo = root / "pmaports"
        repo.mkdir()
        self.assertEqual(git("init", "-q", "-b", "main", cwd=repo).returncode, 0)
        (repo / "pmaports.cfg").write_text("[pmaports]\nversion=7\nchannel=edge\n")
        self.assertEqual(git("add", "-A", cwd=repo).returncode, 0)
        self.assertEqual(git("commit", "-qm", "base", cwd=repo).returncode, 0)
        self.assertEqual(git("remote", "add", "origin", NURA_URL, cwd=repo).returncode, 0)
        return repo

    def test_unset_adds_no_fields(self):
        clc = load_checker("plasma")
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(pathlib.Path(tmp))
            self.assertEqual(clc.pmaports_source_fields(repo, None, None), {})
            self.assertEqual(clc.pmaports_source_fields(repo, "", ""), {})

    def test_set_records_url_ref_and_sha(self):
        clc = load_checker("plasma")
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(pathlib.Path(tmp))
            head = git("rev-parse", "HEAD", cwd=repo).stdout.strip()
            fields = clc.pmaports_source_fields(repo, NURA_URL, "main")
            self.assertEqual(fields, {
                "source_pmaports_url": NURA_URL,
                "source_pmaports_ref": "main",
                "source_pmaports_sha": head,
            })

    def test_bad_input_is_refused(self):
        clc = load_checker("plasma")
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = self.make_repo(root)
            for url, ref in ((NURA_URL, ""), ("", "main"),
                             ("https://example.com/pmaports.git", "main"),
                             (NURA_URL, "not a ref")):
                with self.subTest(url=url, ref=ref), self.assertRaises(SystemExit):
                    clc.pmaports_source_fields(repo, url, ref)
            plain = root / "plain"
            plain.mkdir()
            with self.assertRaises(SystemExit):
                clc.pmaports_source_fields(plain, NURA_URL, "main")

    def test_prepare_takes_the_flags(self):
        text = PREPARE.read_text()
        self.assertIn("--source-pmaports-url", text)
        self.assertIn("--source-pmaports-ref", text)
        self.assertIn('--source-pmaports "$PMAPORTS_SRC"', text)
        checker = CHECKER.read_text()
        self.assertIn('"source_pmaports_url"', checker)
        self.assertIn('"source_pmaports_sha"', checker)
        # The Nura remote is an official upstream for pmbootstrap's origin/main.
        self.assertIn(NURA_URL, checker)


class TestOverlay(unittest.TestCase):
    """`nura-overlay` reproduces the baseline's local overlay on a checkout."""

    SHADOWED = (
        "device/testing/device-oneplus-guacamole",
        "main/postmarketos-mkinitfs-hook-netboot",
        "temp/libcamera-rear",
    )
    # No longer shadowed: the systemd plasma-workspace fork was replaced by a
    # local recipe while the flow still had the Qt-private-API rebuilds.  It
    # stays an upstream recipe now, so a clone made then has to be synced.
    FORMER_SHADOW = "extra-repos/systemd/plasma-workspace"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.seed = self.root / "seed"
        self.seed.mkdir()
        git("init", "-q", "-b", "main", cwd=self.seed)
        (self.seed / "pmaports.cfg").write_text("[pmaports]\nversion=7\nchannel=edge\n")
        for relative in (*self.SHADOWED, self.FORMER_SHADOW, "main/kept"):
            (self.seed / relative).mkdir(parents=True)
            (self.seed / relative / "APKBUILD").write_text(f"pkgname={relative}\n")
        (self.seed / "main/patched").mkdir(parents=True)
        (self.seed / "main/patched/APKBUILD").write_text("pkgname=patched\n")
        git("add", "-A", cwd=self.seed)
        git("commit", "-qm", "upstream", cwd=self.seed)
        git("remote", "add", "origin", NURA_URL, cwd=self.seed)
        # The baseline: a clone at the same commit plus its local overlay
        # (the deletions and the patched recipe the flow reads as "overlays").
        self.baseline = self.root / "baseline"
        run(["git", "clone", "-q", str(self.seed), str(self.baseline)])
        for relative in self.SHADOWED:
            remove_tree(self.baseline / relative)
        (self.baseline / "main/patched/APKBUILD").write_text("pkgname=patched\npkgrel=100\n")
        # The fresh checkout: pristine clone at the same commit.
        self.checkout = self.root / "checkout"
        run(["git", "clone", "-q", str(self.seed), str(self.checkout)])

    def tearDown(self):
        self.tmp.cleanup()

    def overlay(self, action, ui="plasma", baseline=None, checkout=None, env=None):
        full = {"NURA_UI": ui}
        full.update(env or {})
        return run([sys.executable, str(CHECKER), "nura-overlay", action,
                    "--baseline", str(baseline or self.baseline),
                    "--checkout", str(checkout or self.checkout)], full)

    def test_apply_reproduces_the_baseline_overlay(self):
        result = self.overlay("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        for relative in self.SHADOWED:
            self.assertFalse((self.checkout / relative).exists(), relative)
        self.assertEqual((self.checkout / "main/patched/APKBUILD").read_text(),
                         (self.baseline / "main/patched/APKBUILD").read_text())
        # Real recipes outside the overlay stay untouched.
        self.assertTrue((self.checkout / "main/kept/APKBUILD").is_file())
        summary = json_loads(result.stdout)
        self.assertEqual(summary["status"], "overlaid")
        self.assertIn("temp/libcamera-rear", summary["shadowed_paths"])

    def test_verify_accepts_the_overlaid_tree_only(self):
        self.assertNotEqual(self.overlay("--verify").returncode, 0,
                            "a pristine checkout is not overlaid yet")
        self.assertEqual(self.overlay("--apply").returncode, 0)
        result = self.overlay("--verify")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json_loads(result.stdout)["status"], "verified")
        # Apply again: the overlay is not run on top of itself.
        self.assertIn("not pristine", self.overlay("--apply").stderr)

    def test_sync_restores_a_recipe_an_older_set_still_shadowed(self):
        """The clone is reused, so --sync re-applies the overlay in place."""
        self.assertEqual(self.overlay("--apply").returncode, 0)
        # An earlier flow deleted the path it shadowed with a local recipe
        # (the Qt-private-API rebuild of plasma-workspace).  The recipe set no
        # longer shadows it, so verify says so and --sync restores the upstream
        # fork from the commit instead of demanding a fresh clone.
        remove_tree(self.checkout / self.FORMER_SHADOW)
        self.assertNotEqual(self.overlay("--verify").returncode, 0)
        result = self.overlay("--sync")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json_loads(result.stdout)["status"], "synced")
        self.assertEqual((self.checkout / self.FORMER_SHADOW / "APKBUILD").read_text(),
                         (self.seed / self.FORMER_SHADOW / "APKBUILD").read_text())
        self.assertEqual(self.overlay("--verify").returncode, 0)
        # --sync is not a one-way door: the same checkout stays verifiable.
        self.assertEqual(self.overlay("--sync").returncode, 0)

    def test_sync_still_refuses_a_hand_edit(self):
        self.assertEqual(self.overlay("--apply").returncode, 0)
        (self.checkout / "main/extra").mkdir(parents=True, exist_ok=True)
        (self.checkout / "main/extra/APKBUILD").write_text("pkgname=extra\n")
        result = self.overlay("--sync")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("clone it again", result.stderr)

    def test_a_hand_edit_is_refused(self):
        self.assertEqual(self.overlay("--apply").returncode, 0)
        (self.checkout / "main").mkdir(parents=True, exist_ok=True)
        (self.checkout / "main/extra").mkdir(exist_ok=True)
        (self.checkout / "main/extra/APKBUILD").write_text("pkgname=extra\n")
        result = self.overlay("--verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("drifted", result.stderr)

    def test_a_baseline_that_gained_a_change_is_refused(self):
        self.assertEqual(self.overlay("--apply").returncode, 0)
        (self.baseline / "main/kept/APKBUILD").write_text("pkgname=kept\npkgrel=100\n")
        result = self.overlay("--verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("drifted", result.stderr)

    def test_the_overlay_is_not_the_baseline(self):
        result = self.overlay("--verify", checkout=self.baseline)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not be the read-only baseline", result.stderr)


class TestPackageVersion(unittest.TestCase):
    """The version check must see a package built into another channel.

    A recipe under pmaports extra-repos/<repo>/ (the systemd plasma-workspace
    fork is one) builds into packages/<repo>-edge/aarch64, not
    packages/edge/aarch64.  package_version() searched only the latter, so a
    build once died with "expected a built plasma-workspace APK ... found none"
    long after the package had built, with the APK sitting in systemd-edge.
    """

    def function(self) -> str:
        text = PREPARE.read_text()
        start = text.index("package_version() {")
        end = text.index("\n}\n", start) + len("\n}\n")
        return text[start:end]

    def runner(self, root: pathlib.Path) -> pathlib.Path:
        work = root / "work"
        edge = work / "packages/edge/aarch64"
        edge.mkdir(parents=True, exist_ok=True)
        (work / "packages/systemd-edge/aarch64").mkdir(parents=True, exist_ok=True)
        script = (root / "package_version.sh")
        script.write_text(
            "set -eu\n"
            f'WORK="{work}"\nCHANNEL=edge\nPKGDIR="{edge}"\n'
            'die() { echo "ERROR: $*" >&2; exit 1; }\n'
            + self.function()
            + '\npackage_version "$1"\n'
        )
        return script

    def test_it_finds_a_package_built_into_the_systemd_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            script = self.runner(root)
            work = root / "work/packages"
            (work / "edge/aarch64/libplasma-6.8.3-r100.apk").write_bytes(b"")
            (work / "systemd-edge/aarch64/plasma-workspace-99996.8.3-r100.apk"
             ).write_bytes(b"")
            for name, expected in (("libplasma", "6.8.3-r100"),
                                   ("plasma-workspace", "99996.8.3-r100")):
                with self.subTest(package=name):
                    result = run(["bash", str(script), name])
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), expected)
            # A stale build in the same channel: apk installs the highest.
            (work / "systemd-edge/aarch64/plasma-workspace-99996.8.3-r99.apk"
             ).write_bytes(b"")
            result = run(["bash", str(script), "plasma-workspace"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "99996.8.3-r100")

    def test_a_missing_package_names_every_searched_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            script = self.runner(root)
            result = run(["bash", str(script), "kscreen"])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("expected a built kscreen APK", result.stderr)
            self.assertIn("packages/edge/aarch64", result.stderr)
            self.assertIn("packages/systemd-edge/aarch64", result.stderr)


class TestSudoTimer(unittest.TestCase):
    """One typed password must cover a whole build (libplasma alone: 38m46s)."""

    def test_the_candidate_config_asks_pmbootstrap_for_its_sudo_timer(self):
        clc = load_checker("plasma")
        self.assertEqual(clc.SUDO_TIMER, "true")
        text = CHECKER.read_text()
        self.assertIn('"sudo_timer": SUDO_TIMER,', text)
        # It sits in the shared config dict, so every UI gets it.
        self.assertLess(text.index('"sudo_timer": SUDO_TIMER,'),
                        text.index('config["providers"] = {}'))
        self.assertLess(text.index("SUDO_TIMER = \"true\""), text.index("UI_PROFILES = {"))

    def test_the_authorized_key_config_keeps_it(self):
        # The throw-away install config only rewrites ssh_keys/ssh_key_glob, so
        # sudo_timer survives into "pmbootstrap install" as well.
        text = CHECKER.read_text()
        self.assertIn('if key in ("ssh_keys", "ssh_key_glob"):', text)
        self.assertNotIn('"sudo_timer"', text[text.index("def authorized_key_config"):
                                              text.index("def verify_installed")])

    def test_the_flow_wires_the_inert_pmbootstrap_option(self):
        # The pin's Context.__init__ only stores log+config, so the config
        # value alone never reaches run_core; prepare_state patches it the same
        # way it patches --ignore-depends.
        text = CHECKER.read_text()
        self.assertIn('timer_assignment = "    context.sudo_timer = config.sudo_timer\\n"', text)
        self.assertIn('anchor = "    context = Context(config)\\n"', text)
        self.assertIn('die("pmbootstrap context creation changed; cannot safely wire sudo_timer")', text)

    def test_the_pinned_pmbootstrap_supports_it(self):
        module = SCRIPTS.parent / ".work/local-userdata-nura/rootfs-state/pmbootstrap"
        if not (module / "pmb/core/config.py").is_file():
            self.skipTest("no pinned pmbootstrap checkout here")
        config = (module / "pmb/core/config.py").read_text()
        self.assertIn("sudo_timer: bool = False", config)
        run_core = (module / "pmb/helpers/run_core.py").read_text()
        self.assertIn("context.sudo_timer and sudo", run_core)


class TestRecipeSetSwitch(unittest.TestCase):
    """A changed local recipe set reconciles an existing candidate copy.

    The candidate pmaports copy is made once and reused.  The receipt records
    the local-recipe links it carries and the Plasma opt-in it was made for, so
    a link the flow added or dropped since (plasma-mobile is now always linked,
    the 20 Qt-private-API reconstructions are gone) is reconciled in place
    instead of needing a hand-deleted copy.
    """

    def make_trees(self, root: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
        source = root / "source"
        candidate = root / "candidate"
        (source / "extra-repos/systemd/plasma-workspace").mkdir(parents=True)
        (source / "extra-repos/systemd/plasma-workspace/APKBUILD").write_text(
            "pkgname=plasma-workspace\npkgver=99996.8.3\n")
        (source / "main/kept").mkdir(parents=True)
        (source / "main/kept/APKBUILD").write_text("pkgname=kept\n")
        for relative in ("extra-repos/systemd/plasma-workspace", "main/kept"):
            (candidate / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source / "extra-repos/systemd/plasma-workspace",
                        candidate / "extra-repos/systemd/plasma-workspace")
        shutil.copytree(source / "main/kept", candidate / "main/kept")
        return source, candidate

    def test_a_new_local_recipe_clears_the_path_it_now_shadows(self):
        clc = load_checker("plasma")
        with tempfile.TemporaryDirectory() as tmp:
            source, candidate = self.make_trees(pathlib.Path(tmp))
            shadow = "extra-repos/systemd/plasma-workspace"
            restored = clc.refresh_local_recipe_paths(candidate, source, set(), {shadow: "unused"})
            self.assertEqual(restored, [])
            self.assertFalse((candidate / shadow).exists(),
                             "the upstream fork must be cleared for the local symlink")

    def test_a_dropped_local_recipe_restores_the_upstream_one(self):
        clc = load_checker("plasma")
        with tempfile.TemporaryDirectory() as tmp:
            source, candidate = self.make_trees(pathlib.Path(tmp))
            shadow = "extra-repos/systemd/plasma-workspace"
            remove = candidate / shadow
            shutil.rmtree(remove)
            remove.symlink_to("/tmp/does-not-exist")
            restored = clc.refresh_local_recipe_paths(candidate, source, {shadow: "unused"}, {})
            self.assertEqual(restored, [shadow])
            self.assertFalse((candidate / shadow).is_symlink())
            self.assertEqual((candidate / shadow / "APKBUILD").read_text(),
                             (source / shadow / "APKBUILD").read_text())

    def test_paths_outside_the_switch_are_left_alone(self):
        clc = load_checker("plasma")
        with tempfile.TemporaryDirectory() as tmp:
            source, candidate = self.make_trees(pathlib.Path(tmp))
            marker = candidate / "main/kept/APKBUILD"
            before = marker.read_text()
            clc.refresh_local_recipe_paths(candidate, source, {"main/gone"}, {"main/other": "unused"})
            self.assertEqual(marker.read_text(), before)

    def test_the_receipt_records_the_recipe_set_and_the_opt_in(self):
        text = CHECKER.read_text()
        self.assertIn('"local_links": resolved_links', text)
        self.assertIn('"plasma_no_xwayland": PLASMA_NO_XWAYLAND', text)
        self.assertNotIn("qt_rebuilds", text)
        self.assertIn("refresh_local_recipe_paths", text)
        # A recipe added or dropped since the copy was made triggers the same
        # reconciliation as an opt-in switch.
        self.assertIn("stale_links != set(links)", text)
        prepare = shell_function(PREPARE.read_text(), "select_pmaports_source")
        self.assertIn("nura-overlay --sync", prepare,
                      "a reused checkout must be re-overlaid for the current recipe set")


class TestPlasmaMobileRecipe(unittest.TestCase):
    """The one local Plasma recipe: upstream's quick-settings fix.

    The 20 other Qt-private-API reconstructions are gone (rebuilding them did
    not fix the empty quick-settings drawer), so plasma-mobile is the only
    local Plasma package, and every Plasma image builds it.
    """

    RECIPE = DEVICE / "plasma-mobile"
    PATCH = "0001-quicksettings-never-use-a-negative-page-size.patch"
    COMMIT = "7d58ddd8043dbcd48ce1a1b3663e6756daf99ce2"
    REMOVED = (
        "aurorae", "bluedevil", "kde-cli-tools", "kirigami-addons", "kscreen",
        "kwayland", "libksysguard", "libplasma", "milou", "plasma-keyboard",
        "plasma-nano", "plasma-nm", "plasma-pa", "plasma-phonebook",
        "plasma-workspace", "powerdevil", "qcoro", "qqc2-breeze-style",
        "union", "xdg-desktop-portal-kde",
    )

    def test_the_header_explains_the_fix_and_when_to_drop_it(self):
        text = (self.RECIPE / "APKBUILD").read_text()
        header = text.split("maintainer=", 1)[0]
        # Why the recipe exists: the upstream fix, the symptom, the trigger.
        for phrase in ("upstream quick-settings fix", "drawer empty",
                       "QuickSettings.qml", "Qt 6.12.0", "negative",
                       "master only", self.COMMIT, "not in Plasma/6.8"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, header)
        # Drop condition, and the two facts a maintainer needs next.
        self.assertIn("once the packaged plasma-mobile contains", header)
        self.assertIn("NURA_UI=plasma", header)
        self.assertIn("Phosh never", header)
        # The disproved Qt-private-ABI rebuild story is gone.
        for dropped in ("Qt_6_PRIVATE_API", "515491", "rebuilt against",
                        "eb459c5", "plasma-mobile-initial-start"):
            with self.subTest(dropped=dropped):
                self.assertNotIn(dropped, text)

    def test_the_recipe_builds_the_repository_package_plus_one_patch(self):
        text = (self.RECIPE / "APKBUILD").read_text()
        self.assertIn("\npkgname=plasma-mobile\n", text)
        self.assertIn("\npkgver=6.8.3\n", text)
        # 101: above the repository's 6.8.3-r0, and what the phone runs.
        self.assertIn("\npkgrel=101\n", text)
        # The only difference from the upstream recipe is that patch.
        self.assertIn(f"\t{self.PATCH}\n", text)
        source = re.search(r'^source="(.*?)"$', text, re.M | re.S)
        self.assertIsNotNone(source)
        self.assertEqual(sorted(p.name for p in self.RECIPE.iterdir()
                                if p.name != "APKBUILD"),
                         sorted(set(re.findall(r"^\t(\S+)$", source.group(1), re.M))))

    def test_the_patch_is_the_upstream_commit(self):
        body = (self.RECIPE / self.PATCH).read_text()
        self.assertTrue(body.startswith(f"From {self.COMMIT} "))
        self.assertIn("quicksettings: Never use a negative page size", body)
        self.assertIn("components/mobileshell/qml/actiondrawer/private/QuickSettings.qml", body)
        self.assertIn("components/quicksettingsplugin/paginatemodel.cpp", body)
        self.assertIn("count = qMax(0, count);", body)

    def test_sha512sums_match_the_local_files(self):
        text = (self.RECIPE / "APKBUILD").read_text()
        sums = dict((name, sum_) for sum_, name in re.findall(
            r"^([0-9a-f]{128})  (\S+)$", text, re.M))
        for name in (p.name for p in self.RECIPE.iterdir() if p.name != "APKBUILD"):
            with self.subTest(file=name):
                self.assertIn(name, sums, f"no sha512sum entry for {name}")
                self.assertEqual(sums[name],
                                 hashlib.sha512((self.RECIPE / name).read_bytes()).hexdigest())

    def test_link_farm_exposes_the_recipe(self):
        farm = LINK_FARM / "plasma-mobile"
        self.assertTrue(farm.is_symlink(), farm)
        self.assertEqual(farm.resolve(), self.RECIPE.resolve())

    def test_it_is_a_local_recipe_of_every_plasma_build_only(self):
        plasma = load_checker("plasma")
        phosh = load_checker("phosh")
        self.assertEqual(plasma.local_recipe_links()["main/plasma-mobile"],
                         "device/testing/plasma-mobile")
        self.assertNotIn("main/plasma-mobile", phosh.local_recipe_links())
        # ...and the build list follows the same rule.
        phosh_branch, plasma_branch = ui_build_branches(PREPARE.read_text())
        self.assertIn('ui_builds=(plasma-mobile "$UI_PKG")', plasma_branch)
        self.assertIn("ui_builds+=(gmobile phoc)", phosh_branch)
        self.assertNotIn("plasma-mobile", phosh_branch)

    def test_the_flow_verifies_the_r101_build(self):
        prepare = PREPARE.read_text()
        # One reviewed version, checked after the build and before the image.
        self.assertIn('PLASMA_MOBILE_VERSION="6.8.3-r101"', prepare)
        self.assertIn('built_plasma_mobile="$(package_version plasma-mobile)"', prepare)
        self.assertIn('[[ "$built_plasma_mobile" = "$PLASMA_MOBILE_VERSION" ]]', prepare)
        self.assertIn('plasma_mobile_version="$(package_version plasma-mobile)"', prepare)
        self.assertIn('[[ "$plasma_mobile_version" = "$PLASMA_MOBILE_VERSION" ]]', prepare)
        # The install list pins it for /etc/apk/world, like gmobile and phoc.
        adds = shell_function(prepare, "install_add_string")
        self.assertIn('"plasma-mobile=$PLASMA_MOBILE_VERSION"', adds)
        # ...and the installed APK database is checked against the same value.
        self.assertIn('plasma_mobile_args=(--plasma-mobile-version '
                      '"$(package_version plasma-mobile)")', prepare)
        checker = CHECKER.read_text()
        self.assertIn('PLASMA_MOBILE_VERSION = "6.8.3-r101"', checker)
        self.assertIn('required["plasma-mobile"] = args.plasma_mobile_version or '
                      'PLASMA_MOBILE_VERSION', checker)
        self.assertIn('verify.add_argument("--plasma-mobile-version")', checker)

    def test_the_disproved_qt_rebuild_recipes_are_gone(self):
        for package in self.REMOVED:
            with self.subTest(package=package):
                self.assertFalse((DEVICE / package).exists(), package)
                self.assertFalse((LINK_FARM / package).exists(), package)
        # Nothing in the flow still offers the opt-in or its list.
        for path in (CHECKER, PREPARE, ENTRY, NURA, DOCS):
            with self.subTest(path=path.name):
                self.assertNotIn("NURA_QT_REBUILDS", path.read_text())
                self.assertNotIn("qt-private-api-recipes", path.read_text())


class TestPlasmaOptIns(unittest.TestCase):
    """The one Plasma opt-in is off unless asked for."""

    def checker_cli(self, command, env):
        full = {"NURA_UI": "plasma"}
        full.update(env)
        return run([sys.executable, str(CHECKER), command], full)

    def test_defaults_are_todays_package_set(self):
        clc = load_checker("plasma")
        self.assertFalse(clc.PLASMA_NO_XWAYLAND)
        self.assertEqual(clc.EXCLUDED_PACKAGES, ())
        # PowerDevil is the stock repository build again.
        self.assertEqual(clc.POWERDEVIL_VERSION, "6.8.3-r0")
        # The Qt-rebuild opt-in and its recipe list are gone.
        for dropped in ("QT_REBUILDS", "QT_PRIVATE_API_RECIPES",
                        "QT_PRIVATE_API_CANDIDATE_DIRS"):
            with self.subTest(dropped=dropped):
                self.assertFalse(hasattr(clc, dropped), dropped)
        self.assertNotEqual(self.checker_cli("qt-private-api-recipes", {}).returncode, 0,
                            "the subcommand must be gone, not empty")
        self.assertEqual(self.checker_cli("exclude-list", {}).stdout.strip(), "")
        links = clc.local_recipe_links()
        # plasma-mobile is the only local Plasma recipe.
        self.assertEqual(links["main/plasma-mobile"], "device/testing/plasma-mobile")
        self.assertNotIn("extra-repos/systemd/plasma-workspace", links)
        # The default plan says so before the build starts.
        with tempfile.TemporaryDirectory() as tmp:
            result = plan(pathlib.Path(tmp), ["--ui", "plasma", "--pmaports-ref", "main"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("local Plasma recipe: plasma-mobile 6.8.3-r101", result.stdout)
            self.assertIn("Plasma exclusions:  none (default", result.stdout)

    def test_no_xwayland_is_opt_in(self):
        listed = self.checker_cli("exclude-list", {"NURA_PLASMA_NO_XWAYLAND": "1"})
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(listed.stdout.strip(), "xwayland,xwaylandvideobridge")
        with tempfile.TemporaryDirectory() as tmp:
            result = plan(pathlib.Path(tmp), ["--ui", "plasma", "--pmaports-ref", "main"],
                          {"NURA_PLASMA_NO_XWAYLAND": "1"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Plasma exclusions:  xwayland,xwaylandvideobridge (opt-in", result.stdout)
            self.assertIn("local Plasma recipe: plasma-mobile 6.8.3-r101", result.stdout)
        adds = shell_function(PREPARE.read_text(), "install_add_string")
        # The install list keeps Xwayland unless the opt-in drops it.
        self.assertIn('[[ "$NO_XWAYLAND" = 1 ]] || add+=("xwayland")', adds)
        self.assertIn('powerdevil=6.8.3-r0', adds)

    def test_phosh_refuses_the_plasma_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = plan(pathlib.Path(tmp), ["--ui", "phosh"],
                          {"NURA_PLASMA_NO_XWAYLAND": "1"})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Plasma-only", result.stderr)

    def test_bad_values_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = plan(pathlib.Path(tmp), ["--ui", "plasma"],
                          {"NURA_PLASMA_NO_XWAYLAND": "yes"})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must be 0 or 1", result.stderr)

    def test_the_flow_prints_the_recipe_before_the_password(self):
        prepare = PREPARE.read_text()
        prompt = prepare.index("\nread_user_password\n")
        self.assertLess(prepare.index("local Plasma recipe: plasma-mobile"), prompt)
        self.assertLess(prepare.index("Plasma exclusions:"), prompt)
        # Phosh builds gmobile/phoc instead of any Plasma recipe.
        phosh_branch, _ = ui_build_branches(prepare)
        self.assertIn("ui_builds+=(gmobile phoc)", phosh_branch)
        self.assertNotIn("plasma-mobile", phosh_branch)


class TestPmaportsRemoteUrl(unittest.TestCase):
    """pmbootstrap names the pmaports remote by matching its hardcoded
    postmarketOS URLs (pmb/helpers/git.py:get_upstream_remote) and then reads
    channels.cfg from <remote>/main, so a Nura checkout carries that URL as a
    second fetch URL for origin; git fetch still uses the Nura URL first."""

    def test_the_helper_extends_origin_idempotently(self):
        prepare = PREPARE.read_text()
        helper = prepare.split("add_pmaports_upstream_url() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('git -C "$tree" config --get-all remote.origin.url', helper)
        self.assertIn('grep -qxF -- "$PMAPORTS_LEGACY_URL"', helper)
        self.assertIn('git -C "$tree" config --add remote.origin.url '
                      '"$PMAPORTS_LEGACY_URL"', helper)
        # The old URL is the one the checker already accepts as official, and
        # it is the postmarketOS URL pmbootstrap's table knows.
        checker = CHECKER.read_text()
        self.assertIn('"https://gitlab.postmarketos.org/postmarketOS/pmaports.git"',
                      checker)
        self.assertIn("PMAPORTS_LEGACY_URL="
                      '"https://gitlab.postmarketos.org/postMarketOS/pmaports.git"',
                      prepare)

    def test_only_a_nura_build_touches_the_remotes(self):
        prepare = PREPARE.read_text()
        calls = [line.strip() for line in prepare.splitlines()
                 if "add_pmaports_upstream_url \"" in line]
        self.assertEqual(len(calls), 3, calls)
        # The clone/reuse path runs inside the --pmaports-ref branch, after the
        # early return that keeps the pinned baseline untouched.
        select = prepare.split("select_pmaports_source() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('add_pmaports_upstream_url "$dir"', select)
        self.assertLess(select.index('[[ -n "$PMAPORTS_REF" ]] || return 0'),
                        select.index('add_pmaports_upstream_url "$dir"'))
        # The candidate copy is patched on reuse and right after a fresh copy,
        # both guarded by the ref.
        for line in calls:
            if 'add_pmaports_upstream_url "$dir"' in line:
                continue
            self.assertIn('[[ -z "$PMAPORTS_REF" ]] || add_pmaports_upstream_url '
                          '"$STATE/pmaports"', line)
        # Nothing else in the flow overrides pmbootstrap's channel lookup.
        for path in (PREPARE, NURA, ENTRY):
            self.assertNotIn("PMB_CHANNELS_CFG", path.read_text())


if __name__ == "__main__":
    unittest.main()
