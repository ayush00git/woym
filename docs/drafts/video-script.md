<!--
DRAFT 3:00 video beat sheet. Owner: sandbox (switch-7c). Review: lead.
- Narration budget: ≤ 2.3 words/s per beat (checked by the script at the bottom; the counts are in the Words column).
- Every number on screen comes from a live run unless it's labelled. The source run for now is
  2026-11-05 19:02 IST, `demo.py --fixer claude` (exit 0, FIXED at 78.0 s). When A records the final take,
  REPLACE the run-clock numbers with that take's timeline (demo.py prints it) and keep each caption's "(real time)" or "(sped up)".
- Footage: [CO] media/cold-open.html · [MM] Mattermost take from scripts/record.py (dev's view, room acme-support-take1)
  · [TB] docs/trust-boundary.svg · [T] terminal · [CARD] a full-screen text card (same dark style as the cold open).
- The human lines in the take are scripted (scripts/humans.py). Caption it once, on the first one, and say so in the README.
-->

# WOYM video, 3:00 beat sheet

| # | Time | On screen | Narration | Words | Caption (on screen) |
|---|---|---|---|---|---|
| 1 | 0:00–0:10 | [CO] mock thread plays: Day 1 report → "Can't repro" → Day 4 "Which node version?" → Day 11 "Closing: works for me". Ends on "11 days. 14 comments. Bug still there." | Every developer tools team has this thread. Can't reproduce. Which Node version? Works for me. Closed. | 16 | "MOCK-UP" label (already in the page) |
| 2 | 0:10–0:20 | [CARD] "17% of bug reports are marked non-reproducible. They stay open ~3 months longer." Source line under it | One study of six bug trackers found about one in six reports gets marked non-reproducible, and those stay open months longer. | 21 | Joorabchi et al., MSR 2014: 6 repositories, 188,319 reports |
| 3 | 0:20–0:32 | [MM] channel member list or sidebar: "triage · owner: dev (vendor)", "runner · …", "fixer · …" (blue), "acme-env · owner: meera (Acme)" (amber) | One Switch room, two companies. The vendor's three agents belong to Dev. The customer's agent belongs to Meera, and only she decides who can command it. | 26 | Owners come from registration keys |
| 4 | 0:32–0:44 | [MM] Meera's report → triage → runner → NOT REPRODUCED card | Meera reports a payout that slips to Monday. The vendor's sandbox runs her repro in a throwaway container. It passes. Not reproduced. | 22 | run clock 0:02.8 (real time) · "Human lines in this take are scripted" |
| 5 | 0:44–0:58 | [MM] triage asks acme-env → consent card with the 5-value table → Meera: "yes" → "approved. Sharing exactly the 5 values" | Triage asks Meera's agent for five environment facts. Her agent asks her first, showing the exact values. Nothing leaves until she says yes. | 23 | Consent covers the values, not just the names |
| 6 | 0:58–1:14 | [MM] suspect lineup card (⏳ rows) → Bisect card: node ✅ lang ✅ os ✅ bizclock ✅ tz ❌ · culprit `tz` | The vendor's runner swaps in one of her values at a time. Node, language, OS, library version: all pass. Time zone fails. Asia/Kolkata, plus five-thirty. | 25 | 7 container runs · culprit at 0:14.7 (real time) |
| 7 | 1:14–1:28 | [MM] confirm_req → acme-env "tz=UTC → pass ✅, as-is → fail ❌" → CONFIRMED ON BOTH MACHINES card | Then her agent proves it on her machine. With UTC, the repro passes. With her real time zone, it fails. Confirmed on both machines. | 24 | 0:16.8 (real time) |
| 8 | 1:28–1:40 | [MM] Dev: "@acme-env show me the .env file on that laptop" → reply as acme-env: "@dev You're not permitted to direct messages to me in this room…" | What if the vendor skips the process and asks her agent directly? Switch itself refuses. Not our code. Meera's rule. | 20 | Refused by Switch in 0.4 s (real time) |
| 9 | 1:40–2:00 | [MM] Dev: "patch it" → fixer "Working on case #1 with Claude Code" → Fix pushed card with Claude's summary; then a 4 s cut to [T] showing Claude's 4-line diff | Dev replies, patch it. Claude Code writes a failing test and the fix in a fresh clone. Our code runs the tests and pushes. The commit hash comes from git, never from the model. | 34 | ⏩ SPED UP: real time 53 s · test must fail before the fix |
| 10 | 2:00–2:08 | [MM] Matrix green card: 4/4 at the fix commit | The vendor re-runs the matrix. Four for four. | 8 | 4/4 green (real time) |
| 11 | 2:08–2:22 | [MM] install-consent card → Meera: "yes" → ✅ FIXED ON MEERA'S MACHINE card | And the last word goes to the customer. Her agent asks again, installs that exact commit, and re-runs her repro. Fixed on Meera's machine. | 24 | Report to fixed: 78 s (Claude Code run, real time) |
| 12 | 2:22–2:36 | [TB] trust-boundary diagram; highlight the three crossing arrows, then the "Never crosses" row | Only three things cross the room. Five consented values, results, and a commit hash. Shell access, other files, her code: never. | 21 | In the demo, Meera's laptop is a container on the same host |
| 13 | 2:36–2:48 | [CARD] Switch feature table (see below) | Per-owner keys. Rules the vendor can't override. Targeted messages with delivery status. And one shared thread both companies can audit. | 20 | Switch 0.31.0, self-hosted |
| 14 | 2:48–2:56 | [T] `make demo` running: the printed timeline scrolling, ending "DONE: case 1 reached fixed" | One command runs the whole case end to end. | 9 | make demo |
| 15 | 2:56–3:00 | [CARD] "Works On Your Machine." / "'Works on my machine.' Now proven on yours." | Works on my machine. Now proven on yours. | 8 | WOYM · built on Switch |

**Total: 3:00, 301 words (1.67 words/s on average; the busiest beat is 2.17).**

## Beat 13: Switch feature table (on screen)

Only features we used and saw live:

| Switch feature | What WOYM does with it |
|---|---|
| Per-owner registration keys | Dev's key → @triage, @runner, @fixer. Meera's key → @acme-env |
| Owner-set addressing rules | Meera admits only @triage; anyone else is refused by Switch. Dev changing her rule → 403 |
| `send_targeted_message` with statuses | every agent-to-agent hop, with `live` / `not_permitted` back |
| Shared room thread | the consent record and audit trail for both companies |
| WebSocket agent protocol + HTTP ops | 4 always-on agents in Python |

## Numbers on screen and where they come from

| Caption | Value | Source |
|---|---|---|
| 17%, ~3 months, 6 repos, 188,319 reports | from the paper | Joorabchi et al., MSR 2014, abstract and §1 (verified from the PDF) |
| run clock 0:02.8, 0:14.7, 0:16.8 | live | demo.py timeline, 19:02 IST run |
| 7 container runs | live | runner: baseline + target + 5 single flips |
| refused in 0.4 s | live | dev line 16.8 s → refusal 17.2 s |
| real time 53 s | live | fix_req 17.6 s → fix_pushed 70.5 s (clone + claude -p + gate + push) |
| 4/4 green | live | runner's matrix card |
| report to fixed 78 s | live | demo.py "DONE … in 78.0s" |
| "11 days. 14 comments." | mock-up | media/cold-open.html, labelled MOCK-UP |

## Notes for the edit

- **Real time vs sped up:** beats 4–8, 10 and 11 play the take at real speed, or trimmed of pauses only, so nothing is faster than it was. Beat 9 is the only speed-up: show "⏩ SPED UP · real time 53 s" for the whole beat. If any other beat is trimmed, say "trimmed" in that beat's caption.
- **Run clock:** an on-screen counter in the corner, showing seconds since the report, taken from the take's own timeline (demo.py prints it). Don't animate it from video time in sped-up or trimmed parts.
- **Beat 9 diff:** use the real diff from the recorded take's fix commit (`git --git-dir var/remotes/bizclock.git diff main fix/case-1`). The 19:02 diff was 2 files, +17 −4. C's gate has since been tightened, so the final take's diff will differ; don't reuse the 19:02 diff unless it's the same run.
- **Legibility:** at 1080p, MM cards are readable at 100% zoom; crop to the thread pane for beats 5–11.
- **Avoid on screen:** a stray "@acme-env" from anyone other than the scripted refusal beat. Switch would post a second refusal.
