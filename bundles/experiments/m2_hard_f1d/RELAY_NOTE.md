# Relay notes (12 Nov 2026)

1. The relay session restarted while B3-haiku/ep1 call 002 (`run_code`, the read-only preflight)
   was pending; it was answered live by the next session (hence its long relay time, about 14,000 s).
   Nothing else was affected.
2. B3-haiku/ep1 call 008 (`get_studio_mode`) was answered `stop` by a helper script before the live
   call was made. The live `get_studio_mode` made right after also returned `stop`. Every other call
   in this part was made live first.
3. B3-sonnet/ep0 call 003 (`get_studio_mode`): the relay sent the live Studio call and the command
   that wrote `stop` to the answer file in the same batch, so the answer was written without waiting
   for the result. The live call returned `stop`, which is what was recorded.
4. B3-sonnet/ep0 call 023: the device bridge dropped once before the answer was written; nothing
   had been recorded, and the same verbatim result of the live Studio call was then sent once.
