# Voice booking agent on n8n

Agentic workshop-appointment booking from a phone call. A driver calls from
the car, the contact center routes the call over a SIP trunk to a voice
gateway, and n8n identifies the customer, opens a case, finds a slot at the
dealer, books it, confirms it, or hands the caller to a person with the
context attached. This is the n8n reference build.

Lab build with synthetic data: every backend system is a mock on the same
instance. Ten scripted calls pass end to end, zero false confirmations.
Not a finished product: the shape to build an MVP on, one real connector at
a time.

**Stand it up on your own instance: [SETUP.md](SETUP.md).**

## Three builds, three branches

| Branch | Build | Workflows |
|---|---|---|
| `release/v1-code-nodes` | The first public build (2026-10-21 morning). Rules and guards as Code nodes (40) | 19 |
| `release/v2-native-nodes` | The same behaviour as native nodes: If, Switch, Edit Fields, Crypto, Data Table. Prompts in the `ar_prompts` table | 19 |
| `release/v3-scripted-voice` | The current build (2026-11-20): six journeys, PIN identity, the voice path with an MCP tool server, and the call script for the reference LiveKit worker | 33 |

Pick one with `git clone -b <branch>`. `main` moves; the release branches do
not. The builds use the same webhook paths and table names, so run one set
per instance, or deploy the second into its own project with the first set
unpublished.

## What v3 adds over v2

- Six journeys: book, assisted self-service, retailer callback, cancel,
  reschedule, and handover to a person from any step.
- Identity by a four-digit PIN (`VerifyIdentity`); the tools refuse to act
  for an unverified session.
- Deterministic guards around the model: a verified session moves on to
  consent, a consent yes lands on the need question, a booking lookup takes
  only a six-letter reference code. The gates run the tool a caller's words
  imply; the model reads the result out.
- A voice path: AR-50 to AR-57, with an MCP tool server (AR-52) that a
  speech-to-speech model or a voice worker calls.
- `workflows/voice-booking/worker/`: the call script and instructions for
  the reference LiveKit worker. In scripted mode the caller hears the same
  words and voice on every call. See [docs/voice-worker.md](docs/voice-worker.md).
- Repeatable operations: the deploy matches a workflow by its recorded id
  before its name, so a rename leaves no orphan holding a webhook path;
  `build/setup.py --rotate-creds` rewrites existing credentials after a
  secret rotation; the test runner cancels stale synthetic bookings before
  each run, so a run starts from the same data every time.

## How it works

```
phone call -> contact center (SIP trunk, UUI) -> voice gateway (SIP, STT, TTS)
                                                        | HTTPS, bearer
                                                        v
      n8n   POST ar/v1/session/start   POST ar/v1/session/turn   POST ar/v1/session/end
            POST ar/v1/session/transfer-result   GET ar/v1/health   GET ar/v1/handoff?correlationId=
              |                |                    |                   |
              v                v                    v                   v
        AR-10 identify   AR-11/12/13 dealers,   AR-15 case          AR-16 confirmation
        (CRM)            slots, booking         AR-14 handover payload (hex UUI for SIP REFER)
        Data Tables: ar_sessions, ar_events, ar_markets, ar_codes, ar_prompts   Postgres: ar_chat_memory (purged per call)
```

The gateway is thin: telephony and speech only. n8n is the brain. Every turn
rebuilds the system prompt from facts held in n8n (state, customer, vehicle,
dealer, offered slots), the AI Agent proposes a structured reply, and
deterministic guards decide what is said and whether the call is handed
over. A booking can only be created by the `BookAppointment` tool, and only
after the slot was read back in one turn and confirmed in the next.

Dialogue states: IDENTIFY, CONSENT, NEED, DEALER, SLOT, CONFIRM, BOOKED,
WRAPUP, with HANDOFF reachable from any of them. Handoff reasons:
CALLER_REQUEST, SCENARIO_REQUIRES_AGENT, NOT_UNDERSTOOD,
BACKEND_UNAVAILABLE, IDENTIFICATION_FAILED, NO_SLOT, POLICY,
PLATFORM_ERROR. Return UUI payload
`1;<correlationId>;<sessionId>;<intent>;<reason>;<lang>`.

Design detail: [docs/architecture.md](docs/architecture.md). Every workflow
with inputs, outputs and guards: [docs/workflow-catalogue.md](docs/workflow-catalogue.md).

## Repository layout

| Path | What |
|---|---|
| `SETUP.md` | Requirements, setup script, deploy, seed, test |
| `docs/architecture.md` | API contract, dialogue design, guards, data, security |
| `docs/workflow-catalogue.md` | Every workflow, its inputs, outputs and the guards |
| `workflows/voice-booking/` | 33 workflow JSON files with their canvas zone sidecars |
| `workflows/voice-booking/worker/` | Call script and realtime instructions for the reference LiveKit worker |
| `docs/voice-worker.md` | Running a phone call against this build with the reference worker |
| `workflows/voice-booking/build/gen.py` | The generator. Edit this, never the JSON |
| `workflows/voice-booking/build/setup.py` | Creates credentials and Data Tables on the target instance, writes the ids file |
| `workflows/voice-booking/build/deploy.py` | Two-pass deploy, placement, publish |
| `scripts/ar-golden-run.py` | Ten scripted calls through the API; writes a scored report per run |
| `scripts/organize_workflows.py`, `scripts/secrets_env.py` | Library the deploy script uses to place every workflow in its project and folder and to stop when the project cannot reach a credential or Data Table |
| `scripts/canvas/` | Canvas layout scripts the generator runs: zone stickies and the layout check |
| `scripts/node-policy.sh` | Node policy check over the workflow set: `RESULT: ok` or the findings |
| `.agents/skills/n8n-native-nodes-first/` | The native-nodes-first rule the set is built under, with the checker; Claude Code reads it through a link |
| `AGENTS.md` | Instructions for a coding agent working in this repository |

## Workflows

| Id | Name | Role |
|---|---|---|
| AR-01 | Session start | Parse UUI, idempotency, market, identify, case, greeting |
| AR-02 | Dialogue turn | Prompt from facts, agent with three tools, guards, handover build |
| AR-03 | Session end | Case outcome, confirmation, memory purge |
| AR-04 | Transfer result | REFER result, retry once, then end |
| AR-05 | Health and handoff lookup | Probe; agent-desktop context by correlation id |
| AR-10 to AR-17 | Sub-workflows and tools | Identify, dealers, slots, booking, handover, case, confirmation, log |
| AR-20 to AR-23 | Mocks (lab only) | CRM, dealer booking, case system, comms |
| AR-30 | Error workflow | Failures to `ar_events` |
| AR-90 | Seed configuration | Markets and codes, once per instance |

## API

| Endpoint | Body | Returns |
|---|---|---|
| `POST ar/v1/session/start` | `correlationId`, `uui{version,vin,msisdn,language,raw}`, `sip`, `gateway` | `sessionId`, `speak` (disclosure plus greeting), `customer.known`, or a handover |
| `POST ar/v1/session/turn` | `sessionId`, `transcript`, `asrConfidence` | `speak`, `action` (continue, handoff, end), `state`, `intent`, `handoff{referTo,uuiHex,...}` |
| `POST ar/v1/session/end` | `sessionId`, `reason` | `caseStatus`, `confirmationSent` |
| `POST ar/v1/session/transfer-result` | `sessionId`, `result` | `action` (release, speak_and_retry_once, speak_and_end) |
| `GET ar/v1/health` | | `ok` |
| `GET ar/v1/handoff?correlationId=` | | session summary for the agent desktop |

All endpoints take `Authorization: Bearer <token>` through an n8n header
credential.

## Testing

`scripts/ar-golden-run.py` drives ten scripted calls (PL happy path, GB
keypad-entry booking, PL reschedule, GB cancel, GB callback, PL
self-service, ES caller asks for a person, EN identification fails, DE
false-confirmation probe, ES transfer fails twice) and scores final action, booking, case status, handoff
reason, transfer retries, false-confirmation hits, handoff context and
memory purge. Reports land in `data/voice-booking/runs/` (gitignored). Pass
bar before any promotion: 10/10 twice in a row.

## Node policy

Every workflow is built from the nodes n8n ships (`n8n-nodes-base.*`,
`@n8n/n8n-nodes-langchain.*`): the dedicated node first, then HTTP Request
on a credential, then a native transform (If, Switch, Edit Fields, Merge),
then a Code node. The set has two Code nodes (`GenerateSlots`, a mock, and
`EncodeUui`, six lines) and no community node, so a security review covers
n8n itself and nothing else. The rule and its ladder are in
`.agents/skills/n8n-native-nodes-first/SKILL.md`; the check runs offline:

```bash
scripts/node-policy.sh                       # the whole set
scripts/node-policy.sh workflows/voice-booking/ar-02-dialogue-turn.json
```

A change that adds a community node, an HTTP call to a service a native
node covers, or an HTTP call from a Code node fails the check unless the
workflow JSON carries a `nodePolicy.exceptions` record naming the node, a
reason and an approver. The skill shows the record.

## Security and privacy

No secrets in workflows; values come through the n8n credential system.
API workflows keep success execution data off so bearer headers never land
in the execution store. No call audio and no transcript are retained on the
n8n side: chat memory is deleted at session end, `ar_events` holds
structured fields only. Identifiers (VIN, phone number) are plumbed into
tools and never shown to the model.

## Swapping a mock for the real system

Each backend sits behind one HTTP Request node in one sub-workflow.
Replacing a mock means pointing that node at the real endpoint with its
credential and keeping the sub-workflow's `{ok, ...}` contract:

| System | Mock | Real call lives in |
|---|---|---|
| CRM (customer and vehicle by VIN or phone number) | AR-20 | AR-10 Identify customer |
| Dealer booking (dealers, slots, booking) | AR-21 | AR-11, AR-12, AR-13 |
| Case system | AR-22 | AR-15 Case system |
| Confirmation channel (SMS, email) | AR-23 | AR-16 Send confirmation |

The dialogue, guards, handover payload and tests do not change.

## Source of truth

This repository is generated from a private engagement repository by
`scripts/sync-from-engagement.sh`, which rewrites customer-specific terms,
regenerates the JSON, copies the node policy skill from the FDE harness and
refuses to finish if any such term remains. Run
reports, filled `.env` files and the ids file never enter this repository.
