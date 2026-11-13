# Reference: original displayport proposal (unmodified copies)

These three files are verbatim copies of the topic input, kept for review and
for the provenance of the rebased patches.  Do not edit them.

| file | original path | sha256 |
| --- | --- | --- |
| `original-README.md` | `Patches/displayport/README.md` | `4b2979b733a5722d740800d77a19834ed398943f0565f1e25102ea3fcee3ee0d` |
| `01-video.md` | `Patches/displayport/docs/01-video.md` | `66e45f9d1f48db3c76e0c041118c74bbf12c43583ad396c5731d1b1b0ec629c0` |
| `02-audio.md` | `Patches/displayport/docs/02-audio.md` | `2e51ca0dca8d46ad6fbd18898cb15af456334b0c488a14501dc37db674695804` |

Both docs target **6.17** and the flattened `base_guacamole_working.dts`.  The
rc4 baseline is **7.0.0-sm8150** with a real kernel devicetree
(`arch/arm64/boot/dts/qcom/`), so the DT parts were rewritten rather than
applied.  The kernel parts were rebased.  Every claim the patches rely on was
re-checked against the 7.0 sources; the results, including the claims that did
not survive, are in `../../README.md` ("Claim review").

The AFE parameter facts and the prepare ordering the audio patches rely on
(ids, payload sizes, ordering, tolerance) were additionally verified against
the downstream LineageOS `lineage-23.2` reference tree for this device — quoted
excerpts, line numbers and file hashes are in
`lineage-23.2-dp-audio-evidence.md`.  That tree is a downstream reference, not
proven to be the factory build, so it establishes what downstream code does,
not what the shipped image executed.  What is still unverified is listed there
too (firmware acceptance, SET_PARAM packaging, order relative to the port
config).

Read `../../README.md` first: these copies still contain statements that are
wrong for 7.0 (for example the DAPM-direction explanation for the `-110`
timeout, the patch numbering, and the claim that the DT diffs only need a
`dtc` run).
