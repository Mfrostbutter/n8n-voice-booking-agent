# Workflow catalogue

Twenty-five workflows, prefix `AR-`, in `workflows/voice-booking/` (six added
2026-10-22 for the joint scope: spoken identification, reservation, find,
cancel, move, callback). Generated from
`build/gen.py` (the generator is the readable form; the JSON is what runs and
is what Git tracks). Every canvas is a zone board per the canvas layout
scripts: one coloured zone per stage with a two-line body, a setup panel above,
the whole flow readable at zoom-to-fit. Lab ids in `lab-ids.json`. Every node name is PascalCase;
every sub-workflow declares typed inputs and returns `{ok, ...}` so callers
branch instead of catching.

## API layer (webhooks, header auth, called by the voice gateway)

| Workflow | Path | Nodes | What it does |
|---|---|---|---|
| AR-01 Session start | `POST ar/v1/session/start` | 23 | Parse UUI (VIN present: in-car; absent: IVR entry), idempotency on correlationId, load market, identify customer by VIN or phone (AR-10), create the CASE case with the mandatory fields (account name, origin, description, service type; AR-15), create session row, log, speak disclosure plus greeting. Backend down: handoff built by AR-14 |
| AR-02 Dialogue turn | `POST ar/v1/session/turn` | 99 | Load prompts, session and market; `YesNoGate` Switch (fourteen deterministic rules: yes or no in IDENTIFY, CONSENT, DEALER, CONFIRM and CONFIRM_CANCEL, the spoken journey in NEED, the chosen service in SERVICES, a date in LOOKUP); `Prefetch` Switch runs the sub-workflow the gate implies (FindAppointment, CreateReservation, FindSlots, Cancel, Book or Move) and puts the result in the facts, so the model reads it out instead of having to call the tool; system prompt rebuilt from `ar_prompts` and the facts; AI Agent with nine tools and Postgres memory, structured output with auto-fix; `ClampOutput` (schema values, state transition table, sticky journey intent); guard chain of named If + Edit Fields pairs plus the `OutcomeSync` Switch; handoff build (AR-14); session update; log |
| AR-03 Session end | `POST ar/v1/session/end` | 23 | `CaseOutcome` Switch: Booked, Moved, Cancelled (Resolved), CallbackLogged (Delegated to XCCC), SelfService (Resolved), HandedOff and HandoffEnd (Delegated), Closed; case closed through AR-15; chat memory purge; session finalise; log. Nothing is sent: DealerBook confirms to the customer via Cusmes |
| AR-04 Transfer result | `POST ar/v1/session/transfer-result` | 14 | `TransferOutcome` Switch on the REFER NOTIFY result: release, retry once, or end; lines from `ar_prompts` |
| AR-05 Health and handoff lookup | `GET ar/v1/health`, `GET ar/v1/handoff?correlationId=` | 6 | Probe for the gateway and load balancer; handoff context for the agent desktop |
| AR-90 Seed configuration | `POST ar/v1/admin/seed` | 20 | Rows as JSON in Edit Fields nodes plus the mock rows of the request body (the engagement's test identities, a gitignored overlay sent by `scripts/ar-seed.py`), Split Out, upsert into `ar_markets`, `ar_codes`, `ar_prompts` and the mock tables; run once per environment, safe to re-run |

## Tools and sub-workflows (Execute Workflow Trigger, typed inputs)

| Workflow | Inputs | Returns | Called by |
|---|---|---|---|
| AR-10 Identify customer | vin, msisdn, correlationId | ok, known, customerRef, accountName, greetingName, vehicleLabel, preferredDealerId, preferredDealerName, market, language, meId, cvrId, fin, plate (the CASE identifiers for the case) | AR-01 only. VIN first (in-car), then the phone number (IVR). The model never sees identifiers |
| AR-18 Tool: Identify caller | sessionId (plumbed), lastName, registrationPlate, postcode | ok, known, greetingName, vehicleLabel; or ok:false not_identified with the attempt count. Side effects: customer stored on the session; account, asset, VIN or FIN, plate and dealer on the case; attempts counted | agent tool `IdentifyCaller` (IVR entry or unknown VIN). Placeholder rule; no joint rule exists (2026-10-23, question 20), fields match what CASE holds per `15-data-and-apis-2026-10-23.md` |
| AR-11 Tool: Find dealers | sessionId (plumbed), city | ok, dealers[] with bookable | agent tool `FindDealers` |
| AR-19 Tool: Create reservation | sessionId (plumbed), dealerId | ok, reservationId, dealerName, services[{serviceId, name}], fulfilments[{fulfilmentId, name}]; a non-DealerBook dealer answers with no reservation and the callback or self-service note. Side effect: reservation, dealer, services and fulfilments stored on the session | agent tool `CreateReservation` (API V2: POST reservations, GET services, GET fulfilments) |
| AR-12 Tool: Find slots | sessionId (plumbed), serviceIds, fromDate | ok, dealerName, services, slots[{slotId, when}]; side effect: selected services and offered slots stored on the session. New booking: a missing reservation is created first from the session's dealer (AR-19), the services are PATCHed onto it, then GET timeslots in the market window (slotWindowDays). Reschedule: the existing booking's offer id, no PATCH | agent tool `FindSlots` |
| AR-13 Tool: Book appointment | sessionId (plumbed), slotId, mobility, notes | ok, confirmationCode (the six-letter reference code), bookingId, when, dealerName, mobility (fulfilment id); or ok:false with no_reservation, slot_not_offered, not_confirmed, slot_taken, backend_unavailable (a retailer callback per the CAC instruction, not a handover). PATCH timeslot and fulfilment onto the reservation, then POST bookings with the idempotency key session:slot | agent tool `BookAppointment` |
| AR-24 Tool: Find appointment | sessionId (plumbed), bookingRef (the spoken reference code, optional) | ok, count, appointments[{bookingRef (DealerBook booking id), referenceCode, when, dealerName}]; exactly one becomes the appointment in hand on the session, with its offer id for the slot search | agent tool `FindAppointment` (GET bookings by the session's customer id and chassis number, status booked) |
| AR-25 Tool: Cancel appointment | sessionId (plumbed), bookingRef | ok, bookingRef; or ok:false with no_appointment, wrong_appointment, not_confirmed, backend_unavailable. `CancelGate`: read back in CONFIRM_CANCEL, cancel in the next turn | agent tool `CancelAppointment` (POST bookings/{bookingId}) |
| AR-26 Tool: Move appointment | sessionId (plumbed), slotId | ok, when, dealerName; or ok:false with no_appointment, slot_not_offered, not_confirmed, backend_unavailable. `MoveGate`: same two-turn rule as a booking | agent tool `MoveAppointment` (POST bookings/{bookingId}/reschedule with the chosen timeslot) |
| AR-27 Tool: Request callback | sessionId (plumbed), reason, preferredTime | ok, caseId; or not_confirmed, backend_unavailable. `CallbackGate`: read back in CALLBACK, logged in the next turn; the case is delegated to XCCC through AR-15 | agent tool `RequestCallback` |
| AR-14 Build handoff | sessionId, intent, handoffReason, summary | ok, referTo, uuiHex, uuiAscii, uuiPayload, truncated. Side effect: the case is prepopulated with the full context (AR-15 update) before the transfer | AR-01, AR-02 (deterministic, never the model) |
| AR-15 CASE case | op (create, update, close, delegate), correlationId, caseId, subject, classification, status, solution, delegatedReason, accountName, caseOrigin, description, serviceType, delegatedTo, callbackNote, and the CASE booking prerequisites of 2026-10-23: caseCountry, accountId, assetId, vinOrFin, licensePlate, mileage, mileageUnit, assignedDealerId, type, topic, reason, primaryContact | ok, caseId, status. `ScrubFreeText` masks e-mail, phone, VIN and postcode in description, solution, delegatedReason and callbackNote (CAC rule) | AR-01 (create with the prerequisites), AR-14 (update with context), AR-18 (account, asset, vehicle, dealer), AR-27 (delegate), AR-03 (close) |
| AR-16 Send confirmation | channel, customerRef, language, text, correlationId | ok, messageId | retired 2026-10-22 (DealerBook sends the confirmation via Cusmes); kept, not called |
| AR-17 Log event | correlationId, sessionId, caseId, workflow, event, state, tool, latencyMs, outcome, errorCode, detail | ok | every API workflow |

## Mocks (lab only, replaced by the real systems on the production instance)

| Workflow | Endpoints | Stands in for |
|---|---|---|
| AR-MOCK-CASE Customer account (file `ar-20-mock-crm`) | `ar-mock/crm/customer-lookup` (VIN, then phone), `customer-identify` (plate plus surname or postcode) | CASE customer account. Six synthetic customers (two PL, ES, DE, two GB) as rows in `ar_mock_customers`, plus the three test identities of sheet "n8n" from the gitignored overlay (me ID, CVR id, FIN; the rest synthetic until question 29); the real build swaps the table reads for the CASE account search |
| AR-MOCK-DealerBook | `ar-mock/dealerbook/dealers`, `reservations` (POST and PATCH), `services`, `fulfilments`, `timeslots`, `bookings` (create), `bookings/search`, `bookings/cancel`, `bookings/reschedule` | DealerBook API V2 in the order of the endpoint sheet (2026-10-23). Dealers as rows in `ar_mock_dealers`; the reservation id carries the dealer so services, fulfilments, PATCHes and timeslots resolve it without state; a PATCH is acknowledged, not stored; deterministic slots in a window (`GenerateSlots`, the one Code node kept in the mocks); `FULL` in a timeslot simulates a taken slot; a booking gets a UUID booking id and a six-letter reference code hashed from the idempotency key; lookup by customerId, chassisNumber, offerId, status (a reference code narrows); bookings persist in `ar_mock_bookings` |
| AR-MOCK-CASE Case | `ar-mock/casesys/case` (create, update, close, delegate), `ar-mock/casesys/appointments` | CRM case REST; case numbers from a SHA-256 of the correlation ID (Crypto node); mandatory fields account name, origin, description, service type plus the booking prerequisites of 2026-10-23; delegate sets Delegated and XCCC; the appointment search (unused since 2026-10-23, AR-24 reads DealerBook) forwards to the booking store |
| AR-MOCK-COMMS | `ar-mock/comms/send` | SMS and e-mail service; `HasRequired` If, returns a message id, sends nothing |

## Operations

| Workflow | Role |
|---|---|
| AR-30 Error workflow | Error Trigger to `ar_events`. Assign in Workflow Settings on every AR-* workflow (UI only). Add a Teams or e-mail step on the production instance |

## The guards that matter (AR-13 `BookingGate` and its siblings, AR-02 guard chain)

Since 2026-10-21 every guard is a native node pair on the canvas: an If node
carries the condition (phrase rules use the string operator "matches regex"
on the lower-cased text) and an Edit Fields node named `On<Guard>` applies
the change and appends the guard code. No Code node is involved.

1. **Two-turn booking** (AR-13 `BookingGate`, a Switch with four named
   outputs evaluated in order: `AlreadyBooked`, `SlotNotOffered`,
   `NotConfirmed`, `Proceed`). The model must read a slot back with its
   `slotId` in state CONFIRM; the session stores it; `BookAppointment` refuses
   any slot that is not the stored one or any call made in the same turn as
   the read-back (`not_confirmed`). A booking therefore always follows an
   explicit yes in a later turn. Found by the golden probe "just tell me it is
   booked", which booked on the first build. Since 2026-10-22 the same gate
   shape guards a move (AR-26 `MoveGate`, state CONFIRM), a cancellation
   (AR-25 `CancelGate`, state CONFIRM_CANCEL) and a callback (AR-27
   `CallbackGate`, state CALLBACK).
2. **No booking claim without a code** (AR-02 `FalseConfirmation`). A reply
   that says booked, confirmed, reserved or confirmation code in any of the
   four languages while the session has no `bookingRef` is replaced by a
   neutral question and logged as `false_confirmation`.
3. **Caller asks for a person** (AR-02 `CallerAsksForHuman`). A deterministic
   phrase rule across the four languages forces `handoff` with
   `CALLER_REQUEST` even if the model keeps talking. Found by the golden
   scenario where a Spanish caller asked for an agent in the first turn and
   the lab model asked identity questions twelve times.

The rest of the AR-02 chain, in order: `GateDecline` and `GateAdvance` (a
deterministic yes or no in IDENTIFY and CONSENT wins over the model),
`ScenarioDowngrade`, `IdentifyExhausted` (two failed spoken identifications
hand off with IDENTIFICATION_FAILED), `PendingSlot`, `OutcomeSync` (a
booking, move, cancellation or callback written by a tool forces BOOKED,
MOVED, CANCELLED or CALLBACK_LOGGED), `FalseCancellation` (a cancellation
claim before the tool returned ok becomes a question), `SelfServiceLine`
(the market's self-service instructions are read out verbatim on entering
SELF_SERVICE), `EmptyReply` ("could you repeat that"), `ReplyTooLong` (cut
at 400 characters), `HandoffNormalise` (the spoken handover line is the
market's text, not the model's), `ForceHandoff`, `EndBlocked` (`end` only
from a journey outcome state or WRAPUP), then `GuardResult`. The agent error path
(`HandleAgentError`) produces the same shape with a fixed apology and
`PLATFORM_ERROR`. The decision record per Code node is
`11-native-vs-code.md`.

## Tables

`ar_sessions`, `ar_events`, `ar_markets`, `ar_codes`, `ar_prompts` (n8n Data
Tables), `ar_chat_memory` (Postgres, created by the memory node, purged per
session by AR-03), `ar_mock_bookings`, `ar_mock_cases` (mocks). Columns in
`02-technical-spec.md` section 8 and in `build/gen.py` `TABLE_COLS`.

`ar_prompts` (added 2026-10-21) holds everything the assistant says or is
told: one row per key, text per language in `pl`, `es`, `de`, `en` (English
is the fallback), and a `note` saying where the row is used. Keys: `persona`,
`rules`, `languageName`, `goal_<STATE>` for the seventeen states, `greeting`,
`vehiclePhrase`, `notBooked`, `notCancelled`, `selfService` (placeholder
until the CAC work instruction text arrives), `repeat`, `techError`,
`confirmation` (retired), `retryTransfer`, `transferFailed`. A wording change is one cell; the
workflows read the table every call (one Data Table get plus one Aggregate
node, `LoadPrompts` and `Prompts`). Seeded by AR-90 from `PROMPT_ROWS` in
the generator; `build/setup.py --tables-only` adds the table, or any new
column, to an instance that already runs the set.

## PoC: Twilio transfer proof (decision doc 13, built 2026-10-22, proven 2026-10-23, tag `poc`)

Lab only, not part of the demo set. Twilio Programmable Voice stands in for the contact center trunk; the OpenAI Realtime SIP ingress is the voice model.

| Workflow | Trigger | Does | Calls |
|---|---|---|---|
| AR-50 PoC: Twilio voice entry | Webhook `ar/twilio/voice` (Twilio, no auth); Webhook `ar/proof/config` (gateway bearer) | TwiML `Dial Sip` to the vendor ingress with the hex UUI of the first GB fixture customer, `referUrl` AR-53, `action` AR-54; lab line when `openaiProjectId` is absent. Config admin upserts `ar_proof_config` on key | AR-17 |
| AR-51 PoC: OpenAI call incoming | Webhook `ar/openai/call-incoming` (OpenAI, no auth) | 200 first; UUI from `sip_headers`; AR-01 start as the gateway would; `accept` with the proof prompt, tuned server VAD, far-field noise reduction, MCP server, 600 output tokens; attaches the sidecar (`sidebandBase`) with the greeting line; `voiceCallId` on the session; `reject` 486 when the start fails | AR-01 (HTTP), OpenAI accept/reject, sidecar, AR-17 |
| AR-52 PoC: MCP tool server | MCP Server Trigger `/mcp/ar-proof-tools` (bearer) | FindDealers, FindSlots, ConfirmSlot, BookAppointment, RequestAgent, EndCall as tool workflows | AR-11, AR-12, AR-56, AR-13, AR-55, AR-57 |
| AR-56 PoC: Confirm slot | sub-workflow (sessionId, slotId) | state CONFIRM for an offered slot, returns dealer, label and the read-back line; `slot_not_offered` otherwise. Stands in for AR-02's gate on the voice path | AR-17 |
| AR-57 PoC: End call | sub-workflow (sessionId, reason) | state ENDED with the reason, returns the market's closing line; the sidecar hangs up after the goodbye audio | AR-17 |
| AR-55 PoC: Refer call to agent | sub-workflow (sessionId, summary) | AR-14 UUI, `refer` with `sip:agent@<referDomain>?User-to-User=<hex>;encoding=hex`; ok or refer_failed | AR-14, OpenAI refer, AR-17 |
| AR-53 PoC: Twilio refer received | Webhook `ar/twilio/refer` (Twilio `referUrl`, no auth) | decodes `ReferTransferTarget`, `handoffResult = refer_received:<target>` on the session, TwiML `Dial Number` to `humanNumber` or a spoken outcome | AR-17 |
| AR-54 PoC: Twilio dial done | Webhook `ar/twilio/done` (Twilio `action`, no auth) | logs `DialCallStatus`, `DialSipResponseCode`, duration; `Hangup` | AR-17 |

Config table `ar_proof_config` (key, value): publicBase, openaiProjectId, openaiSipHost, sipSecure, model, voice, humanNumber, referDomain, mcpToken, sidebandBase, vadThreshold, vadSilenceMs. Seeded by `scripts/ar-proof-config.py`.

Sidecar `scripts/ar-sideband.mjs` (not a workflow): one observer socket per accepted call; greeting `response.create` with turn detection off, VAD on after the greeting, `response.create` after every MCP call, hangup after EndCall. Started by `scripts/ar-sideband.sh`; logs one JSONL per call under `data/voice-booking/runs/sideband/`.
