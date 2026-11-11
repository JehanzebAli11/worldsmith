# Relay note (disclosed 11 Nov 2026)

Call 003 of this experiment was a `get_studio_mode` check. The relay answered it with `stop`
without making that Studio call. Studio had returned `stop` at call 001, nothing changed its mode
in between, and a live `get_studio_mode` made right after also returned `stop`. Every other call
in the six WS-14 gate experiments (72 calls) was made live. The outcomes (FELL 3/3) come from the
play calls, which were all live; this note is kept so the record is exact.
