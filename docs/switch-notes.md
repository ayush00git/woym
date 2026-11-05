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

### Surprises that matter for C and B
- **`@name` inside `post_message` DOES address** (`addressed:true` at the target). The brief said it addresses nobody. Keep using `send_targeted` (it returns statuses), and don't put `@agent` in prose you `post()` unless you mean it.
- **Refused addressing produces a visible auto-reply** in the thread from the target agent, posted by Switch on its behalf, e.g. `@dev I only take instructions from my owner, and this chat account isn't…`. On camera, nobody should @mention `@acme-env` unless the rule admits them.
- Every agent posts **"Hi! Mention @<name> to get my attention."** when it joins a room. setup.sh triggers this once, at room creation.
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

## 2. People and keys (Gateway JSON API, cookie auth) [src]

| Step | Call | Notes |
|---|---|---|
| Log in | `POST /gateway/auth/login` `{email, password}` | Returns the `switch_auth` cookie. Use one cookie jar per user |
| Create user (admin) | `POST /gateway/users` `{name, email, password, role:"user"}` | 409 if the email exists |
| Mint a registration key (as that user) | `POST /gateway/api-keys` `{label}` | `{id, label, key, created_at}`. `key` is shown once. Agents registered with it are **owned by that user** |
| Agent addressing rule (owner or admin only) | `PUT /gateway/agents/{agent_id}/addressing-policy` `{"policy": {"rules": [...]}}` or `{"policy": null}` (open) | 403 for non-owners |
| Add agents to a room | `POST /gateway/rooms/{room_id}/agents` `{agent_ids:[...]}` | Needs room write permission |
| Create a bridged room | `POST /gateway/rooms` `{name, description, bridge_id, agent_names, user_names, channel_type:"channel_public", internal_only:false}` | Switch creates the Mattermost channel and adds the bots and users |
| Mattermost bridge id | `GET /gateway/collaborations` | Row with `bridge_type=="mattermost"` |
| Link a Mattermost account to the signed-in Switch user | `POST /gateway/collaborations/{bridge_id}/identities` `{external_user_id, username, user_id:null}` | **API, not Console-only** |

### Addressing rules [src] (`core/switch_core/addressing.py`)
- `rules: []` means open to anyone (but platform senders are always denied unless a rule has `platform:true`).
- With rules present, deny by default: an attempt is allowed if **any** rule matches.
- New agents start on the default policy: owner + owner's agents.
- A rule has these fields: `rooms`, `room_groups`, `users`, `agents` (each `"*"` or a list of ids; `[]` = none), plus `owner`, `owner_agents` and `platform` (booleans).
  - `agents` lists **agent UUIDs** (the sender agent's id).
  - `users` lists human participant ids (`@switch-user-…:localhost`).
  - `owner: true` matches a human only if the owner has linked that chat account (see the identities route above).
- Planned policy for `@acme-env`:
  ```json
  {"policy": {"rules": [
    {"owner": true, "owner_agents": true, "users": [], "agents": []},
    {"users": [], "agents": ["<triage agent uuid>"]}
  ]}}
  ```

## 3. Agent registration [src]

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

## 4. Connection: WebSocket [src]

```
WS /agents/{agent_id}/connection/ws
   ?connection_id=<uuid, generated once per process, reused on reconnect>
   &scope=single|all  &filter=all|addressed  &start_from=head|<seq>
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

### `message` frame (what C's fakes should emit)
```json
{"event": "message", "id": 4813,
 "data": {"type": "message", "room_id": "<switch room uuid>", "bridge_id": "<uuid|null>",
          "channel_type": "channel_public", "sequence": 4813,
          "payload": {"addressed": true,
                      "sender": "@switch-agent-ab12cd:localhost",
                      "sender_name": "triage",
                      "sender_kind": "platform",
                      "on_behalf_of": "...",
                      "message_id": "<id>", "body": "text", "timestamp": 1762350000000,
                      "thread_id": "<root id or null>", "attachments": []}}}
```
- `sender_kind` is `null` unless the Switch platform posted it [live]. Agent vs human is NOT on the event; it comes from `list_participants` (`type`), matched by **name**. Extra live keys: `opening_message`, `sender_role` (null), and `missed` on the data object.
- `on_behalf_of` is absent unless a platform message speaks for a person.
- Treat `sender` ids as opaque.
- Mapping to `contracts.MessageEvent`:
  - `message_id`, `thread_id` and `text` (from `body`) come straight across;
  - `sender_id` = `sender`;
  - `sender_kind` = `"system"` if the payload says platform, else participants lookup (`agent`/`human`).

## 5. Operations [src]

`POST /agents/{id}/ops/{name}` with Bearer api_key and header `X-Switch-Connection-Id: <connection_id>`. The body is the raw args object. The response is `{"result": ...}`. Status codes: 404 unknown op, 400 bad args (extra keys rejected), 403 denied, 409 dead connection. The live list is `GET /agents/{id}/ops` (the kill test saves it to `var/killtest/ops.json`).

| op | args | result |
|---|---|---|
| `connect_to_room` | `room_id, include_general_instructions=true` | room context + participants; takes over the claim |
| `post_message` | `body, thread_id=None` | `{"event_id"}`; **`@name` in the body DOES address [live]** |
| `send_targeted_message` | `body, target_names=[...], target_roles=None, thread_id=None` | `{"event_id", "target_statuses": {name: "live"/"awaiting_manual_poll"/"no_session"/"disconnected"/"not_permitted"}}`; user targets are omitted; `not_permitted` **still posts** and the target answers in the room that it can't act |
| `read_context` | `limit=50, since=None, before=None, room_id=None` | `{"threads":[{"root":E,"replies":[E...]}], "truncated", "oldest_timestamp"}`, where E = `{id, kind, sender, sender_name, body, timestamp, attachments, sender_kind?}` |
| `list_participants` | none (connected room) | `[{id, name, type, status, alias}]` |
| `invite_agent_to_room` | `room_id, agent_name, include_subagents=false` | `{"ok": true}`; allowed if the target's policy admits you |
| `update_agent_detail` | `agent_id, addressing=…, …` | for the owner or same-owner agents only (per brief, to be confirmed) |

- `thread_id` arguments may be any message in the thread; they are normalised to the root.
- **No edit, redact or delete-message op exists.** Cards are re-posted.
- `POST /agents/{id}/message` `{room_id, content}` posts without a connection.

## 6. Mattermost (for humans.py) [assumed until the kill test]
- The admin logs in with `POST /api/v4/users/login` and gets the `Token` header.
- PATs need `PUT /api/v4/config/patch {"ServiceSettings":{"EnableUserAccessTokens":true}}` and the role `system_user system_user_access_token`. Then `POST /api/v4/users/{id}/tokens`.
- Post as a human: `POST /api/v4/posts {channel_id, message, root_id?}` with `Bearer <PAT>`.

## 7. Open questions the kill test answers
1. Cross-owner `send_targeted`: `not_permitted` under the default rule, `live` after Meera's rule?
2. Does a human thread reply (no mention) reach the agent, and is it `addressed`? What changes with `!follow-ups off`?
3. Does `thread_id` on the event equal the Mattermost root post id?
4. Does linking Meera's MM account make `owner:true` match her messages?
5. Can an agent change its own addressing via `update_agent_detail`?
