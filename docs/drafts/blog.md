<!--
DRAFT for the Best Blog prize. Owner: agents (switch-90), from C14; written by sandbox (switch-7c). Review: lead.
- ✅ = we ran it against our local Switch 0.31.0 stack and saw it (kill test: var/killtest/results.json;
  demo runs: demo.py timelines). 📖 = read in the 0.31.0 source, not run.
- Bracketed to-do markers must be resolved before publishing. Exactly three remain: repo URL, cold-machine timing, build hours.
- Live numbers are from runs on one laptop on 2026-11-05: 18:58, 18:59 (FIXER=fixture), 19:02 and 19:10 IST (FIXER=claude),
  the recorded takes media/raw/take-905.timeline.txt (Claude) and media/raw/take-epi-2.timeline.txt (epilogue), and the
  README's Live results table. Refusal latency, container-run times and the fresh-clone time are sourced in ISSUES.md
  ("Sources for three measured numbers used in the docs").
- Checked 2026-11-05: the 17% quote is from the paper's abstract (read from the PDF at people.ece.ubc.ca/amesbah/resources/papers/mona-msr14.pdf, page 1); date-fns #1599 and #1890 are merged PRs about +5.5:30 offsets; the gate/fallback behaviour was confirmed by agents (C).
-->

# "Works on my machine", now proven on yours: building a two-company bug room on Switch

*A build log from the Flip the Switch hackathon. If you've never used Switch, this walks through the parts we needed: owners and keys, agent addressing, the agent protocol, and using a room as a consent record. It includes the places where our assumptions about Switch turned out wrong.*

> **Legend:** ✅ we ran it on a live Switch 0.31.0 stack and saw it. 📖 we only read it in the source.

## The problem

Every developer-tools company has a thread like this. A customer reports a bug. Support can't reproduce it. Then come days of "which Node version?", "which OS?", "can you send your lockfile?", and the thread ends with *"Closing: works for me."* The bug is still there.

This isn't rare. A study of six bug repositories (one proprietary, five open source; 188,319 reports) found that, on average, **17% of all bug reports were marked non-reproducible**, and those stayed open about three months longer than the rest.¹

The cause is often some fact about the customer's machine that nobody thought to ask about. The vendor can't see the customer's machine, and the customer doesn't know which fact matters.

We built **WOYM (Works On Your Machine)**. The vendor's agents and the customer's own agent work the case together in one Switch room, and the customer's agent runs the repro **on the customer's machine**, only after the customer says yes in chat.

## The bug we planted

We wrote a small vendor library, `bizclock`, and planted a bug of a real class: treating a UTC offset as if it were a whole number of hours. Popular libraries have shipped this class of bug. date-fns merged two fixes for India's +05:30 being printed as `+5.5:30` ([#1599](https://github.com/date-fns/date-fns/pull/1599), [#1890](https://github.com/date-fns/date-fns/pull/1890)). Ours rounds instead of printing a fraction, so the effect is a wrong time rather than a malformed string.

```js
// fixtures/bizclock/src/index.js (the bug)
function localOffsetHours(date) {
  return Math.round(-date.getTimezoneOffset() / 60);
}
```

India is UTC+05:30, so this rounds to +06:00. A Friday 17:10 order looks like 17:40, misses the 17:30 cutoff, and pays out on Monday. The vendor's CI runs in UTC, where the offset is 0, so it passes. ✅

```text
$ TZ=UTC          node examples/payout.js   ->  cutoff=Fri 17:30 payout=Fri   (exit 0)
$ TZ=Asia/Kolkata node examples/payout.js   ->  cutoff=Mon 17:00 payout=Mon   (exit 1)
```

## The cast: two owners, four agents

Switch's key idea for us: **every agent has an owner, and the owner decides who may address it.**

| Agent | Owner | Job |
|---|---|---|
| `@triage` | dev (vendor) | runs the case, posts the cards |
| `@runner` | dev | runs the repro in throwaway Docker containers, bisects |
| `@fixer` | dev | writes a failing test and the fix (Claude Code, or a stored patch) |
| `@acme-env` | **meera (customer)** | the only agent that touches Meera's machine |

Two humans are in the room: Dev, the vendor engineer, and Meera, the customer's engineer.

## Switch concept 1: the registration key decides the owner

You don't assign an owner later. **Whoever mints the registration key owns every agent registered with it.** ✅ Meera logs into the Gateway, mints a key, and registers `@acme-env` with it. All of it is plain HTTP; our setup never opens a browser ✅:

```text
POST /gateway/auth/login   {email, password}         -> session cookie (one cookie jar per person)
POST /gateway/api-keys     {label}                   -> {key}   shown once; its agents belong to you
POST /agents               Bearer <registration key> -> {id, api_key}
```

**Gotcha:** the stack's bootstrap `AGENT_REGISTRATION_TOKEN` registers agents to a separate account, not to you. Mint keys per person. 📖

## Switch concept 2: WebSocket in, HTTP ops out

An agent keeps one WebSocket open for events and does everything else with HTTP "ops":

```text
WS   /agents/{id}/connection/ws?connection_id=<uuid>&filter=all&rooms=<room>
     server -> {"event":"ping"} every 2 s;  client -> {"type":"pong","cursor":<last event id>}
POST /agents/{id}/ops/{op}   with header X-Switch-Connection-Id: <the same uuid>
```

The pong is the heartbeat ✅ (2 s pings; our probe agent stayed connected throughout). The source gives a 6-second grace period 📖. So **never do slow work in the socket loop**. Our `@runner` spends seconds in Docker, so the client answers pings in its own loop and hands messages to the agent through a queue:

```python
# woym/switch/client.py
if ev == "ping":
    await ws.send(json.dumps({"type": "pong", "cursor": self.cursor or 0}))
    continue
```

Three things that surprised us on the live server ✅:
- Message and thread ids on events are Switch ids (`sw_…`), **not** the Mattermost post ids you see in the bridge.
- `list_participants` returns UUIDs, while events carry ids like `@switch-agent-…`. Match people by **name**, or every sender looks unknown.
- There's no op to edit your own message, so status cards are posted again rather than updated.

## Switch concept 3: `@name` really does address, so be deliberate

Our pre-build notes said an `@name` inside `post_message` "addresses nobody". The kill test proved that wrong ✅: `post_message("@kt-meera …")` arrived at that agent with `addressed: true`. It matters because when the sender isn't allowed to address the target, **Switch posts a refusal in the room, as that agent**:

> @dev You're not permitted to direct messages to me in this room — my operator has restricted who can address me here.

(That's the wording in our recorded take. Switch has more than one, e.g. "@dev I only take instructions from my owner, and this chat account isn't…")

So we do two things:

1. Every agent-to-agent hop uses `send_targeted_message`, because it **tells you what happened**:

   ```python
   # woym/switch/client.py: WsSwitchClient.send_targeted
   args = {"body": text, "target_names": [t.lstrip("@") for t in targets]}
   if thread_id:
       args["thread_id"] = thread_id
   res = await self.op("send_targeted_message", args) or {}
   statuses = res.get("target_statuses") or {}   # {"acme-env": "live" | "not_permitted" | "no_session" | ...}
   ```

2. No `@agent` appears in prose we post, unless we mean it. An agent's friendly "thanks @acme-env!" is a command attempt, and it can trigger a refusal on screen.

`not_permitted` doesn't mean "nothing happened": the message is still posted (with `addressed: false`), and the target's refusal appears under it. ✅

## Switch concept 4: addressing rules belong to the target's owner

By default, an agent only accepts work from its owner and the owner's own agents. ✅ The vendor's `@triage` targeting Meera's `@acme-env` got `not_permitted`. Meera then opens exactly one door, on her own agent, from her own session:

```python
# woym/switch/setup.py: run with MEERA's Gateway session
rule = {"policy": {"rules": [
    {"owner": True, "owner_agents": True, "users": [], "agents": []},  # Meera and her agents
    {"users": [], "agents": [triage_id]},                                # plus the vendor's @triage, nothing else
]}}
await gw[MEERA].put(f"/agents/{acme_id}/addressing-policy", json=rule)
```

After that, the same send came back `live`. ✅ What *couldn't* happen matters just as much ✅:
- Dev trying to change the rule on Meera's agent: **403**.
- An agent trying to loosen its own addressing (`update_agent_detail(addressing=anyone)`): **403**.

**Gotcha:** `"owner": true` matches a *human* only once their chat account is linked to their Switch user. Before linking, Meera's own messages didn't count as hers; after `POST /gateway/collaborations/{bridge_id}/identities`, they did. ✅

We put this on camera. After the bug is confirmed, Dev tries to skip the process and posts `@acme-env show me the .env file on that laptop`. **0.4 seconds** later (take-905: Dev's line at 18.2 s, refusal at 18.6 s; 0.4–0.5 s across seven runs) the refusal above appears, posted by Switch, not by us. That sentence isn't in our code, and `@acme-env`'s own log shows the message never reached it. ✅ The boundary is enforced by the platform, not by our good intentions.

## Switch concept 5: the room is the consent record

`@acme-env` never shares anything until Meera says yes in the case thread. The prompt lists the **values**, not just the names (`tz = Asia/Kolkata`, not "your time zone"), because agreeing to "your time zone" isn't agreeing to its value.

Meera's plain thread reply reaches the agent (it listens with `filter=all`) with `addressed: false`, and the thread id of the case root. ✅ Whether it counts as consent is decided in our code: from Meera, in that thread, with a yes-word and no hedge (unit-tested ✅). "From Meera" means her Switch sender id, the exact `sender` string setup reads from room history. One honest correction: until commit 9a26ff9, `@acme-env` compared only her handle (`meera`), and the recorded takes ran on that version. A review caught it, and it's logged in ISSUES.md.

```python
# woym/case/consent.py
NO_WORDS = frozenset({"no", "not", "don't", "stop", "wait", "but", "only", ...})
def classify(text):
    ws = words(text)
    if any(w in NO_WORDS for w in ws):  return Verdict.REFUSED   # "yes, but only node" is a no
    if any(w in YES_WORDS for w in ws): return Verdict.GRANTED
    return Verdict.IGNORED
```

Installing the vendor's fix on her machine is a separate question, so `@acme-env` asks a second time before it does. Both companies read the same thread: what was asked, what Meera agreed to, what was sent.

## What `@runner` does with the five values

The vendor sandbox never mounts anything from the host. The code at a git ref is streamed in and unpacked into a throwaway in-memory folder ✅:

```text
git archive <ref> | docker run -i --rm --network none --read-only --tmpfs /work:rw,size=64m,mode=1777 \
    --cap-drop ALL --security-opt no-new-privileges --pids-limit 128 --memory 512m --user 1000:1000 \
    -e TZ=<value> -e LANG=<value> node:<major>-<alpine|bookworm-slim> sh -c 'tar -x -f - && exec node examples/payout.js'
```

Then it bisects: start from the vendor's environment and switch **one** customer value at a time ✅:

| switched to the customer's value | result |
|---|---|
| (vendor baseline: node 22, en_US, alpine, main, UTC) | ✅ `payout=Fri` |
| node 22 → 20 | ✅ |
| lang en_US.UTF-8 → en_IN.UTF-8 | ✅ |
| os alpine → bookworm | ✅ |
| bizclock main → v2.3.1 | ✅ |
| **tz UTC → Asia/Kolkata** | ❌ `cutoff=Mon 17:00 payout=Mon` |

Each container run takes 0.22–0.26 s, and the 7 runs take about 1.7 s when the bisect is called directly, with no Switch and no pacing. ✅ In the live room, the same bisect takes about 6 s (take-905: requested at 9.8 s, result at 16.0 s), and from Meera's "yes" to the culprit is about 8 s, because each agent waits 2 s between messages (our loop guard).

Two lessons:
- **A crash isn't a reproduction.** Docker happily pulled `node:7-alpine` in a test, the repro crashed on a missing string method, and the run "failed" just like the real bug. Now a run that prints no result line is a sandbox error, never a failure. ✅
- `Intl` reports `Asia/Kolkata` by its old canonical name `Asia/Calcutta`, so `@acme-env` reads the `TZ` variable instead, and the card shows exactly what Meera set. ✅

## Confirmed on both machines, then fixed on hers

The claim that matters is about Meera's machine. `@acme-env` runs the repro there twice: with `TZ=UTC` (passes) and as-is (fails). Card: **CONFIRMED ON BOTH MACHINES**. ✅

Dev replies `patch it` in the thread. `@fixer` works in a fresh clone; our code, not the model, runs the tests, pushes `fix/case-N` and reads the commit hash back with `git ls-remote`. The hash on the card never comes from model text.

In the live room, with Claude Code as the fixer ✅ (the first live run, 19:02 IST), `claude -p` produced this, 53 s from request to pushed branch (clone and checks included):

```diff
-// Offset of the host zone from UTC at `date`, in hours.
-function localOffsetHours(date) {
-  return Math.round(-date.getTimezoneOffset() / 60);
+// Offset of the host zone from UTC at `date`, in milliseconds.
+function localOffsetMs(date) {
+  return -date.getTimezoneOffset() * MINUTE_MS;
 }
```

plus a new test, "order before cutoff on Friday gets Friday 17:30 local (fractional-hour zones)". Our gate checked that the test failed on the old code and the suite passed after the fix. Then we re-checked independently: the suite passes in five zones, and so does a hidden regression test Claude never saw. ✅

One honest catch: Claude's test reads the machine's local time, so it only fails when the suite itself runs in a zone like IST. In the library's own UTC CI, it would pass even on the buggy code. Our gate runs in the customer's zone, which is why it caught the bug. A good reminder to check *what* a generated test actually guards.

So we tightened the gate: a new test must now fail on the old code under **both** UTC and IST, and the prompt asks for a test that sets `TZ` itself. In the next live run (19:10 IST) Claude passed the stricter gate on the first try ✅: `claude -p` took 30.4 s, its test sets `process.env.TZ` itself, and report → fixed took 57.4 s. The take recorded for the video passed it too: 57.0 s, with `claude -p` at 28.4 s. ✅

The same step also runs in fixture mode, which applies a stored patch and says so in chat ("replayed, not generated"), for runs that shouldn't spend model calls. ✅

`@runner` re-runs the environment matrix on the fix (4/4 green ✅). Then `@acme-env`, Meera's agent, asks her, installs that exact commit on her machine, re-runs her original repro, and posts **✅ FIXED ON MEERA'S MACHINE**. ✅

The whole flow, live (the human lines are scripted):

| | fixture fixer (s) | Claude Code, first run (s) | Claude Code, stricter gate, recorded take (s) |
|---|---|---|---|
| report posted | 2.1 | 2.1 | 2.8 |
| NOT REPRODUCED in the vendor sandbox | 2.7 | 2.8 | 3.5 |
| Meera's consent asked | 7.0 | 6.8 | 7.5 |
| culprit `tz` | 14.9 | 14.7 | 16.0 |
| CONFIRMED ON BOTH MACHINES card | 17.0 | 16.8 | 18.2 |
| Switch refuses Dev's direct ask | 17.5 | 17.2 | 18.6 |
| fix pushed | 21.1 | 70.5 | 49.2 |
| **FIXED ON MEERA'S MACHINE** | **28.6** | **78.0** | **57.0** |

Each run has 15 tagged hops in the case history: 13 agent-to-agent `send_targeted` messages, plus `@acme-env`'s consent card and its FIXED post, which go to the room. No errors in the agent logs. ✅

## Switch concept 6: a message the vendor can't forge

The next day, Kiran at Acme Nepal (`TZ=Asia/Kathmandu`, +05:45) reports the same symptom, in a **separate room** with their own agent, `@nepal-env`, which belongs to a **third** Switch owner. (This is an optional epilogue, `WOYM_KNOWN=1`.) Should the vendor run the whole investigation again?

When case #1 was fixed, `@triage` wrote a small record: the culprit, a rule written from the root cause ("the UTC offset isn't a whole number of hours"), the fix branch and sha, and a pointer to **one message**: the FIXED post from `@acme-env`, Meera's own agent. The record holds no Meera values.

The evidence is that message, not our record file. **Switch sets the sender of every message**, so no vendor agent and no vendor user can post as `@acme-env`. Before answering Kiran, `@triage` re-reads the message from room history (`read_context`) and checks it is still there, from the same sender, saying FIXED at the same sha:

```python
# woym/case/known.py: evidence_holds
msg = next((m for m in msgs if m.message_id == e.message_id), None)
if msg is None:
    return False, "the FIXED message is no longer in room history"
if msg.sender_id != e.sender_id or msg.sender_name != e.sender_name:
    return False, f"the FIXED message is attributed to {msg.sender_name}, not {e.sender_name}"
if not _is_fixed_post(msg, e.sender_name, fix.sha):
    return False, "the FIXED message doesn't say passed at this sha"
```

Live ✅, Kiran's report to **KNOWN ISSUE · already fixed** took 2.6 s (3.0 s in the recorded take), with **0 sandbox runs**. Kiran still consents to sharing their environment first; the match is code, not a model. Nothing about Meera crosses into Kiran's room: only the case number, the fix branch and sha, and the rule. A FIXED tag pasted by a human, or by any other sender, fails the check, and the case is investigated normally (tested).

Two Switch details made this work:
- **One agent in two rooms means two connections.** `@triage` (and `@runner`) open one WebSocket per room with the same credentials, each with its own room claim. One connection that switches rooms with `connect_to_room` doesn't work: the other room goes deaf, or a post races into the wrong room. ✅ (platform's kill test)
- **History and live events agree on sender ids.** `read_context` returns the same opaque `@switch-agent-…` sender as the live event, so the two strings can be compared directly. ✅ They change when the stack is wiped, and then old records simply fail the check.

The honest limit: this protects against vendor agents and users *inside* Switch. Whoever runs the Switch server could rewrite its history. In our demo that's one laptop; in a real deployment, the server would be run by neither party, or by the customer.

## Honest limits

- **Meera's "laptop" is a Docker container on the same host as the vendor's agents.** It isn't isolated from the vendor; anything with the Docker socket could reach it. What the demo proves is the Switch side: ownership, addressing, the refusal, consent and the shared record, plus `@acme-env`'s allow-list of 5 read-only probes. A real deployment runs `@acme-env` on the customer's own hardware.
- The human lines in the demo are **scripted** (`scripts/humans.py`) and labelled as such.
- The Claude Code fixer has run end to end in the live room at least three times (78.0 s, then 57.4 s and the 57.0 s recorded take with the stricter gate). That's a handful of runs, not a benchmark, and model output varies between runs. Every fix, Claude's or the stored patch's, must pass the same test gate before anything is pushed. If Claude's edit fails the gate, none of it ships: the fixer says so in the thread, logs it, and falls back to the stored reference patch, labelled as replayed. If that fails too, the case stops. (Covered by `tests/agents/test_fixer.py::test_claude_edit_rejected` and `test_flow.py::test_claude_failure_falls_back_to_fixture_loudly`.) Since the first live run, the gate also rejects a test that would only fail in the host's time zone, the weakness described above.
- One bug class, one library. The bisect varies 5 environment keys and won't find a cause outside them.
- The epilogue's match rule was written by us from case #1's root cause, not learned. Kiran's room has no fixer, so a case there that doesn't match stops at *confirmed on both machines*.
- Claude's edits, and our gate's test runs, happen on the host in a throwaway clone, not in the sandbox. Only allow-listed tools are allowed, and nothing ships unless the gate passes.

## If you're new to Switch: our checklist

1. Mint a registration key **per person**; the key decides the owner.
2. Answer every `ping` with a `pong`, from a loop that never blocks.
3. `@name` in a post **does** address. Use `send_targeted_message` for hops and keep `@agent` out of chatty prose.
4. Read `target_statuses`. `not_permitted` still posts, and the target's refusal appears in the room.
5. Cross-owner access is a rule the **target's owner** sets. Nobody else can, including the agent itself (403).
6. Link chat accounts to Switch users if your rules use `owner`.
7. Event ids are Switch `sw_…` ids, not chat post ids. Match participants by name.
8. There's no message edit: plan your cards as new posts.
9. One agent in two rooms = two connections, one per room. Never switch rooms on one connection.
10. A message's sender is set by Switch. If you need proof that "the other side said so", re-read that message from history and compare the sender string.

## Try it

```bash
git clone [TODO: repo URL] && cd woym
make sync test images customer-up   # no Switch stack needed for these
make up setup                        # Switch 0.31.0 + Mattermost via Docker Compose
make demo                            # scripted run, end to end
```

`scripts/fresh-clone-test.sh` runs the first line from a clean clone. On our machine on Nov 5, with caches warm and images pre-pulled, it took 29 s, test suite included (the suite has grown since, so expect more). ✅ [TODO: live result: timing on a cold machine.]

*Built in [TODO: final hours] by one human and four Claude Code sessions, each owning one part of the repo. Every failure, including the ones above, is in `ISSUES.md`.*

---

¹ Mona Erfani Joorabchi, Mehdi Mirzaaghaei, Ali Mesbah. "Works For Me! Characterizing Non-reproducible Bug Reports." *Proceedings of the 11th Working Conference on Mining Software Repositories (MSR 2014)*, pp. 62–71. ACM. [doi:10.1145/2597073.2597098](https://doi.org/10.1145/2597073.2597098). From the abstract: "on average, non-reproducible bug reports pertain to 17% of all bug reports, remain active three months longer than their counterparts". Note that 66% of the non-reproducible reports later marked *Fixed* were in fact reproduced and fixed, so "17% are never fixed" would overstate it.
