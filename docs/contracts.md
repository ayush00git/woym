# Contracts

How the WOYM components talk to each other. Types live in `woym/contracts.py`, paths and settings in `woym/config.py`. The lead owns all three files. If something here is wrong or missing, message the lead and log it in `ISSUES.md`. Don't patch around it locally.

## 1. Owners and addressing

| Agent | Switch owner | Runs where | Addressing | Accepts work from |
|---|---|---|---|---|
| `@triage` | dev (vendor) | host | `anyone` | vendor human `dev`, customer human `meera` (by sender id, checked in code), agents runner/fixer/acme-env |
| `@runner` | dev | host (uses the Docker socket) | default "me and my agents" | `@triage` |
| `@fixer` | dev | host (`claude -p` in `var/work/`) | default | `@triage` |
| `@acme-env` | **meera (customer)** | host, but only acts inside the `acme-laptop` container | custom rule `agents=[triage]`. Fallback: `anyone` + a sender allowlist in code | `@triage`; consent only from `meera` |

Rules learned from the source:
- An `@name` inside `post()` addresses nobody. Every agent-to-agent hop uses `send_targeted()`.
- An agent can't change its own addressing. The owner, or another agent with the same owner, sets it.
- Humans never command `@runner`, `@fixer` or `@acme-env` directly. Vendor commands go through `@triage` ("@triage patch it"). Meera's accounts aren't linked to Switch users, so her addressing would be refused.
- Run `!follow-ups off` in the room, so Meera's thread replies aren't routed to `@acme-env` as addressed messages and then refused on camera.

## 2. Wire format between agents

Every agent-to-agent message is a human-readable sentence, then a **last line** in backticks:

```
`woym case=42 step=env key=value key2=value2`
```

- `step` is a `Step` value from `contracts.py`.
- Values are percent-encoded (`urllib.parse.quote(v, safe="/:._-+,")`), so they contain no spaces.
- Booleans are `1`/`0`. Lists are comma-joined.
- Parse with `woym.case.tags.parse(text) -> (case_id, step, dict) | None`; build with `woym.case.tags.tag(case_id, step, **kv) -> str`.
- Messages without the tag are human chat. Agents ignore them unless the message is a human command to `@triage` or a consent reply to `@acme-env`.

## 3. Case flow (one Mattermost thread per case, rooted at Meera's report)

| # | From → To | step | Payload | Visible result |
|---|---|---|---|---|
| 1 | meera → @triage | (human) | "@triage <report>" | case opened |
| 2 | @triage → @runner | `sandbox_run` | vendor env | |
| 3 | @runner → @triage | `sandbox_run` | `passed=1 actual=…` | |
| 4 | @triage → room | `not_reproduced` | | **Card: NOT REPRODUCED** |
| 5 | @triage → @acme-env | `env_req` | `keys=node,lang,os,bizclock,tz` | |
| 6 | @acme-env → meera | `consent` | lists exactly what is shared and run | "Share these 5 values and run the repro twice on this machine? reply yes" |
| 7 | meera → @acme-env | (human) | "yes" | |
| 8 | @acme-env → @triage | `env` | the 5 values | |
| 9 | @triage → room | | | **Card: suspect lineup** (5 rows, vendor vs customer) |
| 10 | @triage → @runner | `bisect` | vendor and customer env | |
| 11 | @runner → @triage | `bisect` | per-key pass/fail, `culprit=tz` | **Card rows flip** ✅✅✅✅❌ |
| 12 | @triage → @acme-env | `confirm_req` | `key=tz value=UTC` | |
| 13 | @acme-env → @triage | `confirmed` | `override_pass=1 real_pass=0` | **Card: CONFIRMED ON BOTH MACHINES** |
| 14 | dev → @triage | (human) | "@triage patch it" | |
| 15 | @triage → @fixer | `fix_req` | `culprit=tz` | |
| 16 | @fixer → @triage | `fix_pushed` | `branch=… sha=… test=…` | fix summary |
| 17 | @triage → @runner | `matrix_green` (request) | `ref=<sha>` | |
| 18 | @runner → @triage | `matrix_green` | per-env results | matrix card |
| 19 | @triage → @acme-env | `verify_req` | `sha=…` | |
| 20 | @acme-env → room | `fixed` | `actual=…` | **"✅ FIXED ON MEERA'S MACHINE"**, posted by the customer's agent |

Loop guard, enforced in `woym/case/state.py`:
- each step fires at most once per case;
- at most 20 hops per case (the full flow uses 16; `config.MAX_HOPS_PER_CASE`);
- at most one targeted send per agent every 2 s;
- agents ignore their own messages and Switch system messages;
- the LLM only ever writes prose and never chooses recipients or steps.

## 4. Environment facts

`Env` has exactly 5 keys, in order: `node, lang, os, bizclock, tz`. These are the only facts `@acme-env` may share, and only after consent. Consent covers their **values**.

| key | vendor sandbox (CI) | customer (acme-laptop) | matters? |
|---|---|---|---|
| node | 22 | 20 | no |
| lang | en_US.UTF-8 | en_IN.UTF-8 | no |
| os | alpine | bookworm | no |
| bizclock | main (2.3.2-dev) | v2.3.1 | no (both have the bug) |
| tz | UTC | Asia/Kolkata | **yes** |

## 5. The bug (fixtures/bizclock)

`nextCutoff()` converts local time using `Math.round(-getTimezoneOffset() / 60)` whole hours. So IST +05:30 becomes +06:00, and a Friday 17:10 IST order is treated as after the 17:30 cutoff, so the payout slips to Monday. It passes in UTC CI. The bug class is modelled on date-fns #1599 and #1890.

`examples/payout.js` is the customer's repro script:
- it prints one line `cutoff=<...> payout=<Day>`;
- it exits 0 when the payout is Friday and 1 otherwise;
- with a Friday 17:10 local order, the correct answer is Friday.

## 6. Repos and builds

- `var/remotes/bizclock.git` is a bare repo seeded from `fixtures/bizclock`, with tag `v2.3.1` and branch `main`. It stands in for the vendor's GitHub.
- Runs never bind-mount. The code at a ref is streamed in with `git archive <ref> | docker run -i …` and untarred into `/work` (tmpfs).
- `@fixer` works in `var/work/case-<n>` (a clone of the bare remote) and pushes `fix/case-<n>`. The sha always comes from `git ls-remote`.

## 7. Honesty rules for product behaviour

- Anything replayed or faked has to be labelled in the chat output: fixture fixer mode, scripted human messages in the demo, the customer laptop being a container. The README says the same.
- No silent fallbacks. If a component falls back (for example the fixer drops from claude to fixture), it says so in chat and logs it in `ISSUES.md`.
