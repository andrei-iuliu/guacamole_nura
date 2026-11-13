#!/usr/bin/env python3
"""Static lock-order checks for drivers/input/misc/goodix_fp.c.

The deadlock this guards against is:

    gf_irq() (threaded)          ioctl/release/remove
      mutex_lock(&g->lock)       mutex_lock(&g->lock)
                                 disable_irq()  -> waits for gf_irq()
      wait for g->lock  <------  blocked in disable_irq()

The rule enforced here: never call a synchronous IRQ teardown or a
sleeping teardown helper while holding g->lock.  Use disable_irq_nosync()
under g->lock, drop it, then synchronize_irq().  All file operations are
serialized by g->ops_lock, and remove() deregisters the misc device before
taking ops_lock to avoid the misc_mtx/ops_lock ABBA.

Usage: check_lock_order.py [path-to-goodix_fp.c] [--self-test]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

FUNC_RE = re.compile(
    r"^static\s+[^;{}]*?\b([A-Za-z_]\w*)\s*\([^;{}]*?\)\s*\{",
    re.MULTILINE | re.DOTALL,
)

# Calls that must not run while g->lock is held.
FORBIDDEN_UNDER_G_LOCK = [
    (r"\bsynchronize_irq\s*\(", "synchronize_irq"),
    (r"\bdevm_free_irq\s*\(", "devm_free_irq"),
    (r"\bnetlink_kernel_release\s*\(", "netlink_kernel_release"),
    (r"\bmisc_deregister\s*\(", "misc_deregister"),
    (r"\bkfree\s*\(\s*g\s*\)", "kfree(g)"),
    (r"\bgf_release_hw\s*\(", "gf_release_hw"),
    (r"\bgf_irq_sync\s*\(", "gf_irq_sync"),
]

# disable_irq() with no suffix waits for the threaded handler; only the
# nosync/wake variants are allowed in this driver.
DISABLE_IRQ_SYNC_RE = re.compile(r"\bdisable_irq\s*\(")


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def functions(text: str):
    """Yield (name, body, start) for static function definitions."""
    pos = 0
    while True:
        m = FUNC_RE.search(text, pos)
        if not m:
            return
        name = m.group(1)
        start = m.end() - 1
        depth = 0
        i = start
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    yield name, text[start : i + 1], start
                    pos = i + 1
                    break
            i += 1
        else:
            return


def lock_depth_events(body: str):
    """Yield (line_index, depth_before_line, line)."""
    depth = 0
    for idx, line in enumerate(body.splitlines()):
        before = depth
        depth += len(re.findall(r"mutex_lock\s*\(\s*&g->lock\s*\)", line))
        yield idx, before, line
        depth -= len(re.findall(r"mutex_unlock\s*\(\s*&g->lock\s*\)", line))
        # A line that locks and unlocks in the same statement is not used
        # in this driver.
        if depth < 0:
            depth = 0


def check(text: str) -> list[str]:
    code = strip_comments(text)
    errors: list[str] = []

    if DISABLE_IRQ_SYNC_RE.search(code):
        errors.append("synchronous disable_irq() is used; use disable_irq_nosync() + synchronize_irq()")

    if "suppress_bind_attrs = true" not in code:
        errors.append("suppress_bind_attrs = true is required so remove() stays on module unload")

    found = {}
    for name, body, _start in functions(code):
        found[name] = body
        depth = 0
        for idx, before, line in lock_depth_events(body):
            depth = before
            if depth > 0:
                for pattern, label in FORBIDDEN_UNDER_G_LOCK:
                    if re.search(pattern, line):
                        errors.append(
                            f"{name}: {label} called while g->lock is held (line {idx + 1})"
                        )
            depth += len(re.findall(r"mutex_lock\s*\(\s*&g->lock\s*\)", line))
            depth -= len(re.findall(r"mutex_unlock\s*\(\s*&g->lock\s*\)", line))

    irq = found.get("gf_irq")
    if irq is None:
        errors.append("gf_irq() not found")
    else:
        if re.search(r"\bdisable_irq\w*\s*\(", irq):
            errors.append("gf_irq: IRQ handler must not disable its own line")
        if re.search(r"synchronize_irq\s*\(", irq):
            errors.append("gf_irq: IRQ handler must not synchronize its own line")
        if re.search(r"mutex_lock\s*\(\s*&g->ops_lock\s*\)", irq):
            errors.append("gf_irq: handler must not take ops_lock (reverse lock order)")
        if not re.search(r"mutex_lock\s*\(\s*&g->lock\s*\)", irq):
            errors.append("gf_irq: handler should take g->lock")

    release_hw = found.get("gf_release_hw")
    if release_hw is None:
        errors.append("gf_release_hw() not found")
    else:
        # The sync must be outside the g->lock critical section.  The
        # depth check above already catches sync while locked; this also
        # catches a sync before the first unlock (inside the block).
        unlock_at = release_hw.find("mutex_unlock(&g->lock)")
        sync_at = release_hw.find("synchronize_irq")
        if sync_at != -1 and (unlock_at == -1 or sync_at < unlock_at):
            errors.append("gf_release_hw: synchronize_irq appears before mutex_unlock(&g->lock)")

    remove = found.get("gf_remove")
    if remove is None:
        errors.append("gf_remove() not found")
    else:
        dereg = remove.find("misc_deregister")
        ops = remove.find("mutex_lock(&g->ops_lock)")
        if dereg != -1 and ops != -1 and dereg > ops:
            errors.append("gf_remove: misc_deregister must precede ops_lock (misc_mtx ABBA)")
        hw = remove.find("gf_release_hw")
        free = remove.find("devm_free_irq")
        if hw != -1 and free != -1 and free < hw:
            errors.append("gf_remove: devm_free_irq must follow gf_release_hw/synchronize")
        if not re.search(r"mutex_lock\s*\(\s*&g->ops_lock\s*\)", remove):
            errors.append("gf_remove: missing ops_lock serialization")

    for name in ("gf_open", "gf_release", "gf_ioctl"):
        body = found.get(name)
        if body is None:
            errors.append(f"{name}() not found")
        elif not re.search(r"mutex_lock\s*\(\s*&g->ops_lock\s*\)", body):
            errors.append(f"{name}: missing ops_lock serialization")

    return errors


GOOD_SNIPPET = """
static irqreturn_t gf_irq(int irq, void *data)
{
	struct gf_dev *g = data;
	mutex_lock(&g->lock);
	mutex_unlock(&g->lock);
	return IRQ_HANDLED;
}
static void gf_release_hw(struct gf_dev *g)
{
	mutex_lock(&g->lock);
	mutex_unlock(&g->lock);
	synchronize_irq(g->irq);
}
static void gf_remove(struct platform_device *pdev)
{
	misc_deregister(&g->misc);
	mutex_lock(&g->ops_lock);
	gf_release_hw(g);
	devm_free_irq(&pdev->dev, g->irq, g);
	mutex_unlock(&g->ops_lock);
}
static int gf_open(struct inode *inode, struct file *filp)
{
	mutex_lock(&g->ops_lock);
	mutex_unlock(&g->ops_lock);
}
static int gf_release(struct inode *inode, struct file *filp)
{
	mutex_lock(&g->ops_lock);
	mutex_unlock(&g->ops_lock);
}
static long gf_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
{
	mutex_lock(&g->ops_lock);
	mutex_lock(&g->lock);
	mutex_unlock(&g->lock);
	synchronize_irq(g->irq);
	mutex_unlock(&g->ops_lock);
}
static struct platform_driver p = { .driver = { .suppress_bind_attrs = true } };
"""

# A deliberately bad function: synchronous disable under g->lock.
BAD_DISABLE = GOOD_SNIPPET + """
static void bad_disable(struct gf_dev *g)
{
	mutex_lock(&g->lock);
	disable_irq(g->irq);
	mutex_unlock(&g->lock);
}
"""

BAD_SYNC = GOOD_SNIPPET + """
static void bad_sync(struct gf_dev *g)
{
	mutex_lock(&g->lock);
	synchronize_irq(g->irq);
	mutex_unlock(&g->lock);
}
"""

BAD_IRQ = GOOD_SNIPPET + """
static irqreturn_t gf_irq(int irq, void *data)
{
	struct gf_dev *g = data;
	mutex_lock(&g->ops_lock);
	mutex_unlock(&g->ops_lock);
	return IRQ_HANDLED;
}
"""


def self_test() -> int:
    if check(GOOD_SNIPPET):
        print("self-test FAIL: good snippet rejected:", check(GOOD_SNIPPET))
        return 1
    if not any("synchronous disable_irq" in e for e in check(BAD_DISABLE)):
        print("self-test FAIL: synchronous disable_irq not caught")
        return 1
    if not any("bad_sync" in e and "synchronize_irq" in e for e in check(BAD_SYNC)):
        print("self-test FAIL: synchronize under g->lock not caught")
        return 1
    if not any("gf_irq" in e and "ops_lock" in e for e in check(BAD_IRQ)):
        print("self-test FAIL: IRQ handler ops_lock not caught")
        return 1
    print("self-test PASS")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--self-test"]
    self_test_requested = "--self-test" in sys.argv[1:]
    path = Path(args[0]) if args else Path(__file__).resolve().parent.parent / "src/goodix_fp.c"

    if self_test_requested:
        return self_test()

    errors = check(path.read_text())
    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1
    print(f"PASS: lock order checks in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
