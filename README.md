# Works On Your Machine (WOYM)

> **"Works on my machine." Now proven on yours.**
> The vendor's agents and the customer's own agent work one bug together in one [Switch](https://github.com/sandbox-quantum/switch) room. They go from *"can't reproduce"* to *"fixed on your machine"*, and the customer approves, in chat, everything that touches her machine or her data.

Built for the **Flip the Switch** hackathon (WeMakeDevs × DevTools Academy × Switch), Track 01: Best Business Workflow.

---

## The problem

A customer reports a bug. The vendor's CI is green, and support replies "can't reproduce". Then come days of "which Node version? which OS? can you send your lockfile?", and the thread often ends with *"Closing: works for me."* The bug is still there.

This is common. Across six projects and 188,319 bug reports, about **17% of bug reports were marked non-reproducible**, and those stayed open about three months longer than the rest (Joorabchi, Mirzaaghaei, Mesbah, *"Works For Me! Characterizing Non-Reproducible Bug Reports"*, MSR 2014, [doi:10.1145/2597073.2597098](https://doi.org/10.1145/2597073.2597098)).

The cause is usually one fact about the customer's machine that nobody thought to ask about. The vendor can't look at that machine, and the customer doesn't know which fact matters.

Our demo bug belongs to a real bug class. `bizclock`, a small payouts library written for the demo, rounds UTC offsets to whole hours, so India's **+05:30 becomes +06:00**. An order placed Friday at 17:10 IST lands after the 17:30 cutoff, and the payout slips to **Monday**. In UTC CI everything passes. date-fns shipped fixes for the same class ([date-fns#1599](https://github.com/date-fns/date-fns/pull/1599), [date-fns#1890](https://github.com/date-fns/date-fns/pull/1890)).

## What WOYM does

One Mattermost channel, bridged by Switch. Its agents belong to **two different Switch owners**:

| Agent | Owner | Job |
|---|---|---|
| `triage` | dev (vendor) | runs the case and posts the cards |
| `runner` | dev (vendor) | runs the customer's repro in a throwaway, network-less Docker sandbox, and bisects the environment |
| `fixer` | dev (vendor) | **Claude Code** (`claude -p`) writes a failing test and the fix |
| `acme-env` | **meera (customer)** | acts only on the customer's machine (a container in the demo), runs only allow-listed probes, and asks Meera before anything leaves |

The flow, all in one Mattermost thread:

1. **Meera reports the bug.** `runner` runs her repro in the vendor sandbox, and it passes: ✅ **NOT REPRODUCED**.
2. **Consent.** `triage` asks Meera's agent for environment facts. `acme-env` shows Meera the **exact 5 values** it would share and waits for her **yes**.
3. **Suspect lineup.** The vendor and customer environments differ in 5 places: node, locale, OS, library version, timezone.
4. **Bisect.** `runner` re-runs the repro with one customer value at a time: ✅ ✅ ✅ ✅ ❌. **Culprit: `TZ=Asia/Kolkata`.**
5. **Confirmed on both machines.** Meera's own agent runs the repro **on her machine** with `TZ=UTC` (passes) and as-is (fails).
6. **Switch enforces the boundary.** When dev tries to command Meera's agent directly, **Switch itself** refuses: *"You're not permitted to direct messages to me in this room — my operator has restricted who can address me here."* That text isn't in our code; it's Switch applying Meera's rule.
7. **Fix.** Dev replies "patch it". Claude Code writes a failing test and the fix. The gate accepts it only if the new test fails on the old code **in both UTC and IST** and the full suite passes after the fix. `runner` then checks a 4-environment matrix.
8. **✅ FIXED ON MEERA'S MACHINE.** After a second yes from Meera, her agent installs the patched build on her machine and re-runs the original repro. The vendor's claim is confirmed by the customer's side.

![Trust boundary: what crosses between vendor and customer, and what never does](docs/trust-boundary.svg)

### Epilogue: the next customer gets an instant answer (`WOYM_KNOWN=1`)

Kiran at Acme Nepal (`TZ=Asia/Kathmandu`, +05:45) reports the same symptom in a separate room, through Kiran's own agent `nepal-env`, which belongs to a third owner. `triage` gets Kiran's consented environment and matches it against the fix verified in case #1: "UTC offset isn't a whole number of hours". Before answering **KNOWN ISSUE · already fixed**, it re-reads the evidence from Switch's room history: the FIXED message that Switch attributes to the first customer's own agent. No sandbox run, no vendor human, and nothing about Meera crosses into Kiran's room.

## Results

Measured on one laptop against a local Switch 0.31.0 stack and Mattermost:

| Run | Time |
|---|---|
| Full flow, stored reference fix (`FIXER=fixture`) | **28.6 s** |
| Full flow, **real Claude Code fix** (`FIXER=claude`) | **57.0 s** (`claude -p` 28.4 s) |
| Full flow on a freshly wiped stack | **28.7 s** (setup 7 s, no manual steps) |
| Epilogue: Kiran's report → KNOWN ISSUE | **3.0 s**, 0 sandbox runs |

## How Switch is used

Switch carries the trust model. The full map, with file and function for every row, is in **[docs/switch-usage.md](docs/switch-usage.md)**.

| Switch feature | What it does here | Without it |
|---|---|---|
| **Per-owner registration keys** | dev owns three agents, meera owns `acme-env` | There's no second party, just one vendor talking to itself |
| **Addressing policy, set by the target's owner** | Only `triage` may address Meera's agent. Dev can't change her rule (403), and no agent can loosen its own (403) | Any vendor agent or human could command the customer's machine |
| **The refusal** | Switch posts the refusal itself when the rule says no | The boundary would only be our code's promise |
| **`send_targeted_message`** | Every agent-to-agent hop is targeted and returns `live` or `not_permitted` per recipient | A refused hop looks the same as a slow one |
| **Threads** | One thread per case; consent only counts in that thread | Cases interleave, and a "yes" elsewhere could count |
| **Mattermost bridge + identity links** | Meera answers as herself and matches her own agent's owner rule | No human-visible room, and no consent from the right person |
| **The room as the record** | Request, exact values, yes, runs and result, in order | Data sharing and installs would happen out of sight |

Protocol details and the surprises we hit (an `@name` inside an ordinary post *does* address, ids differ between Switch and Mattermost, there's no edit op) are in [docs/switch-notes.md](docs/switch-notes.md).

## Quickstart

Requirements: **Linux** with **Docker** (tested on Fedora 44), **[uv](https://docs.astral.sh/uv/)**, **git**, **Node ≥ 20**, and optionally the **Claude Code CLI**, signed in, for the live fix.

```bash
git clone https://github.com/ayush00git/woym.git && cd woym
make sync            # python deps
make images          # pre-pull node images, build the customer-laptop image
make customer-up     # start Meera's "laptop" (a container) on bizclock v2.3.1
make up              # Switch 0.31.0 + Mattermost + gateway, localhost only
make setup           # users, per-owner keys, agents, room, Meera's rule, identity links
make status          # preflight
make demo FIXER=fixture   # no Claude needed: a stored, labelled reference fix
make demo                 # Claude Code writes the fix

make setup-epilogue                # optional: third owner kiran, room acme-nepal
make demo-epilogue FIXER=fixture   # full flow, then Kiran's KNOWN ISSUE answer
```

Open Mattermost at http://localhost:8065 and watch `#acme-support`. `make demo` posts Meera's and dev's lines for you, labelled `[SCRIPTED]`. `make reset` returns everything to the pre-demo state, and `make test` runs the 250+ test suite.

## Security model

- **Vendor sandbox:** `docker run --network none --read-only --tmpfs /work --cap-drop ALL --security-opt no-new-privileges --pids-limit 128 --memory 512m --user 1000:1000`, with a 90 s timeout. Code goes in over stdin from `git archive`, with no bind mounts, and every env value is validated.
- **Customer side:** `acme-env` can run 5 fixed, read-only probes and the customer's own repro; only `tz`/`lang` can be overridden. Every share and every install needs Meera's yes, in the case thread, from her account.
- **Routing is code, not model output.** Every (step, recipient) pair is fixed in `woym/agents/*.py`. The only model, `claude -p` in `fixer`, writes code in a throwaway clone with allow-listed tools; nothing is pushed unless the gate passes, and the commit sha is read with `git ls-remote`, never from model text.

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

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). `fixtures/bizclock` is a fictional library made for this demo.
