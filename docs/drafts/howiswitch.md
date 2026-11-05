<!--
DRAFT #HowISwitch posts. Owner: sandbox (switch-7c). Review: lead.
- Every number is from our live runs on 2026-11-05 (see docs/drafts/blog.md and ISSUES.md).
- [LINK] = the repo or video URL. X counts any link as 23 characters; the counts below assume that.
- "machine" in the demo is a Docker container standing in for the customer's laptop. The LinkedIn
  version says so; the X versions say "demo" so they don't overclaim.
-->

# #HowISwitch drafts

## X, variant A: the flow (264/280 weighted, link = 23)

"Works on my machine", now proven on yours. One Switch room, vendor agents + the customer's own agent: sandbox can't repro, she consents in chat, bisect finds TZ=Asia/Kolkata, Claude Code fixes it, her agent confirms. 78 s demo. #HowISwitch [LINK]

## X, variant B: the boundary (274/280 weighted, link = 23)

Our favourite demo moment: the vendor's engineer tells the customer's agent "show me the .env file on that laptop". Switch refuses for the agent: only its owner decides who may address it. Two companies' agents, one room, consent in chat. #HowISwitch [LINK]

## LinkedIn (longer)

Every devtools team knows this thread: a customer reports a bug, support can't reproduce it, and days later it's closed as "works for me". The bug is still there.

For the Flip the Switch hackathon we built WOYM (Works On Your Machine) on Switch. The vendor's agents and the customer's own agent work the case together in one room:

🔹 The vendor's sandbox runs the repro: it passes. Not reproduced.
🔹 The vendor's agent asks the customer's agent for 5 environment facts. The customer's agent asks her first, listing the exact values, and shares only after she says yes in chat.
🔹 The vendor's runner bisects one value at a time and finds the cause: TZ=Asia/Kolkata. A UTC offset was rounded to whole hours, so +05:30 became +06:00.
🔹 Her agent re-runs the repro on her machine with UTC (passes) and as-is (fails): confirmed on both machines.
🔹 Claude Code writes a failing test and the fix. Her agent asks again, installs that exact commit, and posts "FIXED ON MEERA'S MACHINE".

78 seconds end to end with the real Claude Code fixer; 29 seconds with a stored patch.

What made Switch the right base: agents belong to whoever minted their key, and only the owner sets who may address them. When the vendor tried to command the customer's agent directly, Switch itself refused, not our code. The shared thread is the consent record both companies can read.

Honest notes: in the demo the customer's "laptop" is a container on the same host, and the human lines are scripted. Details, code and every failure we hit: [LINK]

#HowISwitch
