# Issues log

Every failure, gap, wrong assumption or workaround goes here, as it happens. Nothing gets hidden or softened: a blocked task or a fallback is logged even if it was worked around.

Append only. Add entries with a heredoc and never rewrite someone else's entry. To update an issue, append a new entry that references the old one's title.

```bash
cat >> ISSUES.md <<'EOF'

## [2026-11-05 19:10 IST] <who> | <short title>
- **Status:** open | worked-around | fixed | blocked | wontfix
- **What happened:** ...
- **Evidence:** exact command and error text, file:line, or doc URL
- **Impact:** what it breaks, or what score or claim it weakens
- **Workaround / next step:** ...
EOF
```

`<who>` is one of: lead, platform (switch-ba), sandbox (switch-7c), agents (switch-90).

---

## [2026-11-05 18:30 IST] lead | Known risks carried over from ideation (not yet verified)
- **Status:** open
- **What happened:** Research found several unverified assumptions behind the plan:
  1. Cross-owner `send_targeted` works once Meera sets a custom rule. This is documented but untested.
  2. The cross-owner invite op is undocumented.
  3. Linking a Mattermost account to a Switch user is Console-only.
  4. Thread replies keep the root's `thread_id`.
  5. `claude -p` runs non-interactively from a subprocess here.
  6. Node on alpine honours `TZ=Asia/Kolkata`.
- **Evidence:** `.hack/ideas/builder-r3.md`, `.hack/decision.md` (Fallbacks section)
- **Impact:** If item 1 fails, Use of Switch falls to about 7/10 and the pitch changes from "Switch-enforced" to "owner-scoped".
- **Workaround / next step:** Kill test A2 tonight; B3 checks the alpine TZ; C2 checks `claude -p`.

## [2026-11-05 18:45 IST] agents (switch-90) | `claude -p` non-interactive check (risk 5): works
- **Status:** fixed (verified)
- **What happened:** `claude -p` (Claude Code 2.1.318) runs non-interactively from `subprocess.run(..., stdin=DEVNULL)`. No `--permission-mode` needed: tools in `--allowedTools` run unattended, and tools not on the list are refused (recorded in `permission_denials` in the JSON output) without hanging.
- **Evidence:**
  - Read test: `claude -p "Read a.txt and reply with only its contents." --allowedTools Read --output-format json` → rc 0, wall 10.4 s, default model opus-5-5, $0.25 (30k cache-creation tokens from user plugins/hooks).
  - Edit test: `claude -p "In b.py change 1 to 2. Reply 'done'." --allowedTools Read,Edit --model sonnet --output-format json --strict-mcp-config --setting-sources project` → rc 0, wall 15.0 s, $0.04, file edited. The model first tried Bash `sed`, which was denied, then used Edit.
- **Impact:** None on the plan. Cost note: without `--model sonnet --strict-mcp-config --setting-sources project`, every call loads the user's plugins/MCP config (about 6x the cost).
- **Workaround / next step:** ClaudeFixer will use `--model sonnet --strict-mcp-config --setting-sources project --output-format json`, with a timeout of 300 s. `Bash(node *)` patterns are untested until the bizclock fixture lands.

## [2026-11-05 19:00 IST] sandbox (switch-7c) | Verified: node alpine and bookworm honour TZ=Asia/Kolkata (risk 6 closed)
- **Status:** fixed (risk closed; nothing to work around)
- **What happened:** `getTimezoneOffset()` is -330 on node:22-alpine, node:20-alpine, node:20-bookworm-slim and node:22-bookworm-slim. Node ships its own ICU time zone data, so alpine needs no tzdata package.
- **Evidence:** `docker run --rm --network none -e TZ=Asia/Kolkata node:22-alpine node -e "console.log(new Date().getTimezoneOffset(), Intl.DateTimeFormat().resolvedOptions().timeZone)"` printed `-330 Asia/Calcutta`; the other three images printed the same.
- **Impact:** None on the plan. Note that `Intl` reports the ICU canonical name `Asia/Calcutta`, not `Asia/Kolkata`, so cards should show the TZ env value, not Intl's name. A warm run with all the hardening flags takes about 0.22 s; a cold pull takes about 6 s per image.
- **Workaround / next step:** none needed.

## [2026-11-05 19:10 IST] sandbox (switch-7c) | Intl reports Asia/Kolkata as "Asia/Calcutta"
- **Status:** worked-around
- **What happened:** In node, `Intl.DateTimeFormat().resolvedOptions().timeZone` returns the ICU canonical alias `Asia/Calcutta` when TZ=Asia/Kolkata.
- **Evidence:** see the entry above ("Verified: node alpine and bookworm honour TZ=Asia/Kolkata").
- **Impact:** A probe that read the zone from Intl would show "Asia/Calcutta" on the lineup card and wouldn't match the customer's real setting.
- **Workaround / next step:** `DockerCustomerMachine.probe()` reads `tz` from the TZ env var (`printenv TZ`), never from Intl.

## [2026-11-05 19:20 IST] agents (switch-90) | Hop limit 12 is lower than the §3 flow needs (13)
- **Status:** open (needs a lead decision)
- **What happened:** Counting agent→agent targeted sends in docs/contracts.md §3 (steps 2,3,5,8,10,11,12,13,15,16,17,18,19) gives 13 hops for one full M2 case. `config.MAX_HOPS_PER_CASE = 12`, so the guard would stop the case at step 19 (verify_req). If posts to the room/human (steps 4,6,20) also count, it's 16.
- **Evidence:** `tests/case/test_state.py::test_full_flow_agent_hops_vs_limit` shows it (13th hop raises GuardError at 12, passes at 13).
- **Impact:** M2 would stop just before FIXED ON MEERA'S MACHINE.
- **Workaround / next step:** Proposed `MAX_HOPS_PER_CASE = 16`. Until then the per-agent `SendGuard` caps each agent at 12 (triage sends only 7), and triage's case-wide counter takes `max_hops` from config.

## [2026-11-05 19:20 IST] platform (switch-ba) | Starting the Switch stack blocked by the permission checker
- **Status:** blocked
- **What happened:** `./infra/switch/up.sh` (docker compose up of the published ghcr.io/sandbox-quantum images, 0.31.0) was refused by the Claude Code auto-mode permission checker ("Code from External"). Images are already pulled.
- **Evidence:** tool denial on `cd infra/switch && ./up.sh`
- **Impact:** A2 kill test can't run until the stack is up. Everything else for A1/A2 is written.
- **Workaround / next step:** the user starts it once with `! ./infra/switch/up.sh` from the repo root (or allows it). Then `uv run python scripts/killtest/killtest.py`.

## [2026-11-05 19:20 IST] platform (switch-ba) | 0.31.0 docs describe SSE + client heartbeat; source also has the WebSocket
- **Status:** open (source-verified, not yet run)
- **What happened:** `docs/official/internals/agent-protocol.md` at tag switch-v0.31.0 describes `GET /agents/{id}/events` (SSE) + `POST /connection/beat` every 2 s. The same tag's source also serves `WS /agents/{id}/connection/ws` (server sends `{"event":"ping"}` every interval, client answers `{"type":"pong","cursor":n}`; refusals come as a `refused` frame then close code 4000+status). Registration is `POST /agents` (raw, `connector_type:"custom"` + `integration_profile`) or `POST /agents/register-known` (known types only), both with a registration key as Bearer.
- **Evidence:** core/switch_core/bridges/agent/api/handlers.py:1178 (connection_socket), :186 (register), :286 (register-known) at tag switch-v0.31.0
- **Impact:** none if the WS works on the live server; the client will use WS.
- **Workaround / next step:** kill test confirms on the live stack.

## [2026-11-05 19:20 IST] platform (switch-ba) | Assumptions in the brief that the 0.31.0 source contradicts or refines
- **Status:** open (source-verified, not yet run)
- **What happened:**
  1. Linking a Mattermost account to a Switch user is NOT Console-only: `POST /gateway/collaborations/{bridge_id}/identities` {external_user_id, username, user_id:null} as the signed-in user (gateway/collaborations.py:815).
  2. Addressing rules can name humans by participant id (`users: [...]`) and agents by agent UUID (`agents: [...]`), plus symbolic `owner` / `owner_agents`. Set by owner/admin with `PUT /gateway/agents/{id}/addressing-policy` (gateway/agents.py:685). So "@acme-env custom rule agents=[triage]" is a plain API call, no browser.
  3. Gateway users and registration keys are API-able: admin `POST /gateway/users`, then each user `POST /gateway/api-keys` (type "registration") after cookie login `POST /gateway/auth/login`.
  4. No message edit/redact op exists in the 0.31.0 operation registry, so cards must be re-posted, not edited in place.
  5. Adding an agent to a room: `invite_agent_to_room` op (allowed if the target's policy admits you) or `POST /gateway/rooms/{id}/agents` {agent_ids} (room write permission).
- **Evidence:** file:line above at tag switch-v0.31.0
- **Impact:** positive. If confirmed live, A5 setup.sh needs zero browser steps.
- **Workaround / next step:** confirm in the kill test.

## [2026-11-05 20:05 IST] sandbox (switch-7c) | A crashing repro looked like a reproduced bug
- **Status:** fixed
- **What happened:** A test meant to use a nonexistent image, `node:7-alpine`, but that image exists on Docker Hub, so Docker pulled it. Node 7 has no `String.prototype.padStart`, the repro threw, and the run came back as exit 1, `passed=False`. That's the same shape as the real bug, so a bisect would have named the wrong culprit.
- **Evidence:** `tests/sandbox/test_sandbox_docker.py::test_unknown_image_raises_sandbox_error` failed with "DID NOT RAISE SandboxError".
- **Impact:** Any env that crashes the repro (old node, broken ref) could have been reported as reproducing the bug.
- **Workaround / next step:** `DockerSandbox.run()` now raises `SandboxError` when the repro prints no `cutoff=… payout=…` line. Agents must surface a SandboxError as "sandbox error", never as a failure. The test now uses node 99, and the node:7 image was removed.

## [2026-11-05 20:05 IST] sandbox (switch-7c) | bizclock's own test suite fails in IST (intended)
- **Status:** wontfix (by design)
- **What happened:** `TZ=Asia/Kolkata npm test` in fixtures/bizclock: 3 pass, 6 fail. In UTC, 9/9 pass on node 20, 22 and 24. That's the point of the story: the vendor's CI is UTC. After `fixtures/bizclock-fix.patch`, 19/19 pass in UTC, Kolkata, Kathmandu, St_Johns and New_York.
- **Evidence:** fixtures/make-fix-patch.sh output; tests/sandbox/test_sandbox_bizclock.py
- **Impact:** @fixer in claude mode must run `npm test` under TZ=Asia/Kolkata as well as UTC to see the bug in the existing suite. Side note for the epilogue: in Asia/Kathmandu (+05:45) the buggy repro prints `cutoff=Fri 17:15 payout=Fri`. The cutoff is wrong, but the payout day is right and the repro exits 0, so a Kathmandu customer would NOT see a failing repro with the 17:10 order.
- **Workaround / next step:** none needed for M1/M2. If the epilogue needs Kathmandu to fail, use a 17:20 order there.

## [2026-11-05 19:40 IST] lead | Hop limit 12 too low for the full flow (re: agents entry on MAX_HOPS_PER_CASE)
- **Status:** fixed
- **What happened:** The §3 flow sends 16 tagged agent messages, but the guard was set to 12, so the case would have been cut off at verify_req.
- **Evidence:** tests/case/test_state.py::test_full_flow_completes_under_hop_limit
- **Impact:** None after the fix.
- **Workaround / next step:** `config.MAX_HOPS_PER_CASE = 20`; contracts.py and docs/contracts.md updated to match.

## [2026-11-05 20:10 IST] agents (switch-90) | ClaudeFixer live run: works (1 run, 24 s, $0.08)
- **Status:** fixed (verified once)
- **What happened:** `WOYM_LIVE_CLAUDE=1 uv run pytest -s tests/agents/test_fixer.py::test_live_claude_fixer` cloned main @ 2341e84365, ran `claude -p --model sonnet --allowedTools "Read,Edit,Write,Glob,Grep,Bash(node *),Bash(npm test*)" --output-format json --strict-mcp-config --setting-sources project` with TZ=Asia/Kolkata in the environment, and finished in 24 s for $0.08. Our checks passed (suite green under TZ=Asia/Kolkata and TZ=UTC; the changed test fails on the original src). It pushed fix/case-99 @ c564087a88, with the sha read from ls-remote.
- **Evidence:** It edited src/index.js and the EXISTING test/cutoff.test.js (it didn't create a new file). Test name: "a Friday order before the cutoff is paid Friday in half-hour offset zones". 1 tool call was denied (not on the allow list; it carried on).
- **Impact:** None. The output isn't deterministic: a later run may choose a different test or fix, but the same checks gate it. Summary text from the model contains backticks; the card handles them.
- **Workaround / next step:** The remote used was a tmp copy, not var/remotes. Adding the deliberate `Write` and `Glob,Grep` (read-only) tools to the PLAN's allowedTools list is a deviation, so the fixer can create a new test file.

## [2026-11-05 20:40 IST] sandbox (switch-7c) | LANG=en_IN.UTF-8 is only an env var; the locale isn't generated
- **Status:** wontfix (doesn't affect the demo)
- **What happened:** The node `*-slim` and alpine images don't ship the en_IN glibc locale. LANG is set in acme-laptop and in sandbox runs, but `locale -a` wouldn't list it. Node's Intl uses its bundled ICU, not libc locales, so bizclock's behaviour doesn't depend on it.
- **Evidence:** infra/acme-laptop/Dockerfile (no `locales` package); the bisect shows `lang` doesn't flip the result.
- **Impact:** Someone checking `locale` inside the container would see the locale missing. The "lang doesn't matter" result is still true for this bug.
- **Workaround / next step:** none. Mention it in the README's honest notes if anyone asks.

## [2026-11-05 20:50 IST] sandbox (switch-7c), logged by lead | reset.sh first version killed its own calling shell
- **Status:** fixed
- **What happened:** The first version of `scripts/reset.sh` found agents with `pgrep -f 'python.* -m woym\.agents\.'`. That also matched the shell running the test, because the pattern text was in its command line and its cwd was the repo, so reset killed its caller.
- **Evidence:** Found by sandbox while testing reset from a dirty state, before the code was handed over.
- **Impact:** None shipped. Any pattern-based process kill in the demo tooling could do the same thing.
- **Workaround / next step:** reset now reads /proc/<pid>/cmdline and requires argv[0]=python*, argv[1]=-m and argv[2]=woym.agents.<name> exactly. It also checks the cwd is this checkout and skips $$ and $PPID.

## [2026-11-05 18:49 IST] lead | Several log timestamps above were estimated, not clock time
- **Status:** fixed (process)
- **What happened:** Entries stamped 19:00–20:50 IST were written before 18:49 IST by the real clock (`date`). Sessions estimated the time instead of reading it. The content is accurate; the times aren't.
- **Evidence:** `date` returned 18:28 IST at `git init` and 18:49 IST when this entry was written. Commit times in `git log --format='%h %ad'` are the real ones.
- **Impact:** Only the order of the log. No technical impact.
- **Workaround / next step:** From now on every entry uses `$(date '+%Y-%m-%d %H:%M IST')`. All sessions have been told.

## [2026-11-05 18:52 IST] sandbox (switch-7c) | Bugs fixed while building B8/B9 (own unreleased code)
- **Status:** fixed
- **What happened:**
  1. fresh-clone-test.sh first checked only probe's exit code, so wrong facts would still pass. It now compares the exact probe JSON and greps the IST/UTC repro lines; a mismatch exits 1 (checked).
  2. media/cold-open.html: the final caption overlapped the "closed as not reproducible" line. The caption is now a full-screen overlay over the dimmed thread.
  3. media/cold-open.html: in windows that weren't 1920x1080, the stage scaled around the wrong origin and was cut off (seen at 1280x720). It now uses top-left anchoring plus a centering translate; checked at 1920x1080, 1280x720 and 1000x900.
  4. media/cold-open.html: the "mock-up" label was dimmed under the caption overlay; it now sits above it.
- **Evidence:** headless Chrome screenshots (`google-chrome --headless=new --window-size=W,H --screenshot`).
- **Impact:** none remaining.
- **Workaround / next step:** Caveat: headless Chrome's virtual time doesn't advance CSS transitions, so I could only check the static final frame and the first 3.5 s by screenshot. Watch the full animated run once in a real browser before recording.

## [2026-11-05 18:53 IST] platform (switch-ba) | Kill test A2: GO. Cross-owner addressing is Switch-enforced
- **Status:** fixed (resolves the lead's 18:30 risks 1–4; also the 19:20 "stack blocked" entry: the user started the stack)
- **What happened:** `uv run python scripts/killtest/killtest.py` against live 0.31.0:
  - cross-owner send_targeted → `not_permitted` under the default policy, then `live` + addressed after Meera's rule `{agents:[<uuid>]}`;
  - dev changing Meera's agent's policy → 403, and an agent changing its own addressing → 403;
  - identity link by API works, after which `owner:true` matches Meera;
  - thread replies keep the root's thread_id (a Switch `sw_…` id).
  Then `scripts/setup.sh` ran clean twice (idempotent, no manual steps), and a live smoke test of WsSwitchClient + humans.py passed.
- **Evidence:** var/killtest/results.json; docs/switch-notes.md §0
- **Impact:** the "Switch-enforced" pitch holds; no fallback needed.
- **Workaround / next step:** none.

## [2026-11-05 18:53 IST] platform (switch-ba) | Brief was wrong: @name inside post_message DOES address
- **Status:** open (for agents to note)
- **What happened:** `post_message("@kt-meera …")` arrived at kt-meera with `addressed: true`.
- **Evidence:** var/killtest/results.json S6.post_with_at_name
- **Impact:** an agent prose line containing "@acme-env" addresses acme-env, and if it isn't permitted, acme-env's auto-reply refusal appears in chat.
- **Workaround / next step:** use send_targeted for hops; avoid @agent in post() prose.

## [2026-11-05 18:53 IST] platform (switch-ba) | Refused addressing posts a visible auto-reply in the room
- **Status:** open (demo risk)
- **What happened:** when a non-permitted sender addresses an agent, Switch posts a reply from that agent: "@dev I only take instructions from my owner, and this chat account isn't…" / "@kt-dev You're not permitted to direct messages to me in this room…".
- **Evidence:** Mattermost channel of room kt-*, var/killtest/events.jsonl
- **Impact:** on camera, a stray @acme-env from dev shows a refusal. That could be a deliberate demo beat ("Switch enforces Meera's rule") or noise.
- **Workaround / next step:** lead decides whether to show it on purpose.

## [2026-11-05 18:53 IST] platform (switch-ba) | Kill-test script: two false FAILs (test bugs, not Switch)
- **Status:** worked-around
- **What happened:** (1) `connect_to_room` was called before the WS had opened → 409 "connection … is not open". The real client only calls it after `connection_state`. (2) The MM thread check ran before the bridge delivered the post; a later read shows the reply in the thread.
- **Evidence:** results.json S5.connect_to_room.*, S7.agent_reply_lands_in_mm_thread; MM thread 9t9i8wz3… shows "kt agent reply in thread".
- **Impact:** none.
- **Workaround / next step:** the live smoke test covers both paths.

## [2026-11-05 18:53 IST] platform (switch-ba) | list_participants ids != event sender ids; client fixed
- **Status:** fixed
- **What happened:** list_participants returns UUIDs; events carry `@switch-agent-…` / `@switch-mattermost-…` ids. The first client mapped kinds by id, so everything would have read as "human".
- **Evidence:** results.json S5.list_participants vs events.jsonl
- **Impact:** fixed in woym/switch/client.py (mapped by name); tests updated.
- **Workaround / next step:** none.
