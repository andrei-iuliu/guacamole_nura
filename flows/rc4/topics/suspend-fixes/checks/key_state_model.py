#!/usr/bin/env python3
"""Model and structural check for the ath10k SNOC key-completion state machine.

    checks/key_state_model.py <patched-source-dir>

The model mirrors ath10k_install_key() and ath10k_wmi_tlv_key_complete() from
patches/0045: which operation arms the WMI wait, when an acknowledgement can
complete it, and what a timeout does.  The structural assertions bind the model
to the applied source, so a change to the patch that the model does not know
about fails the check instead of silently drifting.

The point of the sequences is the ambiguity reported in review: the WMI event
carries no transaction id, so after a timeout a delayed acknowledgement could
match a later operation with the same vdev/peer/key-index/flags (a repeated
key removal of the same peer does exactly that).  The audited design latches
the sequence on the first timeout and never arms the WMI wait again until the
firmware restarts, falling back to the pre-patch HTT-only wait instead of
returning -EIO.
"""

import pathlib
import sys

SET_KEY, DISABLE_KEY = "set", "disable"
EIO, ETIMEDOUT = -5, -110

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"PASS {name}")
    else:
        print(f"FAIL {name}{': ' + detail if detail else ''}")
        failures.append(name)


class State:
    def __init__(self):
        self.desynced = False
        self.pending = False
        self.status = ETIMEDOUT
        self.dropped_acks = 0


def op(st, cmd, ack_in_wait, ack_status=0, htt_ok=True, late_ack=False):
    """One ath10k_install_key() call.  Returns (ret, armed_wmi_wait)."""
    armed = False
    if not st.desynced:
        st.pending = True
        st.status = ETIMEDOUT
        armed = True

    # ath10k_send_key() always runs.
    if armed:
        if ack_in_wait:
            st.status = EIO if ack_status else 0
            st.pending = False
            ret = st.status
        else:
            ret = ETIMEDOUT
        if ret or cmd == DISABLE_KEY:
            st.pending = False
            if ret:
                st.desynced = True
            return ret, armed

    # HTT-only wait (installs, and every desynced operation).
    if late_ack:
        # The handler only completes when key_wmi_pending is set; a desynced
        # operation never sets it, so the late ack is dropped.
        check("late ack dropped while desynced", not st.pending)
        st.dropped_acks += 1
    ret = 0 if htt_ok else ETIMEDOUT
    if armed:
        st.pending = False
        if ret:
            st.desynced = True
    return ret, armed


def sequences():
    # 1. Normal install and removal complete on the WMI ack; no latch.
    st = State()
    ret, armed = op(st, SET_KEY, ack_in_wait=True)
    check("install completes on WMI ack", (ret, armed) == (0, True))
    check("install does not latch", not st.desynced)
    ret, armed = op(st, DISABLE_KEY, ack_in_wait=True)
    check("removal completes on WMI ack", (ret, armed) == (0, True))

    # 2. Removal timeout latches the sequence.
    ret, armed = op(st, DISABLE_KEY, ack_in_wait=False, htt_ok=False)
    check("removal timeout returns -ETIMEDOUT", (ret, armed) == (ETIMEDOUT, True))
    check("removal timeout latches", st.desynced)

    # 3. After the latch: repeated removal of the same key/peer is NOT armed,
    #    waits HTT only, and a delayed ack is dropped rather than matched.
    ret, armed = op(st, DISABLE_KEY, ack_in_wait=False, htt_ok=False, late_ack=True)
    check("latched removal is not armed", armed is False)
    check("latched removal keeps the old 3 s timeout", ret == ETIMEDOUT)
    check("late ack did not satisfy the latched removal", st.dropped_acks == 1)
    check("latch survives the removal", st.desynced)

    # 4. Reconnect install while latched: HTT path works, still no WMI wait.
    ret, armed = op(st, SET_KEY, ack_in_wait=False, htt_ok=True, late_ack=True)
    check("latched install works through HTT", (ret, armed) == (0, False))
    check("late ack did not satisfy the latched install", st.dropped_acks == 2)

    # 5. The latch never produces -EIO for later operations.
    ret, _ = op(st, DISABLE_KEY, ack_in_wait=False, htt_ok=False)
    check("latched operations never return -EIO", ret != EIO)

    # 6. Only a firmware restart clears the latch, and then WMI is used again.
    st.desynced = False
    ret, armed = op(st, DISABLE_KEY, ack_in_wait=True)
    check("firmware restart re-arms the WMI wait", (ret, armed) == (0, True))

    # 7. A firmware status error latches, and the next install still works.
    st = State()
    ret, armed = op(st, SET_KEY, ack_in_wait=True, ack_status=1)
    check("WMI status error returns -EIO", (ret, armed) == (EIO, True))
    check("WMI status error latches", st.desynced)
    ret, armed = op(st, SET_KEY, ack_in_wait=False, htt_ok=True)
    check("install after status error works", (ret, armed) == (0, False))

    # 8. After a latch, no removal is ever reported as successful without an
    #    HTT acknowledgement.
    st = State()
    op(st, DISABLE_KEY, ack_in_wait=False, htt_ok=False)
    for _ in range(3):
        ret, armed = op(st, DISABLE_KEY, ack_in_wait=False, htt_ok=False)
        check("latched removal is not silently successful",
              ret == ETIMEDOUT and armed is False)


def function_body(text, signature):
    start = text.index(signature)
    end = text.index("\n}\n", start)
    return text[start:end]


def source_checks(root: pathlib.Path):
    mac = (root / "drivers/net/wireless/ath/ath10k/mac.c").read_text()
    tlv = (root / "drivers/net/wireless/ath/ath10k/wmi-tlv.c").read_text()
    core = (root / "drivers/net/wireless/ath/ath10k/core.c").read_text()
    body = function_body(mac, "static int ath10k_install_key(")

    check("pending is armed exactly once", body.count("ar->key_wmi_pending = true;") == 1)
    armed_block = body.split("if (!ar->key_wmi_desynced) {", 1)
    check("pending is armed only when not desynced",
          len(armed_block) == 2 and
          "ar->key_wmi_pending = true;" in armed_block[1].split("}", 1)[0])
    check("desync is latched on a failed wait",
          body.count("ar->key_wmi_desynced = true;") == 1 and
          "if (ret)\n\t\t\tar->key_wmi_desynced = true;" in body)
    check("desync is never cleared from mac.c", "key_wmi_desynced = false" not in mac)
    check("desync is cleared by ath10k_core_start", "ar->key_wmi_desynced = false;" in core)
    check("ath10k_install_key no longer returns -EIO", "-EIO" not in body)
    check("both waits are gated by wmi_wait", body.count("if (wmi_wait) {") == 2)
    check("non-SNOC keeps the legacy handler",
          "if (ar->hif.bus != ATH10K_BUS_SNOC) {\n\t\tath10k_wmi_event_vdev_install_key_complete(ar, skb);"
          in tlv)
    for field in ("key_wmi_vdev ==", "key_wmi_idx ==", "key_wmi_flags ==", "key_wmi_addr,"):
        check(f"ack match uses {field.split()[0].rstrip(',')}", field in tlv)


def main():
    if len(sys.argv) != 2:
        print("usage: key_state_model.py <patched-source-dir>", file=sys.stderr)
        return 2
    sequences()
    source_checks(pathlib.Path(sys.argv[1]))
    print(f"key state model: {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
