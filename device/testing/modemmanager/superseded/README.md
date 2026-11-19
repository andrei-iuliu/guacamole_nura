# Superseded: active-SIM-identifier (EF2FE2) workaround

`0002-active-sim-identifier-from-ef2fe2.patch` is **superseded and archived**.
Do not add it to any `source` list or packaging recipe.

Why it was withdrawn: it seeded the active physical slot's SIM identifier as
NULL so the SIM would load EF2FE2. That would have made ModemManager's SIM-swap
comparison pass on a device where two cards are present and two physical slots
report ACTIVE, while the SIM object would still be bound to the slot that MM
selected (slot 2) but carry the other card's identity (slot 1). It masked a
wrong-card read instead of fixing the selection.

The replacement candidate is `../0002-primary-sim-slot-selection.patch`, which
selects the physical slot of the **Primary GW** provisioning session (the
session `UIM Read Transparent` follows) and keeps the historical fallback.
See `../README.md` and the nura-rc2 `PATCH_REPORT.md`.

The file is retained byte-identically (sha256
`eb05c7a28b8f6ec07cd6d6fd3aa57a94f62c61f6ef625cb83e718670ddaff309`) only for
provenance. The regression test asserts it is not referenced by the recipe.
