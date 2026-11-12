# Relay note (12 Nov 2026)

For B0, B1 and B2 of this part, the relay sometimes did not re-read a pending call's code file
before sending it to Studio. When the file matched an earlier one (same sha256 checked with
cmp/diff, or the controller script differing only in its NONCE line), it sent the earlier content
with the new nonce. Studio does not check the hash of what it receives, so this is recorded here.
The outcomes (B0 and B2 STUCK x3 with no LEVER_ACTIVATED, B1 STUCK x3 then SUCCESS x3) match the
live gate (bundles/experiments/gate_f1c, gate_f1c_fix). From B3-haiku on, the relay re-reads every
code file and sends it exactly as read.
