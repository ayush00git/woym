# Switch notes (platform, switch-ba)

How WOYM talks to Switch 0.31.0. Owned by platform. Every claim has a tag:

- **[src]** read in the source at tag `switch-v0.31.0`, not yet run;
- **[live]** confirmed against the local stack by `scripts/killtest/killtest.py` (evidence in `var/killtest/`);
- **[assumed]** neither.

Status: **kill test ran 2026-11-05 against the live 0.31.0 stack. Result: GO.** `scripts/setup.sh` and a live client smoke test also ran clean. Raw evidence: `var/killtest/results.json`, `events.jsonl`, `ops.json`, `read_context.json`. Section 0 summarises what was confirmed; the [src] tags further down were written before the run, and section 0 wins where they disagree.

## 0. Kill test results [live]

| # | Question | Result |
|---|---|---|
| 1 | Gateway users + per-owner registration keys by API | ✅ `POST /gateway/users`, `POST /gateway/api-keys`. No browser |
| 2 | Probe agent per owner: register, WS connect, survive pings | ✅ `POST /agents` (custom). Ping every 2 s, pong with cursor, 14 s with no eviction. 53 ops |
| 3 | Cross-owner `send_targeted` | ✅ default policy → `not_permitted` (the message still posts, `addressed:false`, and the target auto-replies "You're not permitted to direct messages to me…"). After Meera's rule `{agents:[<kt-dev uuid>]}` → `live`, `addressed:true`. Dev changing Meera's agent's policy → 403. Reverse direction (meera's agent → dev's agent) → `not_permitted` |
| 3b | Agent changes its own addressing | ✅ refused: `update_agent_detail(self, addressing=anyone)` → 403 |
| 4 | Cross-owner agent into the room | ✅ `invite_agent_to_room` by dev's agent → 200 even under Meera's default policy. In setup, Meera's own `POST /gateway/rooms/{id}/agents` → 200 |
| 5 | Human thread reply reaches the agent | ✅ delivered (filter=all) with `addressed:false`, `thread_id` = the root's **Switch** id (`sw_…`), `sender` = `@switch-mattermost-<bridge>-meera:localhost`, `sender_name` = `meera` |
| 5b | `!follow-ups off` | Already off: Switch answered "Following the conversation isn't switched on for this Switch server". Harmless |
| 6 | Agent edits its own message | ❌ no op (only `edit_role`). Re-post cards |
| 7 | MM users dev/meera with PATs | ✅ after `PUT /api/v4/config/patch` EnableUserAccessTokens + role `system_user_access_token` |
| 8 | Link MM account → Switch user by API | ✅ `POST /gateway/collaborations/{bridge}/identities` → 200. Afterwards `owner:true` matches Meera's messages (`addressed:true`); before linking it did not |
| 9 | Agent reply into a human thread | ✅ `post_message(thread_id=sw_…)` lands under the Mattermost root post |
| 10 | One agent process in two rooms | ✅ two `WsSwitchClient` objects (same credentials, one connection each, one per room): both rooms receive, posts land in the right room, the main room's claim is never taken. ❌ one connection switching rooms: with `scope=single` the side room goes deaf after switching back; with `scope=all` a concurrent post can race into the wrong room (`scripts/killtest/two_rooms.py`, `var/killtest/two_rooms.json`) |
| 11 | `start_from=<seq>` on reconnect | ✅ **exclusive**: the event at `<seq>` is not replayed, later ones are (kt-dev: …225, …226, reconnect at …226 → only …228). Clients still drop repeated message ids as a guard |

### Surprises that matter for C and B
- **`@name` inside `post_message` DOES address** (`addressed:true` at the target). The brief said it addresses nobody. Keep using `send_targeted` (it returns statuses), and don't put `@agent` in prose you `post()` unless you mean it.
- **Refused addressing produces a visible auto-reply** in the thread from the target agent, posted by Switch on its behalf, e.g. `@dev I only take instructions from my owner, and this chat account isn't…`. On camera, nobody should @mention `@acme-env` unless the rule admits them.
- Switch posts a **join greeting for each agent when it's added to a room** (randomised wording: "Hey! I'm on standby — @triage to get started.", "Hi! Mention @kt-dev to get my attention."). It posts at room creation, in the same second, whether or not the agent is connected [live, 2 rooms]. So greetings always sit at the top of a fresh room.

## 0b. Showing owners on screen [live]
- **Display name:** the owner calls `PUT /gateway/agents/{id}/name {"name": <same>, "display_name": "acme-env · owner: meera (Acme)"}` → 200, and Switch renames the Mattermost bot (`first_name` / bot `display_name`). Mattermost `TeamSettings.TeammateNameDisplay` must be `full_name` (it already is; setup enforces it).
- **Avatar:** `PUT /gateway/agents/{id}/icon {icon_url}` → 200 but did NOT reach the MM bot (no picture update within 6 s; Switch seems to set the bot icon only when it creates the identity). Instead setup uploads a PNG straight to Mattermost as admin: `POST /api/v4/users/{bot_user_id}/image` → 200. Vendor agents are blue, acme-env is amber. Re-run setup after a stack restart in case Switch resets the images [assumed].
- setup.py applies both on every run (`LABELS` in woym/switch/setup.py).

## 0c. Preflight and rooms
- `uv run python scripts/status.py [--json]` is read-only: health, live-run lock, active room, each agent's connection (Gateway `rooms[].status=="live"` in the active room), owner, addressing rule in words, and the latest case step. Exit 0 = READY. It never connects as an agent: **a second connection for the same agent takes over its room claim** from the running process.
- `uv run python -m woym.switch.setup --fresh-room <name> [--activate]` creates a new room with the same agents, rule and links under `rooms.<name>` in credentials.json. The active room changes only with `--activate`. It waits until all 4 join greetings are in.
- `uv run python scripts/record.py [-- <demo.py args>]` records media/raw/take-<n>.webm (1920x1080, VP8, 25 fps) plus a still every 10 s in take-<n>-frames/. It uses headless Chrome over CDP, the viewer is dev, and it opens the new case thread full screen. `--attach --seconds N` only watches; `--headed` shows the window; `--zoom` defaults to 1.25.
- **Customer rooms (A10)**: `uv run python -m woym.switch.setup --customer acme-nepal` reads config.CUSTOMERS["acme-nepal"] = (kiran, nepal-env, kiran-laptop) and creates or reuses:
  - Gateway user kiran + registration key, MM user kiran + PAT, identity link, and `humans.kiran.switch_participant_id`;
  - agent nepal-env owned by kiran (display "nepal-env · owner: kiran (Acme Nepal)", amber), with kiran's rule {owner, owner_agents} + {agents:[triage uuid]};
  - room `acme-nepal` (triage, runner, fixer, nepal-env; humans dev + kiran; NOT acme-env) stored as `rooms["acme-nepal"]` = {id, name, channel_id, bridge_id, customer, customer_agent}.
  It never changes the active room. Re-runs reuse the room [live]. `humans.py kiran …` posts in rooms["acme-nepal"] by default; `--room <key>` picks any room explicitly.
- **Agent sender ids match across history and events** [live, kill test]: read_context `sender` == live event `sender` for agent posts (kt-dev `@switch-agent-5cd25f69:localhost` and kt-meera `@switch-agent-1d815afe:localhost` in both var/killtest/read_context.json and events.jsonl). These are opaque short ids, not names or UUIDs, and they change when the stack is wiped. Human ids also match across both [live, smoke test].
- **Live-run lock** (contracts §8): while `var/run/live.lock` holds a live pid, `creds.save()` and every setup mode refuse. A stale lock (dead pid) is removed automatically.
- `list_participants` ids are **UUIDs** (agent id / user id), NOT the event `sender` ids. Map kinds by `name`.
- Message ids and `thread_id`s are Switch ids (`sw_<hex>`), **not** Mattermost post ids. `humans.py` prints the Mattermost post id. Use that only for `humans.py --thread`, never to compare with an agent's `thread_id`.
- Switch's own notices come from `@switch-admin:localhost` with `sender_kind: null`. The client maps them to `sender_kind="system"`.
- `targeted` messages arrive with `@target ` prepended to the body.
- Event `sequence`/`id` values are huge (~9e15). They fit a Python int.
- Human ids are stable across linking (same `sender` before and after).

### Human participant ids (for sender checks)
`var/switch/credentials.json` → `humans.<h>.switch_participant_id`, e.g. `@switch-mattermost-8396fa22-…-meera:localhost`. setup.py reads it from room history (exact `sender` where `sender_name == h`), never builds it. It equals `MessageEvent.sender_id` for that human's messages [live, smoke test].

## 1. Stack

- `infra/switch/up.sh` runs `gen-env.sh`, which creates `.env` once, then `docker compose up -d` with profiles `collab,gateway`. down.sh stops it; pass `-v` to also wipe volumes.
- Ports (127.0.0.1 only):
  - Switch API and agent bridge: 8000;
  - Gateway JSON API: under `:8000/gateway/...`;
  - Gateway UI: 3000;
  - Mattermost: 8065;
  - Postgres: 55432.
- Credentials are in `infra/switch/.env`: `GATEWAY_ADMIN_EMAIL/PASSWORD`, `MATTERMOST_ADMIN_USER/PASSWORD`, `AGENT_REGISTRATION_TOKEN` (the bootstrap key, whose agents belong to a separate non-admin account, so we don't use it), and the Mattermost team `switch`.

## 2. People and keys (Gateway JSON API, cookie auth) [live]

All of these ran in the kill test, setup.py and the A9 cold-stack run (§0 #1, #4, #8); the table is [live] unless a row says otherwise.

| Step | Call | Notes |
|---|---|---|
| Log in | `POST /gateway/auth/login` `{email, password}` | Returns the `switch_auth` cookie. Use one cookie jar per user |
| Create user (admin) | `POST /gateway/users` `{name, email, password, role:"user"}` | 409 if the email exists |
| Mint a registration key (as that user) | `POST /gateway/api-keys` `{label}` | `{id, label, key, created_at}`. `key` is shown once. Agents registered with it are **owned by that user** |
| Agent addressing rule (owner or admin only) | `PUT /gateway/agents/{agent_id}/addressing-policy` `{"policy": {"rules": [...]}}` or `{"policy": null}` (open) | 403 for non-owners (dev on Meera's agent → 403 [live]) |
| Add agents to a room | `POST /gateway/rooms/{room_id}/agents` `{agent_ids:[...]}` | Needs room write permission |
| Create a bridged room | `POST /gateway/rooms` `{name, description, bridge_id, agent_names, user_names, channel_type:"channel_public", internal_only:false}` | Switch creates the Mattermost channel and adds the bots and users |
| Mattermost bridge id | `GET /gateway/collaborations` | Row with `bridge_type=="mattermost"` |
| Link a Mattermost account to the signed-in Switch user | `POST /gateway/collaborations/{bridge_id}/identities` `{external_user_id, username, user_id:null}` | **API, not Console-only** |

### Addressing rules (`core/switch_core/addressing.py`; [src] unless marked)
- `rules: []` means open to anyone (but platform senders are always denied unless a rule has `platform:true`).
- With rules present, deny by default: an attempt is allowed if **any** rule matches.
- New agents start on the default policy: owner + owner's agents.
- A rule has these fields: `rooms`, `room_groups`, `users`, `agents` (each `"*"` or a list of ids; `[]` = none), plus `owner`, `owner_agents` and `platform` (booleans).
  - `agents` lists **agent UUIDs** (the sender agent's id) [live: kt-dev's UUID admitted it].
  - `users` lists human participant ids. Live human ids look like `@switch-mattermost-<bridge>-<username>:localhost` (§0c). Whether `users` matches that form is **unverified**: WOYM never uses a `users` rule; it uses `owner: true` instead.
  - `owner: true` matches a human only if the owner has linked that chat account (see the identities route above) [live, §0 #8].
- Planned policy for `@acme-env`:
  ```json
  {"policy": {"rules": [
    {"owner": true, "owner_agents": true, "users": [], "agents": []},
    {"users": [], "agents": ["<triage agent uuid>"]}
  ]}}
  ```

## 3. Agent registration [live]

`POST /agents` with `Authorization: Bearer <registration key>`:
```json
{"name": "triage", "description": "...", "connector_type": "custom",
 "integration_profile": {"connection_model": "always_on", "message_exchange": true,
   "pre_invocation_mediation": [], "post_invocation_mediation": [], "event_reporting": []},
 "overwrite": true}
```
- Returns `200 {"id": "<uuid>", "api_key": "<shown once>"}`. Name regex `^[a-z0-9][a-z0-9._-]*$`. 409 on a duplicate name without `overwrite`.
- Registering also creates a Mattermost bot whose username is the agent name.
- `POST /agents/register-known` exists too, but only for known types (claude-code, codex, opencode…). We don't use it.

## 4. Connection: WebSocket [live]

```
WS /agents/{agent_id}/connection/ws
   ?connection_id=<uuid, generated once per CLIENT and reused on its reconnects; two clients in one process = two ids>
   &scope=single|all  &filter=all|addressed  &start_from=head|<seq>   (<seq> is exclusive [live, §0 #11])
   &rooms=<room_id>,<room_id>   &client=woym&client_version=0.1.0
Header: Authorization: Bearer <agent api_key>
```

**Server → client** frames are JSON `{"event": <kind>, "data": {...}, "id": <seq>}`. `id` is present only on domain events.

| event | data |
|---|---|
| `connection_state` | first frame: `connection_id, agent_id, scope, filter, rooms[], cursor, protocol, heartbeat_interval_seconds, server, client` |
| `ping` | `{}`, every heartbeat interval (2 s) |
| `message` | see below |
| `command` | `{type, room_id, payload:{command, args, user_id, user_name, thread_id}, sequence}` |
| `room_join` | `payload:{member, member_name, timestamp, listening}` |
| `gap` / `subscription_changed` / `room_released` | control frames, no `id` |
| `evicted` | `{code, reason, room_id}`. Reconnect unless `code=="taken_over"` |
| `refused` | `{status, detail}`, sent at open, then close code 4000+status |

**Client → server:** `{"type": "pong", "cursor": <highest id seen>}` answers each ping. This counts as the heartbeat. Miss it and the connection lapses after 6 s TTL. Anything else sent is ignored.

### `message` frame (what C's fakes should emit) [live, copied from var/killtest/events.jsonl]
```json
{"event": "message", "id": 9007194959773701,
 "data": {"type": "message", "room_id": "642b1f8c-5a0c-46c9-92e3-bd59b62a4246",
          "bridge_id": "8396fa22-5003-4a9c-82aa-acffecc47287",
          "channel_type": "channel_public", "sequence": 9007194959773701,
          "payload": {"addressed": true,
                      "sender": "@switch-agent-5cd25f69:localhost",
                      "sender_name": "kt-dev",
                      "sender_kind": null, "on_behalf_of": null,
                      "message_id": "sw_f16352e38dec4a6a9f4a32f89afd2da2",
                      "body": "@kt-meera kt plain post e7bb72", "timestamp": 1793884714851,
                      "thread_id": null, "attachments": [],
                      "opening_message": null, "sender_role": null}}}
```
- `sender_kind` is `null` unless the Switch platform posted it [live]. Agent vs human is NOT on the event; it comes from `list_participants` (`type`), matched by **name**. Extra live keys: `opening_message`, `sender_role` (null), and `missed` on the data object.
- `on_behalf_of` is absent unless a platform message speaks for a person.
- Treat `sender` ids as opaque.
- Mapping to `contracts.MessageEvent`:
  - `message_id`, `thread_id` and `text` (from `body`) come straight across;
  - `sender_id` = `sender`;
  - `sender_kind` = `"system"` if the payload says platform, else participants lookup (`agent`/`human`).

## 5. Operations ([live] for the ops WOYM uses; [src] for the rest)

`POST /agents/{id}/ops/{name}` with Bearer api_key and header `X-Switch-Connection-Id: <connection_id>`. The body is the raw args object. The response is `{"result": ...}`. Status codes: 404 unknown op, 400 bad args (extra keys rejected), 403 denied, 409 dead connection. The live list is `GET /agents/{id}/ops` (the kill test saves it to `var/killtest/ops.json`).

| op | args | result |
|---|---|---|
| `connect_to_room` | `room_id, include_general_instructions=true` | room context + participants; takes over the claim |
| `post_message` | `body, thread_id=None` | `{"event_id"}`; **`@name` in the body DOES address [live]** |
| `send_targeted_message` | `body, target_names=[...], target_roles=None, thread_id=None` | `{"event_id", "target_statuses": {name: "live"/"awaiting_manual_poll"/"no_session"/"disconnected"/"not_permitted"}}`; user targets are omitted; `not_permitted` **still posts** and the target answers in the room that it can't act |
| `read_context` | `limit=50, since=None, before=None, room_id=None` | `{"threads":[{"root":E,"replies":[E...]}], "truncated", "oldest_timestamp"}`, where E = `{id, kind, sender, sender_name, body, timestamp, attachments, sender_kind?}` |
| `list_participants` | none (connected room) | `[{id, name, type, status, alias}]` |
| `invite_agent_to_room` | `room_id, agent_name, include_subagents=false` | `{"ok": true}`; allowed if the target's policy admits you |
| `update_agent_detail` | `agent_id, addressing=…, …` | an agent loosening its OWN addressing → **403 [live, §0 #3b]**. Whether a same-owner agent may change another's addressing is **unverified** (the brief says yes; we never needed it) |

- `thread_id` arguments may be any message in the thread; they are normalised to the root.
- **No edit, redact or delete-message op exists.** Cards are re-posted.
- `POST /agents/{id}/message` `{room_id, content}` posts without a connection.

## 6. Mattermost (for humans.py) [live, §0 #7; used by setup.py and humans.py on every run]
- The admin logs in with `POST /api/v4/users/login` and gets the `Token` header.
- PATs need `PUT /api/v4/config/patch {"ServiceSettings":{"EnableUserAccessTokens":true}}` and the role `system_user system_user_access_token`. Then `POST /api/v4/users/{id}/tokens`.
- Post as a human: `POST /api/v4/posts {channel_id, message, root_id?}` with `Bearer <PAT>`.

## 7. Questions the kill test answered (all live; details in §0)
1. Cross-owner `send_targeted`: `not_permitted` under the default rule, `live` + addressed after Meera's rule → **yes** (§0 #3).
2. A human thread reply without a mention reaches the agent with `addressed: false`. `!follow-ups off` changes nothing because follow-ups are already off on this server (§0 #5, #5b).
3. `thread_id` on the event is the **Switch** id (`sw_…`), **not** the Mattermost root post id (§0 #5). humans.py prints Mattermost ids; don't compare the two.
4. After Meera links her MM account, `owner: true` matches her messages (§0 #8).
5. An agent changing its own addressing via `update_agent_detail` → **403** (§0 #3b).
