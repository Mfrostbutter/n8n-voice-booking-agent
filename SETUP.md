# Stand the voice booking workflows up on your own n8n instance

Four steps: prepare the instance, run the setup script, deploy, seed and
test. Everything is driven from a terminal with Python 3.9 or later and
nothing else installed. The scripts talk to n8n over its public REST API.

## 1. What the instance needs

| Requirement | Why |
|---|---|
| n8n 2.35 or later | Data Tables, projects and folders, and `POST /workflows/{id}/publish` on the public API |
| An API key | Settings > n8n API. The key needs workflow, credential, project, tag and data-table scopes. An owner or admin key has them all |
| A team project (optional) | The set is placed in the project `Voice Booking`, folder `Voice Booking Agent`. Set `AR_PROJECT` and `AR_FOLDER` to your own names, or `AR_PROJECT=personal` on an instance without team projects |
| A Postgres database | Chat memory for the dialogue agent. One table, `ar_chat_memory`, created by the Postgres Chat Memory node on first use. Memory is deleted at session end |
| An OpenAI-compatible model endpoint | Any `/v1/chat/completions` endpoint with tool calling: OpenAI, Azure OpenAI, an internal model gateway, or a local server. The model needs tool calling and structured output |
| A bearer token for the gateway | Any string you choose. The voice gateway (or the test runner) presents it as `Authorization: Bearer <token>` on every call |

The instance must be able to reach itself over HTTP: the mocks are webhook
workflows on the same instance, called by the sub-workflows at
`<AR_SELF_URL>/webhook/ar-mock/...`. The instance's public URL works; inside
a container `http://localhost:5678` also works.

## 2. Setup: credentials, Data Tables, ids file

```bash
git clone -b release/v2-native-nodes <this repository>   # or release/v1-code-nodes
cd <repository>/workflows/voice-booking

export N8N_URL=https://n8n.example.com        # no trailing slash
export N8N_API_KEY=...                        # from Settings > n8n API
export AR_GATEWAY_TOKEN=...                   # the bearer the gateway will send
export AR_PG_HOST=... AR_PG_DATABASE=... AR_PG_USER=... AR_PG_PASSWORD=...
export AR_PG_PORT=5432 AR_PG_SSL=disable      # ssl: disable | allow | require
export AR_MODEL_API_KEY=...                   # key for the model endpoint
export AR_MODEL_BASE_URL=https://.../v1       # endpoint base, ending in /v1
export AR_MODEL=gpt-4.1                       # model id the endpoint expects
# export AR_PROJECT=personal                  # only without team projects

python3 build/setup.py --dry-run              # shows what would be created
python3 build/setup.py
```

The setup script creates three credentials (`AR gateway bearer`, `AR chat
memory Postgres`, `AR model endpoint`; plus `AR OpenAI Realtime` and `AR MCP
bearer` when `AR_OPENAI_API_KEY` and `AR_PROOF_MCP_TOKEN` are exported, for the
voice proof), ten Data Tables (`ar_sessions`, `ar_events`, `ar_markets`,
`ar_codes`, `ar_prompts`, `ar_mock_bookings`, `ar_mock_cases`,
`ar_mock_customers`, `ar_mock_dealers`, `ar_proof_config`) and writes
`lab-ids.json`, the per-instance configuration
the generator and the deploy script read. Re-running reuses what exists by
name; `python3 build/setup.py --tables-only` adds a new table or column to
an instance that already runs the set, leaving credentials alone. Secret values
go to n8n once and are never written to disk; keep them in your secret
manager and export them for the session only.

## 3. Deploy

```bash
scripts/node-policy.sh      # from the repository root; must print RESULT: ok
python3 build/deploy.py
```

The first line is the node policy check (README, "Node policy"). It passes
on the shipped set; run it again after any change to `build/gen.py`.

Two passes: the first creates the thirty-three workflows by name and learns their
ids, the second rewrites every sub-workflow reference and publishes. Callers
that reference a sub-workflow not yet published fail to publish in pass one;
that is expected, pass two resolves it. The last line is either `all deployed
workflows published` or the list of what is not, with the n8n error.

Every workflow lands in the project and folder from the ids file with the
tag `voice-booking` and a role tag. The deploy stops before touching anything
if the project cannot reach one of the credentials or Data Tables the nodes
use.

Redeploy a subset after a change: `python3 build/deploy.py --only
ar-02-dialogue-turn,ar-13-book-appointment`. Edit `build/gen.py`, never the
JSON; the generator is the readable form and the JSON is its output.

## 4. Seed and test

```bash
# markets (PL, ES, DE, GB), the intent and handoff codes, and the prompts
# (goals, rules and every spoken line per language), once per instance; safe to re-run
curl -s -X POST -H "Authorization: Bearer $AR_GATEWAY_TOKEN" "$N8N_URL/webhook/ar/v1/admin/seed"
# or: python3 scripts/ar-seed.py (reads AR_GATEWAY_TOKEN and N8N_URL from the environment)

# health
curl -s -H "Authorization: Bearer $AR_GATEWAY_TOKEN" "$N8N_URL/webhook/ar/v1/health"

# the five scripted calls
cd ../..
python3 scripts/ar-golden-run.py
```

The runner drives five calls through the API (Polish happy path, Spanish
caller asks for a person, English unknown VIN, German false-confirmation
probe, Spanish transfer fails twice) and scores final action, booking, case
status, handoff reason, transfer retries, false confirmations and handoff
context. Reports land in `data/voice-booking/runs/`. The pass bar is 5/5.

The memory-purge check needs a way to count rows in `ar_chat_memory`. Give
the runner a command that prints the count for a session, for example:

```bash
export AR_MEMORY_COUNT_CMD="psql postgresql://user:pass@host/db -tAc \"SELECT count(*) FROM ar_chat_memory WHERE session_id = '{sessionId}'\""
```

Without it the report says the check did not run and the scenario is scored
on everything else.

One call by hand:

```bash
C=$(uuidgen | tr A-Z a-z)
curl -s -H "Authorization: Bearer $AR_GATEWAY_TOKEN" -H 'Content-Type: application/json' \
  -d "{\"correlationId\":\"$C\",\"uui\":{\"version\":\"1\",\"raw\":\"1;$C;TST2970461A123456;+48601234567;pl\"}}" \
  "$N8N_URL/webhook/ar/v1/session/start"
```

Then `session/turn` with the returned `sessionId` and a transcript, and
`session/end`. Synthetic VINs known to the mock CRM: `TST2970461A123456`
(PL), `TST2130421A654321` (ES), `TST1770871N998877` (DE), `TST2476011J112233`
(GB). Any other VIN is an unknown customer and hands over.

## Swapping a mock for the real system

Each backend sits behind one HTTP Request node in one sub-workflow.
Replacing a mock means pointing that node at the real endpoint with its
credential and keeping the sub-workflow's contract:

| System | Mock | Real call lives in |
|---|---|---|
| CRM (customer and vehicle by VIN or phone number) | AR-20 | AR-10 Identify customer |
| Dealer booking (dealers, slots, booking) | AR-21 | AR-11, AR-12, AR-13 |
| Case system | AR-22 | AR-15 Case system |
| Confirmation channel (SMS, email) | AR-23 | AR-16 Send confirmation |

The dialogue, guards, handover payload and tests do not change. Keep the
`{ok, ...}` return shape so callers keep branching on it.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `publish-failed(400) ... references workflow ... not published` in pass 1 | Expected in pass 1 | Read the pass 2 lines |
| `project "Voice Booking" does not exist` | No team project of that name and the key cannot create one | Create it in the UI, set `AR_PROJECT` to an existing project, or `AR_PROJECT=personal` |
| `credential scope: ... cannot take` | A credential with the same name exists in another project | Rename or move it, then rerun setup |
| `Data table ... could not be found in this project` at runtime | Tables live in a different project than the workflows | Rerun setup with the same `AR_PROJECT`, then deploy |
| Health returns 401 | Token mismatch | The credential value is `Bearer <AR_GATEWAY_TOKEN>`; the caller must send exactly that |
| Agent replies but never calls a tool | Model without tool calling | Use a model that supports function calling and JSON output |
| `session/start` returns a handoff with `BACKEND_UNAVAILABLE` | The instance cannot call its own mocks | Set `AR_SELF_URL` to a URL the n8n process can reach, rerun setup, redeploy |
