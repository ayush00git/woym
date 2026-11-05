# Switch notes (platform, switch-ba)

How WOYM talks to Switch 0.31.0. Owned by platform. Every claim has a tag:

- **[src]** read in the source at tag `switch-v0.31.0`, not yet run;
- **[live]** confirmed against the local stack by `scripts/killtest/killtest.py` (evidence in `var/killtest/`);
- **[assumed]** neither.

Status: the kill test has **not run yet** (the stack start is waiting on the user's approval). Everything below is [src] until this line changes.

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
- `sender_kind` is present only when the Switch platform posted it; otherwise it's **absent**. Agent vs human is NOT on the event [src]; it comes from `list_participants` (`type`).
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
| `post_message` | `body, thread_id=None` | `{"event_id"}`; `@name` in the body addresses nobody (per docstring) |
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
