# Running a phone call against this build with the reference LiveKit worker

The workflows here are the brain of the call. Audio (answer, listen, speak)
is the job of a voice worker outside n8n. This page wires the public
reference worker to this workflow set so a call says the same words every
time.

- Worker: https://github.com/Mfrostbutter/n8n-livekit-worker
- LiveKit node package for n8n (transfer, rooms, dispatch, webhook events):
  https://github.com/Mfrostbutter/n8n-nodes-livekit

Both are community code under the MIT licence, shared as is. Neither is an
official n8n product.

## What is in `workflows/voice-booking/worker/`

| File | What |
|---|---|
| `script.en.json` | The call script for scripted mode: the tools the model may pick, the n8n tool behind each, and one fixed line per outcome |
| `realtime-instructions.txt` | The instructions for realtime mode, where the model words its own replies |

## Why scripted mode

In scripted mode the speech model never speaks. For each caller utterance it
picks exactly one tool; the worker calls the matching n8n tool and speaks the
line the script holds for that outcome. The result:

- The same words and the same voice on every call. Lines are synthesized per
  sentence and cached on disk, so a repeated sentence is the same audio.
- Nothing the model writes on its own is spoken.
- A tool call that fails, or returns an outcome the script has no line for,
  is a scripted line too: `errorLine` ("The booking system is unavailable
  right now. Would you like to speak to a colleague?"). If n8n cannot be
  reached when the call starts, the worker speaks its own fallback line
  (`FALLBACK_LINE_<LANG>`). A backend outage is a spoken answer and an offer
  of a person, not silence.
- A change of wording is an edit to `script.en.json`, not a prompt change.

## What the instance needs

The set deployed and seeded as in [SETUP.md](../SETUP.md), with the two
optional voice credentials created: export `AR_OPENAI_API_KEY` and
`AR_PROOF_MCP_TOKEN` before `build/setup.py`. The worker uses:

| n8n side | Path |
|---|---|
| Session start (AR-01) | `POST <N8N_URL>/webhook/ar/v1/session/start` |
| Session end (AR-03) | `POST <N8N_URL>/webhook/ar/v1/session/end` |
| MCP tool server (AR-52): VerifyIdentity, FindDealers, FindSlots, ConfirmSlot, BookAppointment, RequestAgent, EndCall | `<N8N_URL>/mcp/ar-proof-tools` |

## Worker environment

Install the worker as its README describes, then set, next to the LiveKit
and speech keys:

```bash
export N8N_MODE=scripted                      # or realtime
export N8N_BASE_URL=$N8N_URL/webhook
export N8N_BEARER=$AR_GATEWAY_TOKEN
export N8N_MCP_URL=$N8N_URL/mcp/ar-proof-tools
export N8N_MCP_TOKEN=$AR_PROOF_MCP_TOKEN
export N8N_SCRIPT_FILE=<this repository>/workflows/voice-booking/worker/script.en.json
export REALTIME_INSTRUCTIONS_FILE=<this repository>/workflows/voice-booking/worker/realtime-instructions.txt
export N8N_AGENT_NAME=n8n-reference-worker    # the name the SIP dispatch rule calls
export N8N_END_CALL=worker N8N_REPORT_END=true
export TTS_VOICE=marin
export REALTIME_VAD_THRESHOLD=0.7 REALTIME_VAD_SILENCE_MS=500

# Test only: the identity sent when a call arrives without a User-to-User header.
export TEST_CALLER_VIN=TST2476011J112233 TEST_CALLER_MSISDN=+447700900123
```

`TST2476011J112233` is the synthetic GB customer in the mock CRM; its PIN is
`7391`.

## Check it without a call

Run from the worker repository. Each step calls the real n8n tool and prints
the line the caller would hear:

```bash
PYTHONPATH=. .venv/bin/python scripts/script_check.py \
  VerifyIdentity:pin=7391 FindSlots:fromDate= ChooseSlot:option=2 BookAppointment EndCall
```

`scripts/voice_check.py --script` renders the fixed lines into the voice
cache, so the first real call already plays cached audio.
`scripts/contract_check.py` runs start, turn and end against n8n for turn
mode.

Run both checks after every change to the script or to a tool workflow, and
before a demo. A green check plus one test call is the repeatable
pre-flight.

## Handover to a person

On `RequestAgent`, or after a second failed PIN, the worker speaks the
scripted line for it. What happens next depends on `N8N_PATH_HANDOFF`:

- Unset: the worker stops listening and leaves the transfer to n8n.
- Set: the worker posts `sessionId`, `correlationId`, `room`, `identity` and
  `summary` to that n8n webhook. The workflow behind it builds the return
  UUI with AR-14 and sends the SIP transfer through the LiveKit node's SIP
  Transfer Participant operation, with the UUI as a `User-to-User` header.

That handover workflow is not part of this repository: it needs the LiveKit
community node, and this set carries native nodes only. The contract is in
the worker README. Transfers need a SIP trunk whose provider accepts SIP
REFER; LiveKit refuses a transfer on a number rented from LiveKit.

## Several workers, one number

Each worker takes its own `N8N_AGENT_NAME` and `WORKER_HTTP_PORT=0` (any free
port), so two builds can run on one machine. `scripts/route.py <dispatch
rule> <agent name>` in the worker repository points a number's dispatch rule
at one of them and reads it back; the switch applies to the next call.

## Measured on our lab

Scripted mode: about one second from the end of the caller's speech to the
reply, with cached audio and n8n tools answering in 150 to 450 ms. A slow
downstream API adds directly to that. There is no spoken filler for a slow
tool in scripted mode yet.
