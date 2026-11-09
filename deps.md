# Dependencies and licences

Worldsmith is licensed **Apache-2.0** (see `LICENSE` and `NOTICE`).
Recorded for WS-01 on 9 Nov 2026 (PKT).

## Pinned upstream: Autopilot

| Field | Value |
| --- | --- |
| Repository | [`ramenvr/autopilot`](https://github.com/ramenvr/autopilot) |
| Branch | `staging` |
| **Pinned commit** | **`53a41f6a9c34d0434cd50f70eb528d4d74ec0e36`** |
| What that commit is | The merge of [PR #40 (roblox-mcp)](https://github.com/ramenvr/autopilot/pull/40), by JehanzebAli11, merged into `staging` on 2026-11-07 05:09 PKT |
| Verified | By the control thread on 9 Nov 2026 against upstream `staging` (the commit is on `staging`'s history) |
| Licence | Apache-2.0 |
| How Worldsmith uses it | As an **external dependency**: the Roblox Studio MCP connector and the player. It is **not vendored**, and **no Autopilot code is copied** into this repository. |

Context only (earlier merges into the same `staging` branch; not separately pinned, and their
ancestry relative to the pinned commit was not re-checked in WS-01):

| PR | Name | Commit | Merged into `staging` |
| --- | --- | --- | --- |
| [#38](https://github.com/ramenvr/autopilot/pull/38) | regress (replay of CONFIRMED episodes) | `af1e32b` | 2026-11-07 04:48 PKT |
| [#39](https://github.com/ramenvr/autopilot/pull/39) | evolve | `68ab39b` | 2026-11-07 04:48 PKT |

Worldsmith freezes the player and changes the world; it does not use or extend `evolve` (SPEC §2).

## Python runtime dependencies

None at WS-01.

## Python development dependencies (CPU tests)

Installed by `scripts/test.sh` via `pip install -e ".[dev]"`. Versions are those resolved on
9 Nov 2026 (Python 3.10.12); licences are as declared in each package's metadata.

| Package | Version | Licence | Why |
| --- | --- | --- | --- |
| pytest | 9.2.0 (pinned) | MIT | Test runner |
| pluggy | 1.7.0 | MIT | pytest dependency |
| iniconfig | 2.3.1 | MIT | pytest dependency |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | pytest dependency |
| Pygments | 2.21.0 | BSD-2-Clause | pytest dependency |
| exceptiongroup | 1.3.1 | MIT | pytest dependency, Python < 3.11 only |
| tomli | 2.5.0 | MIT | pytest dependency, Python < 3.11 only |
| typing_extensions | 4.16.0 | PSF-2.0 | exceptiongroup dependency, Python < 3.11 only |

Build backend (build time only): setuptools >= 77 (MIT).

## Not dependencies (inspiration only)

These are named in SPEC §2 as inspiration. They are **not dependencies**, and no code, assets or
text from them are used:

- [Mirage Tesseract](https://github.com/mirage-hq/Tesseract): proprietary.
- [Vork](https://github.com/voyager-so/vork): source-available.

## Services (later tickets, not used at WS-01)

Roblox Studio with its MCP server (live tiers, on the Mac), Claude (architect calls), and Jev via
an Aura account or TypeSafe key (P2 only, after approval). See SPEC §13. None are called by the
CPU tests.
