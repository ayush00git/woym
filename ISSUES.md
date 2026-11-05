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
