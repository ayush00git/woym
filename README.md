# Works On Your Machine (WOYM)

> **"Works on my machine." Now proven on yours.**
> The vendor's agents and the customer's own agent work one bug together in one [Switch](https://github.com/sandbox-quantum/switch) room. They go from *"can't reproduce"* to *"fixed on your machine"*, and the customer approves every step in chat.

Built for the **Flip the Switch** hackathon (WeMakeDevs × DevTools Academy × Switch), Track 01: Best Business Workflow.

<!-- TODO(lead): demo video link -->

---

## The problem

A customer reports a bug. The vendor's CI is green, and support replies "can't reproduce". Then come days of "which Node version? which OS? can you send your lockfile?", and the thread often ends with *"Closing: works for me."* The bug is still there.

This is common. Across six projects and 188,319 bug reports, about **17% of bug reports were marked non-reproducible**, and those stayed open about three months longer than the rest (Joorabchi, Mirzaaghaei, Mesbah, *"Works For Me! Characterizing Non-Reproducible Bug Reports"*, MSR 2014, [doi:10.1145/2597073.2597098](https://doi.org/10.1145/2597073.2597098)).

The cause is usually one fact about the customer's machine that nobody thought to ask about. The vendor can't look at that machine, and the customer doesn't know which fact matters.

Our demo bug belongs to a real bug class. `bizclock`, a small payouts library we wrote for the demo, rounds UTC offsets to whole hours, so India's **+05:30 becomes +06:00**. An order placed Friday at 17:10 IST lands after the 17:30 cutoff, and the payout slips to **Monday**. In UTC CI everything passes. date-fns shipped fixes for the same class, offsets with minutes printed as `+5.5:30` ([date-fns#1599](https://github.com/date-fns/date-fns/pull/1599), [date-fns#1890](https://github.com/date-fns/date-fns/pull/1890)). Their symptom was a malformed string rather than a rounded time.

## What WOYM does

One Mattermost channel, bridged by Switch. Its agents belong to **two different Switch owners**:

| Agent | Owner | Job |
|---|---|---|
| `triage` | dev (vendor) | runs the case and posts the cards |
| `runner` | dev (vendor) | runs the customer's repro in a throwaway, network-less Docker sandbox, and bisects the environment |
| `fixer` | dev (vendor) | **Claude Code** (`claude -p`) writes a failing test and the fix |
| `acme-env` | **meera (customer)** | runs only on the customer's machine, runs only allow-listed probes, and asks Meera before anything leaves |

The flow, all in one Mattermost thread:

1. **Meera reports the bug.** `runner` runs her repro in the vendor sandbox, and it passes: ✅ **NOT REPRODUCED**.
2. **Consent.** `triage` asks Meera's agent for environment facts. `acme-env` shows Meera the **exact 5 values** it would share and waits for her **yes**.
3. **Suspect lineup.** The vendor and customer environments differ in 5 places: node, locale, OS, library version, timezone.
4. **Bisect.** `runner` re-runs the repro with one customer value at a time: ✅ ✅ ✅ ✅ ❌. **Culprit: `TZ=Asia/Kolkata`.**
5. **Confirmed on both machines.** Meera's own agent runs the repro **on her machine** with `TZ=UTC` (passes) and as-is (fails).
6. **Switch enforces the boundary.** When dev tries to command Meera's agent directly, **Switch itself** refuses: *"You're not permitted to direct messages to me in this room."* That text isn't in our code; it's Switch applying Meera's rule.
7. **Fix.** Dev replies "patch it". Claude Code writes a failing test and the fix. Our gate accepts it only if the new test fails on the old code and the suite passes in both UTC and IST. `runner` then checks a 4-environment matrix.
8. **✅ FIXED ON MEERA'S MACHINE.** After a second yes from Meera, her agent installs the patched build on her machine and re-runs the original repro. The vendor's claim is confirmed by the customer's side.

![Trust boundary: what crosses between vendor and customer, and what never does](docs/trust-boundary.svg)

## Live results

Every number below is from a full run on one laptop against a local Switch 0.31.0 stack and Mattermost, on 2026-11-05.

| Run | Report → result |
|---|---|
| Up to *confirmed on both machines* (M1), with the refusal | **17.5 s** |
| Full flow, stored reference fix (`FIXER=fixture`) | **28.6 s** |
| Full flow, **real Claude Code fix** (`FIXER=claude`) | **78.0 s** (fix step 52.9 s, from request to pushed) |

In the live Claude run, Claude changed one function to use exact minutes instead of rounded hours and added a regression test. A hidden reference test, which Claude never saw, also passes against its fix. One catch: Claude's test only failed when the host itself was in IST, so it wouldn't have guarded the vendor's UTC CI. We then tightened the gate. A new test must now fail on the old code under **both** UTC and IST, and the prompt asks for a test that sets `TZ` itself. <!-- TODO(lead): re-run FIXER=claude with the stricter gate and update this paragraph with the result. -->

## How Switch is used

Switch carries the trust model. The full map, with file:function and live-or-fake evidence for every row, is in **[docs/switch-usage.md](docs/switch-usage.md)**. The short version:

| Switch feature | What it does here | Without it |
|---|---|---|
| **Per-owner registration keys** | dev owns three agents, meera owns `acme-env` | There's no second party, just one vendor talking to itself |
| **Addressing policy, set by the target's owner** | Only `triage` may address Meera's agent. Dev can't change her rule (403), and no agent can loosen its own (403) | Any vendor agent or human could command the customer's machine |
| **The refusal** | Switch posts the refusal itself when the rule says no | The boundary would only be our code's promise |
| **`send_targeted_message`** | Every agent-to-agent hop is targeted and returns `live` or `not_permitted` per recipient | A refused hop looks the same as a slow one |
| **Threads** | One thread per case; consent only counts in that thread | Cases interleave, and a "yes" elsewhere could count |
| **Mattermost bridge + identity links** | Meera answers as herself and matches her own agent's owner rule | No human-visible room, and no consent from the right person |
| **The room as the record** | Request, exact values, yes, runs and result, in order | Data sharing and installs would happen out of sight |

We hit several surprises in Switch along the way (an `@name` inside an ordinary post *does* address, ids differ between Switch and Mattermost, there's no edit op). They're all written up in [docs/switch-notes.md](docs/switch-notes.md) and [ISSUES.md](ISSUES.md).

## Quickstart

Requirements:
- Linux or macOS with **Docker** (rootful Docker CE on Linux; we used Fedora 44);
- **[uv](https://docs.astral.sh/uv/)**, **git** and **Node ≥ 20** (the fixer runs bizclock's tests on the host);
- optionally the **Claude Code CLI**, signed in, for the live fix.

```bash
git clone <TODO(lead): repo URL> && cd woym
make sync            # python deps
make images          # pre-pull node images, build the customer-laptop image
make customer-up     # start Meera's "laptop" (a container) on bizclock v2.3.1
make up              # Switch 0.31.0 + Mattermost + gateway, localhost only
make setup           # users, per-owner keys, agents, room, Meera's rule, identity links
make status          # preflight
make demo FIXER=fixture   # no Claude needed: a stored, clearly labelled reference fix
make demo                 # the real thing: Claude Code writes the fix
```

Then open Mattermost at http://localhost:8065 and watch `#acme-support`. `make reset` puts everything back to the pre-demo state in under a second. `make test` runs the suite (200+ tests, including a full flow through the real client against a fake Switch server).

## Honest notes

- **Meera's "laptop" is a container on the same host** (node 20, Debian bookworm, `TZ=Asia/Kolkata`). It isn't a second computer. The boundary in the demo is enforced by Switch ownership and addressing, plus `acme-env`'s allow-list, not by hardware. Anything with the host's Docker socket could reach it.
- **The human lines in `make demo` are scripted.** Meera's report, her two "yes" replies and dev's messages are posted through the Mattermost API by `scripts/humans.py`, and the demo output labels them `[SCRIPTED]`. The agents treat them like any human message.
- **`FIXER=fixture`** applies a stored reference patch instead of calling Claude, and every chat card says so ("FIXTURE patch, replayed, not generated").
- **The fixer runs bizclock's tests on the host**, because it's the vendor's own repo. Customer repros only ever run inside the hardened sandbox or on the customer's container.
- We've run this on one machine, against the bundled Mattermost bridge only. It isn't tested with Slack, Teams or Discord yet.

## Security model

- **Vendor sandbox:**
  - `docker run --network none --read-only --tmpfs /work --cap-drop ALL --security-opt no-new-privileges --pids-limit 128 --memory 512m --user 1000:1000`, with a 90 s timeout;
  - code goes in over stdin from `git archive`, with no bind mounts;
  - every env value is validated.
- **Customer side:**
  - `acme-env` can run 5 fixed, read-only probes and the customer's own repro, and only `tz`/`lang` can be overridden;
  - every share and every install needs Meera's yes, in the case thread, from her account (checked by sender id).
- **Routing is code, not model output.** Every (step, recipient) pair is fixed in `woym/agents/*.py`. The only model in the system, `claude -p` in `fixer`, writes code and one line of prose, and its commit sha is read with `git ls-remote`, never from model text.

## Repo map

```
woym/switch/     WebSocket client for the Switch agent protocol, setup, credentials, live-run lock
woym/agents/     triage, runner, fixer and acme-env processes
woym/case/       case state machine, wire tags, chat cards, consent matching
woym/sandbox/    docker runner + environment bisect, customer machine, bare git remote
woym/fixer/      Claude Code fixer and the fixture fixer, with the fails-then-passes gate
fixtures/        the bizclock library (planted bug, history, reference fix)
infra/           Switch compose stack, customer-laptop image
scripts/         demo, reset, status, setup, recorder, fresh-clone check
docs/            contracts, Switch notes and usage map, trust diagram
```

## How it was built

One human and four Claude Code sessions (a lead plus three coworkers), each owning one part of the repo. The plan, the ownership rules and every failure are in the open: see [PLAN.md](PLAN.md), [docs/contracts.md](docs/contracts.md) and [ISSUES.md](ISSUES.md).
