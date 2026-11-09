# Worldsmith (Architect vs Player): build plan

**For:** Jehanzeb Ali · **Written:** 7 Nov 2026, Asia/Karachi (PKT) · **Status:** a plan only. No repo exists for it yet. Nothing has been built, run or published, and no paid call has been made.
**Read with:** `01-ideas-and-priorities.md` (why this is first) and `03-claude-decision-and-kickoff.md` (how Claude builds it).
**Labels:**
- **[VERIFIED]** is sourced in §18 and was checked on 7 Nov 2026.
- **[OUR DESIGN]** is proposed here and is not an upstream API.
- **[ESTIMATE]** is a proposed timebox or threshold, not a measurement.
- **[ILLUSTRATIVE]** marks an example value, caption or schema excerpt. It is not a result and not copy-paste ready.

---

## 1. One-paragraph summary

Worldsmith is a small companion tool for Autopilot. It builds **one owned Roblox room** from a bounded JSON recipe, and a **frozen player** tries to complete it. Worldsmith records an **instrumented failure event**, and an **architect** proposes a **bounded scene patch** that an independent **validator** must approve. Then the room is **rebuilt from scratch**, the *same frozen* player **actually retries**, and the tool emits a side-by-side **replay bundle**.

The original work is four pieces:
1. the world grammar and validator;
2. the failure taxonomy and instrumentation;
3. the transactional edit→rebuild→rerun loop;
4. reproducible edit+run bundles.

This is a clear executable demo of closed-loop *world* revision. It is **not** a novel general world model, does **not** prove human fun, does **not** prove that Roblox physics will be solved generally, and is **not** pixels-only failure discovery. The benchmark is *patch planning* from privileged, designed instrumentation.

## 2. Why it is distinct
- Autopilot's existing **`evolve`** changes the *harness, prompts or kit constants* to do better on a locked suite [VERIFIED]. **Worldsmith does the opposite:** it freezes the player and the evaluation and changes the *world*. Don't sell `evolve` as new.
- It isn't Dream Gauntlet (no neural rendering, no RL), the Autopilot learning curve, Spark-1 QA, Batao or the 67.js adapter (all in the existing JzLab plan).
- [Mirage Tesseract](https://github.com/mirage-hq/Tesseract) ("Claude authors an editable 3D scene") and [Vork](https://github.com/voyager-so/vork) are **inspiration only**: proprietary and source-available, never dependencies.

## 3. What the substrate actually provides [VERIFIED, staging branch]

**Autopilot basics** ([ramenvr/autopilot](https://github.com/ramenvr/autopilot)):
- Licensed Apache-2.0.
- An LLM coding agent reads the game's source, probes the engine and writes a harness. The **Jev** decision model (TypeSafe) then picks among harness options during play.
- So the player is **source-aware and privileged**, not pixels-only. Disclose this in every claim.

**Your [PR #40 (roblox-mcp)](https://github.com/ramenvr/autopilot/pull/40)** (merged to staging 7 Nov 05:09 PKT, commit `53a41f6a9c34d0434cd50f70eb528d4d74ec0e36`):
- How it maps onto Studio MCP:
  - Reads go through `execute_luau`, and one `game.batch` is one call.
  - Input goes through `user_keyboard_input` / `user_mouse_input`.
  - Start, stop and restart use `start_stop_play`.
  - Errors are read from the console through `get_console_output`.
  - Stills come from `screen_capture`.
- During play, reads run in the Client data model and writes in the Server data model. In Play mode, `Edit` is refused for `execute_luau`.
- Units: Y-up studs. The kit's documented convention is 1 stud = 0.28 m. That is a units convention, not a guaranteed character scale. Measure the character in M0.
- **Limits:**
  - Studio must be open (Mac or Windows; Windows untested in the PR).
  - It isn't headless.
  - The game runs in real time with **no pause or step**.
  - Stills only, with **no native video**.
  - There are no frame-accurate detectors.
  - Reflex play is mostly out of reach.
- The upstream author's batched observe was p50 67 ms / p95 72 ms (n=100, on a Mac). That is *their* number, not ours.
- `character_navigation` is privileged pathfinding. Don't present it as learned movement.
- **Source discrepancy:** the PR text reports a live 12-prompt bench run, while the staging doc says of the suite's expected verdicts that "none of them has been run against Studio yet", and that "whole runs are still being tried". **Neither is our validation.** Whole live runs are unproven for us until M0.

**Your [PR #38 (regress replay)](https://github.com/ramenvr/autopilot/pull/38)** (merged to staging 7 Nov 04:48 PKT): replays saved CONFIRMED episodes using their recorded decisions and checks the final state and evidence, with no new model calls. It is **not** frame-perfect physics replay, and a recorded trace can go **stale** once the world changes.

**Accounts and billing:**
- Live Autopilot's Jev decisions need an **Aura account** or **BYOK with a TypeSafe key**.
- The [staging README](https://github.com/ramenvr/autopilot/blob/staging/README.md) describes the default case: via Claude Code with an Aura sign-in, the LLM usage counts against your Claude subscription. In practice, Claude work is billed to whichever **auth route is active**: subscription, or Console/API credits. Check `/status`.
- Anthropic credits can fund Claude Code, architect calls and Autopilot's harness authoring when routed through Console. They do **not** pay for Jev.

**The upstream test place** (`examples/roblox_testplace`) already has a lever that opens a door. That is a *pattern*, and Worldsmith still builds its own owned room.

## 4. The 20-second demo [ILLUSTRATIVE storyboard; real counts and values are substituted from bundles]
| t | Shot |
|---|---|
| 0–4 s | Overhead still: spawn on the near side, lever on a side ledge, gate, a bridge over a fixed gap, goal on the far side. Caption: "Room v1, built from a small JSON recipe." |
| 4–9 s | Stills from the attempt: lever pulled, gate opens, player steps onto the bridge, **falls**. Card: `FELL near bridge_1` · `support-length check: bridge < gap` (geometry check, not a diagnosis from pixels). |
| 9–12 s | Architect card: `set bridge_1.length` (larger value). Validator: ✔ schema, ✔ bounds, ✔ protected entities untouched, ✔ gap unchanged, ✔ lever detour kept. |
| 12–18 s | Room v2 rebuilds and the *same frozen* player retries, crosses the bridge (`BRIDGE_CROSSED`) and reaches the goal. Badge: `SUCCESS <measured k>/<N>`. |
| 18–20 s | Side by side v1/v2 with scene hashes, plus "random valid patches: <measured>" from result files. |

Captions name the player tier that was actually used. Stills come from `screen_capture`, plus an OS screen recording made by you.

## 5. The room family (F1, "bridge family") [OUR DESIGN]
- **Layout:** fixed start platform (protected spawn) → corridor → `lever_1` on a side ledge (a detour) → `gate_1` (opens while the lever is active) → `bridge_1` across `gap_1` → fixed far platform with the protected `goal`. A protected kill floor sits below the gap.
- **Locked in F1:** gap width and both platforms' geometry. Start and goal are on **opposite fixed sides** of the gap.
- **Success needs both events, in order:** `BRIDGE_CROSSED` (a trigger volume on the bridge's far half) and `GOAL_TOUCHED`. This is a required check, not a proof against every bypass; locked platform geometry, the no-new-floors rule and support/contact checks enforce the intended crossing.
- **Low-reflex by design:** walking only, no timing puzzles.

**Hand-authored fixtures:**
- **F1-A:** bridge too short, the M0 fixture. Its known defect gives it a paired fix.
- **F1-E:** a passing control.

**Later families** (M1+, with widened ops): lever unreachable behind a wall, low clearance, gate not linked to the lever.

**What M0 shows:** a known, hand-authored defect plus a paired-intervention patch gives a **functional** result. It is **not** causal diagnosis for arbitrary rooms.

## 6. World grammar and patch DSL [OUR DESIGN]

**NON-EXECUTABLE SCHEMA EXCERPT.** Required entities (`plat_b`, `kill_floor`, `bounds`, `corridor`, the trigger volumes) and some fields are omitted, and the values are illustrative. Don't copy it as a valid scene. WS-02 defines the complete, validated fixture.
```jsonc
{"schema":"worldsmith.scene/0.1","scene_id":"room0","family":"F1","version":1,
 "units":"stud","up":"+Y",
 "protected":["spawn","goal","kill_floor","bounds","plat_a","plat_b","gap_1"],
 "constraints":{"min_gap":6,"min_lever_detour":8,"player_physics":"locked",
                "required_events":["BRIDGE_CROSSED","GOAL_TOUCHED"]},
 "entities":[
  {"id":"spawn","type":"Spawn","on":"plat_a"},
  {"id":"gap_1","type":"Gap","from":"plat_a","to":"plat_b","width":9},
  {"id":"bridge_1","type":"Bridge","spans":"gap_1","length":7,"width":4},
  {"id":"lever_1","type":"Lever","pos":[-8,3,12],"controls":"gate_1"},
  {"id":"gate_1","type":"Gate","opens_on":"lever_1"},
  {"id":"goal","type":"Goal","on":"plat_b"}
  /* … plat_a, plat_b, kill_floor, bounds, corridor, triggers omitted … */ ]}
```
- The entity types form a **closed set**: Spawn, Platform, Gap, Bridge, Ramp, Wall, Lever, Gate, Goal, KillFloor, Trigger and Decor.
- Every numeric field has **hard bounds** in `grammar/bounds.yaml`.
- `canonical_hash` = SHA-256 of the canonical JSON (sorted keys, fixed float precision).

**Patch ops by stage:**
- **M0:** the hand-authored fixed scene differs only in `bridge_1.length`; no automated patch engine is needed yet. The later patch engine initially accepts only `{"op":"set","id":"bridge_1","field":"length","value":<number>}`.
- **M1+:** may add bounded `set` or `move` on whitelisted bridge, support, lever and gate fields, plus `add` of a Wall or Decor from a palette. Each widening is a recorded ADR.
- **Never:** removing protected IDs, Ramp or Platform additions in F1, or any code, Luau, script, URL or free-text field. Unknown keys are rejected.
- **Limits per episode:** at most **3 architect revisions** and at most **3 ops per patch** (M0 effectively uses 1).
- A patch is applied only if `base_hash` matches the current scene.

## 7. Validator (independent of the architect) [OUR DESIGN]
1. **Schema and bounds:** JSON Schema plus `bounds.yaml`.
2. **Protection:** spawn, goal, platforms, gap, kill floor and bounds are unchanged. `player_physics` is locked (no WalkSpeed, JumpPower, gravity or teleport changes), and there are no new floors, ramps or teleport pads.
3. **Difficulty:** `gap_1.width ≥ min_gap` **always** (locked in F1). The lever detour stays at or above `min_lever_detour`, and the gate must still depend on the lever. The lever and gate may be *repaired* in later families as long as both rules hold.
4. **Structural validity (hard reject):** forbidden solid penetrations (with explicit allowances for designed bridge/platform joins), dangling references (such as a gate whose lever doesn't exist), or anything outside the room bounds.
5. **Navigation approximation:** a coarse walk graph over top surfaces, using the step and jump limits and capsule measured in M0. The output is `GRAPH_CONNECTED` or `GRAPH_DISCONNECTED` *under that declared approximation*. **It is never a physical proof of solvability or unsolvability. Actual runs decide.**
6. **Inputs disclosure:** the architect gets the scene JSON, the observed event tags and the trace summary. That is privileged, designed instrumentation, so the benchmark measures **patch planning**, not failure discovery from pixels. It never sees player harness internals, Autopilot memory, Jev prompts or test constants, and its input is hashed and logged.

The validator outputs `{ok, violations[], warnings[], graph:"CONNECTED|DISCONNECTED"}`. Rejected patches count against the revision budget.

## 8. Build, reset and transactions [OUR DESIGN]
- A **compiler** turns scene JSON into Luau from **fixed, reviewed templates** (one per entity type). The architect never writes Luau.
- Everything lives under one `Workspace.Worldsmith` folder. Instance names equal entity IDs, and the attributes `ws_scene_hash` and `ws_version` are set.
- **Transaction:**
  1. Stop play.
  2. In the Edit data model, delete `Workspace.Worldsmith` and rebuild it from the full scene (no diffs).
  3. Read back the instance tree and compare it with the expected tree (IDs, sizes, positions within tolerance).
  4. Commit `scene_vN.json`.
  5. Start play.
- If any step fails, roll back to the last committed scene and log `BUILD_FAILED`.
- **Instrumentation:** server-side templates print structured tags with a run nonce (`WS|<nonce>|event|…`) for `LEVER_ACTIVATED`, `GATE_OPENED`, `BRIDGE_CROSSED`, `KILL_FLOOR_TOUCHED` and `GOAL_TOUCHED`. The tool polls them via `get_console_output` plus batched position reads. Upstream warns that console lines have no type and include other plugins' output, so parse by exact prefix and nonce.
- **Timeouts:**
  - Every attempt has a **per-attempt wall-clock timeout `T_attempt`**, declared in config before runs. The proposed value is 120 s [ESTIMATE], to be revised only *after* M0 measures a normal completion time, and declared before comparisons.
  - Timing starts at `start_stop_play(true)`.
  - Polling uses a fixed interval, with `max_polls = T_attempt / interval`.
  - Each MCP call has its own timeout. **No unbounded polling or waits.**
  - Wall-clock time and Roblox server time are logged per event. There are no frame-exact claims.

## 9. The player: frozen and disclosed
| Tier | What | Allowed claim |
|---|---|---|
| **P0 scripted controller** | Deterministic key-hold waypoints through the real `roblox-mcp` input tools. One route is chosen **per fixture family before** comparing original and fixed scenes, and the **same controller code, hash and inputs** run before and after the patch. | "Substrate probe: the patch changes the real engine outcome." **Not** an Autopilot or agent result. |
| **P1 `autopilot regress`** | Replays a CONFIRMED trace on the **same** scene version | Reproducibility of a recorded run. STALE if the scene hash differs. |
| **P2 frozen live Autopilot** | A freeze manifest (hashes) covering the exported harness, options, controllers, LLM and Jev model versions, prompt, kit and an **initial memory snapshot**. `evolve` is disabled. **No harness or model regeneration and no persistent memory adaptation between patches** (memory is reset to the snapshot before each attempt). Runtime decisions may vary with observations, but the policy may not. `AUTOPILOT_SHARE=0`. | "Frozen source-aware Autopilot player." **Gate:** if Autopilot can't run a frozen harness cleanly through the connector, P2 fails. Don't claim "same player" just because the goal prompt is the same. |

- Different player versions are only allowed as **separate, labelled experiments**.
- **Repetition:** the pre-declared **N = 3 attempts per scene version, pass bar k = 3** (3/3). 3/3 is *functional-repeatability evidence only*, not statistical reliability.
- **If results are inconsistent** (for example 2/3), stop and debug. **Never lower the threshold after the fact.**
- **Restart:** `start_stop_play` stop then start. Seed where the place allows it, and log the seed.
- **Source access:** Autopilot reads place scripts, including Worldsmith's generated ones. That is disclosed.

## 10. Failure taxonomy (v0) [OUR DESIGN]
| Tag | Kind | Rule |
|---|---|---|
| `SUCCESS` | observed | `BRIDGE_CROSSED` then `GOAL_TOUCHED` |
| `FELL` | observed | Kill-floor event, plus the last position |
| `BRIDGE_SHORT_HYPOTHESIS` | hypothesis | `FELL` near the bridge **and** an independent geometry check (bridge length < gap) **and**, to be confirmed, the same frozen player succeeding 3/3 after a bridge-length-only change. Without all three: `FELL` with cause `UNCLASSIFIED`. |
| `LEVER_NOT_ACTIVATED` / `GATE_NOT_OPENED` | observed | Missing events at timeout |
| `STUCK` | observed | Displacement < ε over a window |
| `TIMEOUT` | observed | `T_attempt` reached |
| `HARNESS_ERROR` / `CONNECTOR_ERROR` / `BUILD_FAILED` | system | Exceptions and console errors |
| `UNCLASSIFIED` | — | The default cause. Never forced into a tag. |

A fall near the bridge does **not** by itself separate "bridge short" from "missed jump" or player error. The M0 fixture's defect is known **by construction**, and that must be disclosed. The architect sees tags and scene state, which is privileged instrumentation.

## 11. Loop, trace and folders [OUR DESIGN]

**Loop:** load scene → validate → build → attempt × N → classify → if not 3/3 SUCCESS and revisions < 3: architect → patch → validate → build → attempt × N → … → bundle.

**Trace line** [ILLUSTRATIVE, incomplete; the `…` values are placeholders]:
```jsonc
{"run_id":"…","episode":"room0-F1A","scene_hash":"…","scene_version":1,"attempt":1,
 "player":{"tier":"P0","controller_sha":"…","freeze_manifest_sha":null,"t_attempt_s":120},
 "event":"FELL","t_wall":"…Z","t_server":"…","pos":["…"],"poll_interval_ms":"…","cause":"UNCLASSIFIED"}
```
- Architect calls are logged as `{model_role, request_sha, response_sha, patch, validator_result, usage_if_reported, latency}`.
- The **canonical outcome summary** (scene hash, tags, successes/N) is what reproducibility checks compare. Raw logs will differ.

**Folders (proposed):**
```
worldsmith/
  grammar/ (schema.json, bounds.yaml, templates/*.luau)
  validator/  compiler/  runner/ (mcp client, attempts, classifier)
  architect/ (prompt, fixtures/, client)   baselines/ (random_valid.py, authored/)
  scenes/ (F1-A, F1-E, …)   bundles/   viewer/ (static HTML)   tests/
  docs/ (PLAN.md, ADR/, TICKETS.md, HANDOFF.md)
```
- All unit tests run on CPU without Studio.
- Live tiers make **local** Studio MCP calls on the Mac.
- Architect calls go to Claude, and P2 calls Jev's hosted service.
- No GPU, CUDA or training.

## 12. Experiments and baselines
| ID | Patch source | Role and budget |
|---|---|---|
| B0 | None (the original fixture) | N attempts |
| B1 | **Hand-authored fix** | A **one-patch positive control**, not an equal-budget adaptive competitor |
| B2 | **Capped random valid sampler** (same op set) | At most **3 raw proposals per episode, including validator rejects**. No unlimited resampling. Its failures are reported. |
| B3 | **Claude architect** (fixed prompt, model role, recorded) | At most 3 revisions (rejects count) |

- **B2 vs B3** use equal player tier, N, `T_attempt`, edit limits and revision budget. Model call counts are *reported*, not forced equal (random needs zero calls).
- **P2 run caps per episode:** at most `N × (1 + max_revisions)` = 3 × 4 = **12 attempts**, plus at most 3 architect calls, plus a declared wall-clock cap.
- **Metrics:**
  - repaired (3/3 after the final revision, constraints held);
  - revisions and rejections;
  - attempts used;
  - constraint margins.
- **Claims:**
  - A handful of hand-authored fixtures support a **functional demo** only.
  - **Scientific generalisation** needs held-out rooms and seeds (e.g. 10–20 procedurally broken variants [ESTIMATE]), frozen before the architect prompt is tuned.
  - Success is mechanical completion, not enjoyment.

## 13. Resource and role matrix
| Resource | Role | Required? |
|---|---|---|
| Mac (chip unconfirmed) with Roblox Studio and its MCP server on | Live builds and runs, stills, recording | Yes, for live tiers |
| Claude Code (subscription or Console/API credits, whichever auth is active) | Coding, tests, docs, architect calls, reviews | Yes |
| Autopilot staging at the pinned commit | Connector and player | Yes |
| Aura account or TypeSafe key (Jev) | P2 decisions, optional Jev Image critic | P2 only, after approval |
| Cursor | Alternative IDE | Optional |
| ChatGPT Plus / Grok Bot | Second-opinion review, research | Optional |
| Jev Image | Optional critic. A presence Noul on a still ("is the lever visible from spawn?") **before** any Box, which always returns a rectangle. Advisory only, never decides geometry. Stills resized to 280–1568 px per side. | M3 optional |
| fal.ai | Decorative `Decor` props only (e.g. TRELLIS-2 GLB), with collision by proxy primitives | Optional, paid, needs approval |
| DGX Spark (GB10, 128 GB unified memory) | Not needed. A possible later local critic after an ARM64 probe. | No |
| Atlas (World Labs) | No access assumed. An optional later concept-still comparison. | No |
| PlayCanvas (MIT) | Optional browser viewer | Optional |

**Paid live requirements:**
- P2 needs Jev usage via Aura or TypeSafe.
- Architect calls use Claude usage on the active auth route.
- fal and Atlas are separate balances.
- M0 makes **no paid runtime calls**: P0 player, architect in fixture mode. Claude Code is used for coding on the active auth route, and Studio MCP calls are local.

## 14. Milestones and gates [ESTIMATE; conditional on the Studio MCP route working live]
| M | Scope | Timebox | Acceptance (pre-declared) | Kill / fallback |
|---|---|---|---|---|
| **M0 Mechanism probe** | WS-01..WS-04: grammar, compiler, F1-A + fix, P0 controller | ~48 h of effort | Live Studio, the same P0 controller hash, N=3. F1-A shows `FELL` near the bridge **3/3**. The bridge-length fix shows `BRIDGE_CROSSED` + `GOAL_TOUCHED` **3/3**. Rebuild read-back matches. | Inconsistent → stop and debug, without changing the threshold. Build or read-back unreliable → fix the compiler first. **If live MCP is the bottleneck,** report it, keep recorded-fixture tests, and do **not** substitute a fictional engine or claim Roblox results from a simulator. |
| **M1 Validator + taxonomy** | WS-05..WS-08, more F1 fixtures | 3–5 days | The abuse suite (§16) is all rejected. The classifier matches the fixtures, with hypotheses only where §10 allows. | Ambiguity → add instrumentation, not heuristics |
| **M2 Architect + baselines** | WS-09..WS-11: B1–B3 on P0, then P2 if approved and its freeze gate passes | 4–6 days | Results files with successes/N per condition, within budget caps | Architect ≈ capped random → report it. Gaming the constraints → tighten the grammar. P2 can't freeze cleanly → report it and stay on P0. |
| **M3 Demo + release** | WS-12..WS-13 | 2–4 days | Clean-clone CPU tests pass. The clip is made from real bundles. | A claim without a file → cut |

Narrow MVP: **roughly 2–3 weeks of focused effort** [ESTIMATE]. Not a guarantee.

## 15. Tickets (do not file; create in the approved repo only)

Fresh-session workflow per ticket:
1. Read `PLAN.md`, the ticket and `HANDOFF.md`.
2. Write the tests first.
3. Implement.
4. Run the CPU tests, plus the live check if the ticket says so.
5. Append a handoff note and stop.

| ID | Title | Depends | Acceptance |
|---|---|---|---|
| WS-01 | Skeleton, licence notes, pin the Autopilot staging commit | — | Tests run. `deps.md` records the commit and licences. |
| WS-02 | Complete F1 scene schema, bounds, canonical hash, valid F1-A/F1-E files | 01 | Round-trip and hash-stability tests. Unknown keys are rejected. The fixtures validate. |
| WS-03 | Template compiler + Edit-mode rebuild + read-back + event templates | 02 | Luau snapshot tests. **Live:** F1-A builds and read-back matches. |
| WS-04 | P0 controller (route frozen per family) + attempt runner + bounded polling | 03 | **Live:** the M0 gate in §14 (3/3 each, same controller hash). Polling and timeouts are logged. |
| WS-05 | Automated patch apply (same bridge-only restriction as M0) + base-hash check | 02 | A stale base is rejected. Any other op is rejected in M0. |
| WS-06 | Validator: schema, protection, locked gap, difficulty, structure | 05 | The §16 abuse suite is all rejected and legit patches pass |
| WS-07 | Navigation approximation (`GRAPH_CONNECTED/DISCONNECTED`) | 06 | Fixture tests. Documented as an approximation, never a proof. |
| WS-08 | Event classifier + hypothesis rules (§10) | 04 | Recorded traces classified correctly. Unsupported cause → UNCLASSIFIED. |
| WS-09 | Capped random valid sampler | 06 | ≤3 raw proposals including rejects. Seeded. |
| WS-10 | Architect client: JSON-only output, fixture mode, budget guard | 06,08 | Fixture replay is deterministic. A malformed output counts as a rejected revision. No live call without approval config. |
| WS-11 | Orchestrator + bundles + P2 freeze manifest check | 04,08,09,10 | Episode caps enforced (12 attempts, 3 architect calls). P2 refuses to run on a manifest-hash mismatch or with `evolve` enabled. Resumable. |
| WS-12 | Static bundle viewer | 11 | Renders the sample bundle. No secrets. |
| WS-13 | Release: README, limits card, failure gallery, reproduce script | 11,12 | A clean clone reproduces the CPU tests and the recorded summaries |

## 16. Tests that matter
- **Abuse suite:** the validator must reject each of these:
  - moving the goal or spawn;
  - changing the gap or platforms;
  - adding a ramp, floor or teleport;
  - deleting the gate or making it permanently open;
  - changing player physics;
  - any Luau or arbitrary executable/free-text payload (schema enums and entity IDs remain valid strings);
  - out-of-bounds values;
  - more than 3 ops;
  - removing a protected entity;
  - a non-bridge op in M0;
  - success without `BRIDGE_CROSSED`.
- **Transactions:** an interrupted build rolls back. Read-back mismatch injection is detected.
- **Bounds:** an attempt exceeding `T_attempt` ends as `TIMEOUT`. A hung MCP call times out. There are no unbounded loops.
- **Determinism (CPU):** compiler output, hashes and the sampler are identical for the same seed.
- **Replay staleness:** `regress` on a changed scene hash is labelled STALE.
- **Freeze:** the P0 controller hash is identical before and after a patch, and P2 runs only with a matching manifest.
- **Console parsing:** foreign lines and a wrong nonce are ignored.
- **Secrets:** a secret scan passes. Keys come from env or a vault that you set up.
- **Cancellation:** Ctrl-C stops play and leaves a valid, resumable bundle.

## 17. Safe defaults
- No paid runtime calls until you approve a budget.
- No publishing or pushing.
- No hardware.
- No changes to the Autopilot repo, your JzLab repos, or existing plans and schedules.
- `AUTOPILOT_SHARE=0` for any live Autopilot run.
- Owned room assets only.
- No Voldex or NFL material.

## 18. Sources (checked 7 Nov 2026, PKT)
- **Autopilot:**
  - [ramenvr/autopilot repo](https://github.com/ramenvr/autopilot) (Apache-2.0)
  - [staging README](https://github.com/ramenvr/autopilot/blob/staging/README.md) (Claude Code use, Aura vs TypeSafe key)
  - [Roblox MCP route doc](https://github.com/ramenvr/autopilot/blob/staging/docs/roblox-mcp.md) (tool mapping, limits, latency, test place, run status)
  - [connector doc at merge commit 53a41f6](https://github.com/ramenvr/autopilot/blob/53a41f6a9c34d0434cd50f70eb528d4d74ec0e36/autopilot/engines/roblox-mcp.md)
  - [PR #40 roblox-mcp](https://github.com/ramenvr/autopilot/pull/40) · [PR #38 regress](https://github.com/ramenvr/autopilot/pull/38) · [PR #10 native plugin (other author)](https://github.com/ramenvr/autopilot/pull/10)
- **Jev (TypeSafe):** [models](https://docs.typesafe.ai/models) · [image input](https://docs.typesafe.ai/images) · [Box primitive](https://docs.typesafe.ai/primitives/box) · [System One API](https://docs.typesafe.ai/api/system-one)
- **Optional branches:** [fal TRELLIS-2 docs](https://fal.ai/models/fal-ai/trellis-2/llms.txt) · [Atlas beta quickstart](https://docs.atlas-beta.worldlabs.ai/quickstart.md) · [PlayCanvas engine](https://github.com/playcanvas/engine)
- **Inspiration only:** [Mirage Tesseract post](https://x.com/trymirage/status/2118749777814774248) · [Tesseract repo](https://github.com/mirage-hq/Tesseract) (proprietary) · [Vork repo](https://github.com/voyager-so/vork) (source-available)
