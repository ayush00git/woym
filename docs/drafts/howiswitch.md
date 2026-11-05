<!--
DRAFT #HowISwitch posts. Owner: agents (switch-90), from C15; first written by sandbox (switch-7c). Review: lead.
- Every number matches the README's Live results (2026-11-05, one laptop): Claude Code fix, recorded take 57.0 s
  (first live run 78.0 s); stored-patch fixer 28.6 s; epilogue 2.6 s, 0 sandbox runs.
- [LINK] = the repo or video URL. X counts any link as 23, emoji and most symbols (e.g. →) as 2; the counts below
  were computed that way (weighted, out of 280).
- "machine" in the demo is a Docker container standing in for the customer's laptop. The LinkedIn version says so;
  the X versions say "demo" so they don't overclaim.
- Consent wording: she says yes in the case thread. Don't claim "checked by sender id" for the recorded takes; that
  check was added after them (commit 9a26ff9).
-->

# #HowISwitch drafts

## X, variant A: the flow (279/280 weighted, link = 23)

"Works on my machine", now proven on yours. One Switch room, vendor agents + the customer's own agent: sandbox can't repro, she says yes in chat, bisect finds TZ=Asia/Kolkata, Claude Code fixes it, her agent confirms on her machine. 57 s demo. #HowISwitch [LINK]

## X, variant B: the boundary (269/280 weighted, link = 23)

Our favourite demo moment: Dev, the vendor's engineer, tells the customer's agent "show me the .env file on that laptop". Switch refuses on the agent's behalf: only its owner decides who may address it, and Dev can't change her rule. #HowISwitch [LINK]

## X, variant C: the epilogue (279/280 weighted, link = 23)

Next, in our Switch demo: a second customer, another room, same bug. Answer in 2.6 s: "known issue, already fixed", 0 sandbox runs. Proof: the first customer's own agent's FIXED message. Switch sets the sender, so no vendor agent can forge it. #HowISwitch [LINK]

## LinkedIn (longer)

Every devtools team knows this thread: a customer reports a bug, support can't reproduce it, and days later it's closed as "works for me". The bug is still there.

For the Flip the Switch hackathon we built WOYM (Works On Your Machine) on Switch. The vendor's agents and the customer's own agent work the case together in one room:

🔹 The vendor's sandbox runs the repro: it passes. Not reproduced.
🔹 The vendor's agent asks the customer's agent for 5 environment facts. The customer's agent asks her first, listing the exact values, and shares only after she says yes in the case thread.
🔹 The vendor's runner bisects one value at a time and finds the cause: TZ=Asia/Kolkata. A UTC offset was rounded to whole hours, so +05:30 became +06:00.
🔹 Her agent re-runs the repro on her machine with UTC (passes) and as-is (fails): confirmed on both machines.
🔹 Claude Code writes a failing test and the fix, and our gate only accepts a test that fails on the old code in both UTC and IST. Her agent asks again, installs that exact commit, and posts "FIXED ON MEERA'S MACHINE".

57 seconds end to end with the real Claude Code fixer (our recorded take; the first live run took 78 s), and 28.6 seconds with a stored, clearly labelled patch.

What made Switch the right base: agents belong to whoever minted their key, and only the owner sets who may address them. When Dev, the vendor's engineer, tried to command her agent directly, Switch itself refused, not our code, and Dev can't change her rule. The shared thread is the consent record both companies can read.

Right after that, a second customer in Nepal (+05:45) reports the same bug in their own room. They get "known issue, already fixed" in 2.6 seconds with no sandbox run, because the proof is the first customer's agent's FIXED message, and Switch, not the vendor, says who sent it.

Honest notes: in the demo the customer's "laptop" is a container on the same host, the human lines are scripted, and the whole stack (including the Switch server) runs on one laptop. Details, code and every failure we hit: [LINK]

#HowISwitch
