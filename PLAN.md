# WOYM development plan

**Works On Your Machine.** "Works on my machine." Now proven on yours.

Deadline: **Nov 7, 11:59 PM IST**. Internal target: submit by **Nov 7, 21:00 IST**.
Plan written Nov 5, 18:30 IST. Idea and scoring: `.hack/decision.md` (local only, not committed).

## 1. What we're building

A customer reports a bug the vendor can't reproduce. In one Switch room (Mattermost bridge), agents owned by two different Switch users work the case:

1. **Vendor sandbox:** `@runner` runs the customer's repro in a throwaway container. It passes, so the card says NOT REPRODUCED.
2. **Customer side:** `@triage` asks `@acme-env`, the customer's own agent owned by Meera, for 5 environment facts. `@acme-env` asks Meera in chat and she says yes.
3. **Bisect:** `@runner` re-runs with one customer value at a time and finds the culprit, `TZ=Asia/Kolkata`.
4. **Both machines:** `@acme-env` runs the repro on Meera's machine with TZ=UTC (passes) and with her real TZ (fails). CONFIRMED ON BOTH MACHINES.
5. **Fix (M2):** Dev says "@triage patch it". `@fixer` (Claude Code via `claude -p`) writes a failing test and the fix, then pushes a branch. `@runner` re-runs the matrix.
6. **Verify (M2):** `@acme-env` installs the patched build on Meera's machine and posts **✅ FIXED ON MEERA'S MACHINE**.

The interfaces are in `docs/contracts.md` and `woym/contracts.py`. Read both before writing code.

## 2. Architecture

```
            Mattermost (#acme-support)  <-- bridge -->  Switch server (Docker Compose 0.31.0)
                                                          |  WebSocket in, HTTP ops out
          owner: dev (vendor)                             |                 owner: meera (customer)
   +-----------+   +-----------+   +-----------+          |                +-------------+
   |  @triage  |   |  @runner  |   |  @fixer   |   <------+------>         |  @acme-env  |
   | case FSM  |   | Docker    |   | claude -p |                           | probes,     |
   | + cards   |   | sandbox   |   | in a work |                           | consent,    |
   +-----------+   | + bisect  |   | clone     |                           | verify      |
                   +-----+-----+   +-----+-----+                           +------+------+
                         | git archive   | push fix/case-n                        | docker exec
                         v               v                                        v
                 var/remotes/bizclock.git (stands in for the vendor's GitHub)   acme-laptop container
                                                                   (node 20, bookworm, TZ=Asia/Kolkata)
```

Stack:
- Python 3.12+ with uv for all agents.
- Node for the `bizclock` fixture library.
- Docker CE (rootful, already installed).
- `claude -p` on the user's plan for the fixer. No Anthropic API key until after the build.

## 3. Team and ownership

Coworkers edit **only** their own paths. Shared files belong to the lead: `woym/contracts.py`, `woym/config.py`, `docs/contracts.md`, `pyproject.toml`, `Makefile`, `README.md`, `PLAN.md`. To change one, message the lead.

| Who | Session | Role | Owns |
|---|---|---|---|
| **Lead** | switch-b6 | Plan, contracts, review, **all git commits**, integration, demo script, README, video | shared files above, `scripts/demo.py`, `docs/` except `docs/switch-notes.md` |
| **A: Platform** | switch-ba | Switch stack, kill test, real protocol client, setup scripts, scripted human replies | `infra/switch/`, `woym/switch/`, `scripts/killtest/`, `scripts/setup.sh`, `scripts/humans.py`, `tests/switch/`, `docs/switch-notes.md` |
| **B: Sandbox** | switch-7c | bizclock fixture and bug, vendor runner, bisect, matrix, customer machine, bare remote | `fixtures/`, `woym/sandbox/`, `infra/acme-laptop/`, `tests/sandbox/` |
| **C: Agents** | switch-90 | Case state machine, wire tags, cards, consent, fixer, the four agent processes | `woym/case/`, `woym/fixer/`, `woym/agents/`, `tests/case/`, `tests/agents/` |

## 4. Work packages

### A: Platform (switch-ba)
- **A1 Stack.** `infra/switch/`:
  - pin `standalone-docker-compose.yml` to `SWITCH_VERSION=0.31.0` with `--profile collab --profile gateway`;
  - `gen-env.sh` creates secrets (`SECRET_KEYS`, `AGENT_REGISTRATION_TOKEN`) into `.env`;
  - `up.sh` / `down.sh`.
- **A2 Kill test (go/no-go, report by 23:00 IST tonight).** `scripts/killtest/`. Write the results to `docs/switch-notes.md` and `ISSUES.md`:
  1. Gateway user `meera` plus a registration key for each owner (dev, meera). Use the API if possible; otherwise document the manual steps exactly.
  2. A probe agent per owner: register, connect, survive pings, dump the ops list.
  3. Cross-owner `send_targeted`: expect `not_permitted`, then `live` after Meera's custom rule (fallback `anyone`).
  4. Get the cross-owner agent into the room (invite op, admin, or `!invite-agent`).
  5. A human thread reply reaches the agent with follow-ups off; record `thread_id` and sender fields.
  6. Can an agent edit its own message? (Lets cards update in place. Otherwise we re-post.)
  7. Mattermost users `dev` and `meera`; get a personal access token each, for `humans.py`.
- **A3 Client.** `woym/switch/client.py` implements `SwitchClient` from `contracts.py`: reconnect, pings, ops, events mapped to `MessageEvent`. Add `python -m woym.switch.probe` for manual checks.
- **A4 Humans.** `scripts/humans.py meera "…"` / `dev "…"` posts as that user through the Mattermost REST API, optionally in a thread. Used for the scripted demo and QA. The demo labels these lines as scripted.
- **A5 Setup.** `scripts/setup.sh`: idempotent bootstrap after `up` (users, keys, room, agents' addressing, follow-ups off). Print anything that stays manual.

### B: Sandbox (switch-7c)
- **B1 bizclock** in `fixtures/bizclock/`:
  - a zero-dependency Node package `bizclock` with `nextCutoff()` containing the whole-hour rounding bug (`docs/contracts.md` §5);
  - `node:test` tests that pass in UTC;
  - `examples/payout.js` (the repro);
  - a believable small git history (built by a script);
  - tag `v2.3.1`, plus `main` at 2.3.2-dev with an unrelated change;
  - `fixtures/bizclock-fix.patch`, the reference fix plus a test, for fixture mode.
- **B2 Bare remote.** `woym/sandbox/remote.py`: `init_remote()` builds `var/remotes/bizclock.git` from the fixture; `archive(ref) -> bytes`; `ls_remote(branch) -> sha`.
- **B3 Runner.** `woym/sandbox/runner.py` `DockerSandbox` implements `Sandbox`:
  - images `node:{major}-alpine` / `node:{major}-bookworm-slim`;
  - run with `--network none --read-only --tmpfs /work --cap-drop ALL --security-opt no-new-privileges --pids-limit 128 --memory 512m --user 1000` and a 90 s timeout;
  - stream code over stdin, set `TZ`/`LANG`, parse `cutoff=… payout=…`;
  - `bisect()` flips one key at a time (pairs if no single key flips it, capped at 10 runs); `matrix()`.
  - **Verify first:** `TZ=Asia/Kolkata` gives offset −330 in node alpine, and the bug shows on alpine and bookworm.
- **B4 Customer machine.**
  - `infra/acme-laptop/Dockerfile` (node 20 bookworm, TZ=Asia/Kolkata, LANG=en_IN.UTF-8, a tiny `acme-payouts` app using bizclock v2.3.1).
  - `woym/sandbox/customer.py` `DockerCustomerMachine` implements `CustomerMachine` through `docker exec`: allow-listed probes only, `run_repro(overrides)`, and `install(sha)` via `git archive`.
  - `make customer-up` target notes go to the lead.
- **B5** `scripts/pull-images.sh` pre-pulls every image so the demo has no download waits.

### C: Agents (switch-90)
- **C1 Case core** in `woym/case/`:
  - `tags.py`: parse and format the wire tag;
  - `state.py`: case store in `var/cases.json`, transitions, loop guard;
  - `card.py`: Mattermost markdown cards for NOT REPRODUCED, lineup, bisect rows, CONFIRMED BOTH, fix, matrix, FIXED;
  - `consent.py`: a yes only counts from the right sender, in the right thread, with a yes-word.
  - All pure, all unit-tested.
- **C2 Fixer** in `woym/fixer/`:
  - `ClaudeFixer`: clone into `var/work/case-n`, then run `claude -p` with a tight prompt and `--allowedTools "Read,Edit,Bash(node *),Bash(npm test*)"`. Our code (not Claude) runs the tests, commits, pushes `fix/case-n`, and reads the sha via `ls-remote`.
  - `FixtureFixer`: applies `fixtures/bizclock-fix.patch`.
  - Selected by `FIXER`.
  - **Verify early:** `claude -p` works non-interactively here, and note how long it takes.
- **C3 Agents** in `woym/agents/`: `triage.py`, `runner_agent.py`, `fixer_agent.py`, `acme_env.py`, each runnable as `python -m woym.agents.<name>`.
  - Build them against the `SwitchClient` protocol.
  - Test with your own fake client in `tests/agents/`, until A's real client lands.
  - Engines come from B (`Sandbox`, `CustomerMachine`) and C2.

### Lead (switch-b6)
- Scaffold, contracts, config, Makefile, commits after every reviewed unit.
- Integration: wire the real client into the agents. **M1 end to end on Nov 6 by 18:00.**
- `scripts/demo.py`: drives a full run with `humans.py`. Fresh-clone test. README (with the Switch feature table and trust diagram). Video, blog and #HowISwitch drafts.
- Keep `PLAN.md` and `ISSUES.md` current.

## 5. Milestones (IST)

| When | Milestone | Gate |
|---|---|---|
| Nov 5, 23:00 | A2 kill test results; B1–B3 running locally; C1 tested | **Go/no-go on cross-owner messaging.** If no-go, follow the fallbacks in `.hack/decision.md`, log them, and re-plan |
| Nov 6, 13:00 | A3–A5, B4–B5, C2–C3 done against the contracts | |
| Nov 6, 18:00 | **M1**: report → NOT REPRODUCED → consent → lineup → bisect → CONFIRMED BOTH, in real Mattermost | Submittable on its own |
| Nov 6, 23:59 | **M2**: fix → matrix → FIXED ON MEERA'S MACHINE | |
| Nov 7, 10:00 | Cut M2 if it isn't solid. Fresh-clone test | |
| Nov 7, 13:00–18:00 | Record and cut the video; README, blog, #HowISwitch | |
| Nov 7, 21:00 | The user submits | Hard deadline 23:59 |

## 6. Working rules

1. **Stay in your paths.** If you need a change elsewhere, message the owner, or the lead for shared files.
2. **No git writes.** Coworkers don't run `git add/commit/stash/checkout/reset`; read-only `git status/diff/log` is fine. The lead makes every commit, locally only, with conventional names: `feat(scope): …`, `fix(scope): …`, `docs(scope): …`, `test(scope): …`, `chore(scope): …`, `refactor(scope): …`. Scopes: `switch, sandbox, bizclock, case, fixer, agents, infra, demo, docs`. Nothing is pushed to any remote.
3. **Report done units to the lead** (`SendMessage` to `switch-b6`) with: files touched, how to run or test them, what is NOT done, and anything faked or assumed. The lead reviews and commits.
4. **Log every failure, gap or surprise in `ISSUES.md`.** Append only, with `cat >> ISSUES.md <<'EOF'`; never rewrite others' entries. Include the exact command and error text. A blocked or failed task gets logged even if you work around it.
5. **No larping.** Never report something as working unless you ran it. Say "untested" or "assumed" explicitly. Fixtures and replays must be labelled in code, chat output and docs.
6. Use `uv run …` for Python and `uv add` only via the lead (to avoid lockfile races). Use `pytest` for tests.
7. Keep `claude -p` usage small. It runs on the user's plan.
