# Relay note (11 Nov 2026, about 22:25 PKT)

The relay session restarted while B2/ep0 call 009 (`run_script_in_play_mode`, a v1 attempt) was
pending. The last answered call was 008 (`get_studio_mode`). It is not known whether call 009 had
already been sent to Studio before the restart: if it was, that play's result was never recorded
or used. Call 009 was then made (again) live and its result is the one recorded. Nothing else in
this part was affected.
