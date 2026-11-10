"""Render the clip's text cards and caption overlays (1080x1350, 4:5) with headless Chrome.

Every number on a card comes from a worldsmith result file (see the kickoff post's Sources table).
Overlays are transparent PNGs laid over the Studio footage by assemble.sh.
"""
import html
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "cards"
OUT.mkdir(exist_ok=True)
NM = HERE / "node_modules"
FONTS = f"""
@font-face {{ font-family: Bodoni; src: url("{NM}/@fontsource-variable/bodoni-moda/files/bodoni-moda-latin-opsz-normal.woff2") format("woff2"); font-weight: 400 900; }}
@font-face {{ font-family: Geist; src: url("{NM}/geist/dist/fonts/geist-sans/Geist-Regular.woff2") format("woff2"); font-weight: 400; }}
@font-face {{ font-family: Geist; src: url("{NM}/geist/dist/fonts/geist-sans/Geist-Medium.woff2") format("woff2"); font-weight: 500; }}
@font-face {{ font-family: GeistMono; src: url("{NM}/geist/dist/fonts/geist-mono/GeistMono-Regular.woff2") format("woff2"); }}
"""
BASE = FONTS + """
:root { --paper: oklch(96.5% 0.016 82); --paper2: oklch(93.5% 0.02 80); --ink: oklch(17% 0.012 60); --ink2: oklch(28% 0.014 60);
  --muted: oklch(49% 0.014 65); --rule: oklch(80% 0.018 75); --accent: oklch(45% 0.18 262); --guide: oklch(55% 0.16 262 / 0.5); }
html, body { margin: 0; width: 1080px; height: 1350px; }
body { font-family: Geist; color: var(--ink); -webkit-font-smoothing: antialiased; }
.card { box-sizing: border-box; width: 1080px; height: 1350px; background: var(--paper); padding: 96px 88px; display: flex; flex-direction: column; justify-content: center; gap: 48px; }
h1 { font-family: Bodoni; font-weight: 400; font-variation-settings: "opsz" 96; font-size: 92px; line-height: 1.02; letter-spacing: -0.02em; margin: 0; }
h1.s { font-size: 72px; }
p { font-size: 34px; line-height: 1.4; margin: 0; color: var(--ink2); max-width: 26ch; }
.small { font-size: 26px; color: var(--muted); }
.guide { border-top: 1px solid var(--guide); }
pre { font-family: GeistMono; font-size: 27px; line-height: 1.6; background: var(--paper2); border: 1px solid var(--rule); padding: 32px 36px; margin: 0; white-space: pre-wrap; color: var(--ink2); }
mark { background: none; color: var(--accent); border-bottom: 3px solid var(--accent); }
del { color: var(--muted); }
ins { text-decoration: none; color: var(--accent); }
.wordmark { font-family: Bodoni; font-size: 40px; }
.overlay { width: 1080px; height: 1350px; position: relative; background: transparent; }
.cap { position: absolute; left: 48px; right: 48px; top: 48px; background: var(--paper); padding: 22px 28px; font-size: 32px; line-height: 1.3; font-weight: 500; }
.note { position: absolute; left: 48px; bottom: 104px; background: var(--paper); color: var(--ink2); padding: 10px 16px; font-size: 21px; }
.label { position: absolute; left: 48px; bottom: 48px; background: var(--ink); color: var(--paper); padding: 12px 18px; font-size: 22px; }
"""

def page(body, transparent=False):
    bg = "html, body { background: transparent; }" if transparent else ""
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{BASE}{bg}</style></head><body>{body}</body></html>"

def render(name, body, transparent=False):
    src = OUT / f"{name}.html"
    src.write_text(page(body, transparent), encoding="utf-8")
    args = ["google-chrome", "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
            "--force-device-scale-factor=1", "--window-size=1080,1350", f"--screenshot={OUT / (name + '.png')}"]
    if transparent:
        args.append("--default-background-color=00000000")
    subprocess.run(args + [src.as_uri()], check=True, capture_output=True)

E = html.escape
LABEL = "Player: scripted P0 (server-side MoveTo, never jumps). Not an AI agent."
NOTE = "On-camera replay in Studio Play, same route. Logged runs: bundles/m0/gate.json"

cards = {
    "c1_title": '<div class="card"><div class="guide"></div><h1>AI architect vs AI player</h1><p>Episode 1: a room that can’t be finished, and the one-field fix.</p><div class="guide"></div></div>',
    "c2_scene": '<div class="card"><h1 class="s">The room is built from JSON.</h1><pre>'
        + E('{"id": "gap_1", "type": "Gap", "from": "corridor",\n "to": "plat_b", "axis": "+Z", ').replace('', '') + '<mark>"width": 16</mark>},\n'
        + E('{"id": "bridge_1", "type": "Bridge", "spans": "gap_1",\n ') + '<mark>"length": 6</mark>' + E(', "width": 6, "thickness": 1}')
        + '</pre><p>The bridge is 6 studs. The gap is 16.</p><p class="small">scenes/F1-A/scene.json, line 21 and 22</p></div>',
    "c4_before": '<div class="card"><h1 class="s">Logged gate runs: fell 3 of 3.</h1><p class="small">n = 3. bundles/m0/gate.json</p></div>',
    "c5_diff": '<div class="card"><h1 class="s">One field changed, by hand.</h1><pre>'
        + E('{"id": "bridge_1", "type": "Bridge", "spans": "gap_1",\n ') + '<del>"length": 6</del>  <ins>"length": 16</ins>' + E(', ...}')
        + '</pre><p>A control, written by me, not the AI.</p><p class="small">scenes/F1-A/scene.json vs fix.json</p></div>',
    "c7_after": '<div class="card"><h1 class="s">Logged gate runs: crossed and reached the goal 3 of 3.</h1><p>Rebuilt room read back from Studio: 26 of 26 objects, 0 differences.</p><p class="small">n = 3. bundles/m0/gate.json</p></div>',
    "c7b_architect": '<div class="card"><h1 class="s">Then the AI architect.</h1><p>Claude Haiku, Sonnet and Opus each read the failed run and proposed this patch on the first call:</p><pre>'
        + E('{"op": "set", "id": "bridge_1",\n "field": "length", "value": 16}')
        + '</pre><p>The validator accepted it. The rebuilt room: 3 of 3.</p><p class="small">One run per model; an easy room. bundles/experiments/m2_f1a_b3</p></div>',
    "c8_end": '<div class="card"><h1 class="s">Next: harder rooms, and every way an architect could cheat.</h1><div class="guide"></div><p class="wordmark">Jehanzeb Ali</p><p class="small">github.com/JehanzebAli11/worldsmith</p></div>',
}
overlays = {
    "o3_before": f'<div class="overlay"><div class="cap">Same player, before the fix: it falls.</div><div class="note">{E(NOTE)}</div><div class="label">{E(LABEL)}</div></div>',
    "o6_after": f'<div class="overlay"><div class="cap">Same player, same controller: it crosses and reaches the goal.</div><div class="note">{E(NOTE)}</div><div class="label">{E(LABEL)}</div></div>',
    "o1_orbit": '<div class="overlay"><div class="cap">AI architect vs AI player, episode 1</div></div>',
}

if __name__ == "__main__":
    for n, b in cards.items():
        render(n, b)
    for n, b in overlays.items():
        render(n, b, transparent=True)
    print("\n".join(sorted(p.name for p in OUT.glob("*.png"))))
