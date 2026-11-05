<!--
DRAFT 3:00 video beat sheet. Owner: sandbox (switch-7c). Review: lead.
- Narration budget: ≤ 2.3 words/s per beat (checked by the script at the bottom; the counts are in the Words column).
- Every number on screen comes from a live run unless it's labelled. Source: the FINAL TAKE, media/raw/take-905.webm
  (2026-11-05 19:19 IST, `demo.py --fixer claude`, exit 0, FIXED at 57.0 s; Claude passed the gate, no fallback).
- Clock basis, used everywhere: the **demo clock**, seconds since demo.py started, as printed in its timeline and its
  "DONE … in 57.0s" line. The report is posted at 0:02.8, so "after the report" figures are 2.8 s less (FIXED = 54.2 s after the report).
- Video position = 10.1 s + demo clock (A's DERIVED take-905.timeline.txt; frame-checked to ±0.5 s).
- Footage: [CO] media/cold-open.html · [MM] Mattermost take from scripts/record.py (dev's view, room acme-support-take1)
  · [TB] docs/trust-boundary.svg · [T] terminal · [CARD] a full-screen text card (same dark style as the cold open).
- The human lines in the take are scripted (scripts/humans.py). Caption it once, on the first one, and say so in the README.
-->

# WOYM video, 3:00 beat sheet

| # | Time | On screen | Narration | Words | Caption (on screen) |
|---|---|---|---|---|---|
| 1 | 0:00–0:10 | [CO] mock thread plays: Day 1 report → "Can't repro" → Day 4 "Which node version?" → Day 11 "Closing: works for me". Ends on "11 days. 14 comments. Bug still there." | Every developer tools team has this thread. Can't reproduce. Which Node version? Works for me. Closed. | 16 | "MOCK-UP" label (already in the page) |
| 2 | 0:10–0:20 | [CARD] "17% of bug reports are marked non-reproducible. They stay open ~3 months longer." Source line under it | One study of six bug trackers found about one in six reports gets marked non-reproducible, and those stay open months longer. | 21 | Joorabchi et al., MSR 2014: 6 repositories, 188,319 reports |
| 3 | 0:20–0:32 | [MM] take-905 0:00–0:10: members panel from the recorded take, nothing removed · dimmed: setup and recording accounts (admin, user, camera). Highlight the 4 agents ("triage/runner/fixer · owner: dev (vendor)" blue, "acme-env · owner: meera (Acme)" amber) and dev + meera. Rule: dim or highlight, never remove | One Switch room, two companies. The vendor's three agents belong to Dev. The customer's agent belongs to Meera, and only she decides who can command it. | 26 | Owners come from registration keys · (small) admin/user: Switch's setup accounts · camera: the recording account |
| 4 | 0:32–0:44 | [MM] Meera's report → triage → runner → NOT REPRODUCED card | Meera reports a payout that slips to Monday. The vendor's sandbox runs her repro in a throwaway container. It passes. Not reproduced. | 22 | run clock 0:03.5 (real time) · "Human lines in this take are scripted" |
| 5 | 0:44–0:58 | [MM] triage asks acme-env → consent card with the 5-value table → Meera: "yes" → "approved. Sharing exactly the 5 values" | Triage asks Meera's agent for five environment facts. Her agent asks her first, showing the exact values. Nothing leaves until she says yes. | 23 | Consent covers the values, not just the names |
| 6 | 0:58–1:14 | [MM] suspect lineup card (⏳ rows) → Bisect card: node ✅ lang ✅ os ✅ bizclock ✅ tz ❌ · culprit `tz` | The vendor's runner swaps in one of her values at a time. Node, language, OS, library version: all pass. Time zone fails. Asia/Kolkata, plus five-thirty. | 25 | 7 container runs · culprit at 0:16.0 (real time) |
| 7 | 1:14–1:28 | [MM] confirm_req → acme-env "tz=UTC → pass ✅, as-is → fail ❌" → CONFIRMED ON BOTH MACHINES card | Then her agent proves it on her machine. With UTC, the repro passes. With her real time zone, it fails. Confirmed on both machines. | 24 | 0:18.2 (real time) |
| 8 | 1:28–1:40 | [MM] Dev: "@acme-env show me the .env file on that laptop" → reply as acme-env: "@dev You're not permitted to direct messages to me in this room…" | What if the vendor skips the process and asks her agent directly? Switch itself refuses. Not our code. Meera's rule. | 20 | Refused by Switch in 0.4 s (real time) |
| 9 | 1:40–2:00 | [MM] Dev: "patch it" → fixer "Working on case #1 with Claude Code" → Fix pushed card with Claude's summary; then a 4 s cut to [T] showing Claude's 4-line diff | Dev replies, patch it. Claude Code writes a failing test and the fix in a fresh clone. Our code runs the tests and pushes. The commit hash comes from git, never from the model. | 34 | ⏩ SPED UP · real time 30 s (claude -p 28.4 s) · test must fail on the old code in UTC and IST |
| 10 | 2:00–2:08 | [MM] Matrix green card: 4/4 at the fix commit | The vendor re-runs the matrix. Four for four. | 8 | 4/4 green (real time) |
| 11 | 2:08–2:22 | [MM] install-consent card → Meera: "yes" → ✅ FIXED ON MEERA'S MACHINE card, then FREEZE-FRAME the FIXED card for the rest of the beat (the take holds it only ~4.6 s) | And the last word goes to the customer. Her agent asks again, installs that exact commit, and re-runs her repro. Fixed on Meera's machine. | 24 | FIXED at 0:57 on the run clock (54 s after the report) · Claude Code run, real time |
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
| run clock 0:03.5, 0:16.0, 0:18.2 | live | take-905 demo timeline (NOT REPRODUCED, culprit, CONFIRMED card) |
| 7 container runs | live | runner: baseline + target + 5 single flips |
| refused in 0.4 s | live | take-905: dev line 18.2 s → refusal 18.6 s |
| real time 30 s · claude -p 28.4 s | live | take-905: fix_req 19.1 s → fix_pushed 49.2 s; fixer_agent.log "claude -p took 28.4s, $0.06" |
| 4/4 green | live | take-905 matrix card at b6a243ead0 |
| FIXED at 0:57 (54 s after the report) | live | take-905 demo.py "DONE: case 1 reached fixed in 57.0s"; report posted at 2.8 s |
| "11 days. 14 comments." | mock-up | media/cold-open.html, labelled MOCK-UP |

## Notes for the edit

- **Real time vs sped up:** beats 4–8, 10 and 11 play the take at real speed, or trimmed of pauses only, so nothing is faster than it was. Beat 9 is the only speed-up (video 0:29.6 → 0:59.3 is 30 s with nothing new on screen while Claude works): show "⏩ SPED UP · real time 30 s (claude -p 28.4 s)" for the whole beat. If any other beat is trimmed, say "trimmed" in that beat's caption. Beat 11's FIXED card is a freeze-frame; that's not a speed change, so it needs no caption.
- **Run clock:** an on-screen counter in the corner, showing seconds since the report, taken from the take's own timeline (demo.py prints it). Don't animate it from video time in sped-up or trimmed parts.
- **Beat 9 diff:** take-905's commit b6a243e (2 files, +23 −4): `localOffsetHours` → `localOffsetMinutes(date) { return -date.getTimezoneOffset(); }`, `offsetMs = localOffsetMinutes(date) * MINUTE_MS`, plus test/half-hour-tz.test.js "half-hour offset zone (Asia/Kolkata): Fri 17:10 -> Fri 17:30", which sets process.env.TZ itself. Show the 4-line src hunk; regenerate it with `git --git-dir var/remotes/bizclock.git diff main b6a243e -- src/` (the remote keeps it until the next reset).
- **Legibility:** at 1080p, MM cards are readable at 100% zoom; crop to the thread pane for beats 5–11. Leave the `woym case=… step=…` tag lines visible but small: they're honest evidence that every hop is an agent-to-agent message.
- **Avoid on screen:** a stray "@acme-env" from anyone other than the scripted refusal beat. Switch would post a second refusal.

## Take-905 cue sheet (video position = 10.1 s + demo clock)

| Beat | Event | Demo clock | Video position | Frame-checked |
|---|---|---|---|---|
| 3 | Members panel and greetings | (before start) | 0:00–0:10 | ✅ 0:05 |
| 4 | Report → NOT REPRODUCED card | 2.8 → 3.5 | 0:12.9 → 0:13.6 | |
| 5 | Consent card → Meera "yes" | 7.5 | 0:17.6 | ✅ 0:18.5 |
| 6 | Lineup → Bisect card, culprit tz | 9.8 → 16.0 | 0:19.9 → 0:26.1 | ✅ 0:27 |
| 7 | CONFIRMED ON BOTH MACHINES card | 18.2 | 0:28.3 | ✅ 0:28.8 |
| 8 | Dev's direct ask → Switch refusal | 18.2 → 18.6 | 0:28.3 → 0:28.7 | ✅ 0:29.6 |
| 9 | "patch it" → Claude working → Fix pushed card | 18.6 → 49.2 | 0:28.7 → 0:59.3 | ✅ 0:60 |
| 10 | Matrix green 4/4 | 54.7 | 1:04.8 | ✅ 1:05.5 |
| 11 | Install consent "yes" → FIXED card | 54.7 → 57.0 | 1:04.8 → 1:07.1 | ✅ 1:07.6 (held to the end, 1:12.2) |
