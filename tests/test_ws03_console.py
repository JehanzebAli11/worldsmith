"""WS-03: console parsing of WS|<nonce>|<EVENT>|... lines (SPEC sections 8 and 16).

Upstream warns that console lines carry no type and include other plugins' output, so only
exact, well-formed lines with the run's nonce count.
"""

import re

import pytest

from worldsmith.runner import console

N = "0123456789abcdef"


def line(event="BRIDGE_CROSSED", nonce=N, id_="trig_bridge", pos="0.10,3.00,52.70", t="1731150000.125"):
    return f"WS|{nonce}|{event}|id={id_}|pos={pos}|t={t}"


def test_parses_a_well_formed_line():
    evs = console.parse_events(line(), N)
    assert evs == [console.Event(nonce=N, event="BRIDGE_CROSSED", id="trig_bridge",
                                 pos=(0.1, 3.0, 52.7), t=1731150000.125)]


def test_pos_may_be_na():
    (ev,) = console.parse_events(line(event="GATE_OPENED", id_="gate_1", pos="na"), N)
    assert ev.pos is None


def test_keeps_order_and_all_five_events():
    evs = ["LEVER_ACTIVATED", "GATE_OPENED", "BRIDGE_CROSSED", "KILL_FLOOR_TOUCHED", "GOAL_TOUCHED"]
    text = "\n".join(line(event=e) for e in evs)
    assert [e.event for e in console.parse_events(text, N)] == evs


def test_wrong_nonce_is_ignored():
    text = "\n".join([line(nonce="ffffffffffffffff"), line(nonce="-"), line()])
    assert len(console.parse_events(text, N)) == 1


@pytest.mark.parametrize("foreign", [
    "Hello world!",
    "  -- plugin WS|" + N + "|GOAL_TOUCHED|id=goal|pos=na|t=1.000",          # not at line start
    "XWS|" + N + "|GOAL_TOUCHED|id=goal|pos=na|t=1.000",
    line(event="TELEPORTED"),                                                 # unknown event
    line(event="goal_touched"),
    line(id_="Goal"),
    line(id_='goal"'),
    line(pos="1,2,3"),
    line(pos="1.00,2.00"),
    line(t="soon"),
    line() + "|extra=1",
    line().replace("|id=", "|ID="),
    "WS|" + N + "|GOAL_TOUCHED",
    "WS_READBACK|1|1|{}",
    "WS_BUILD_OK|" + "a" * 64 + "|42",
])
def test_foreign_and_malformed_lines_are_ignored(foreign):
    assert console.parse_events(foreign, N) == []
    assert console.parse_events(foreign + "\n" + line(), N) == console.parse_events(line(), N)


@pytest.mark.parametrize("prefix,suffix", [
    ("", ""), ("12:34:56.789  ", ""), ("12:34:56.789  ", "  -  Server - ws_event:14"),
    ("", "  -  Server"), ("  ", "  "),
])
def test_studio_timestamp_prefix_and_context_suffix_are_accepted(prefix, suffix):
    assert len(console.parse_events(prefix + line() + suffix, N)) == 1


def test_requires_a_valid_expected_nonce():
    for bad in ("", "-", "XYZ", "0123"):
        with pytest.raises(ValueError):
            console.parse_events(line(), bad)


def test_new_nonce_is_16_lowercase_hex_and_fresh():
    a, b = console.new_nonce(), console.new_nonce()
    assert re.fullmatch(r"[0-9a-f]{16}", a) and a != b


def test_parser_matches_the_luau_format_string():
    # ws_lib prints string.format("WS|%s|%s|id=%s|pos=%s|t=%.3f", ...) with pos "%.2f,%.2f,%.2f".
    pos = "%.2f,%.2f,%.2f" % (-0.001, 3.0, 52.66)
    text = "WS|%s|%s|id=%s|pos=%s|t=%.3f" % (N, "KILL_FLOOR_TOUCHED", "kill_floor", pos, 12.3456)
    (ev,) = console.parse_events(text, N)
    assert ev.pos == (-0.0, 3.0, 52.66) and ev.t == 12.346
