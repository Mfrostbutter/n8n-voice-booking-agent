#!/usr/bin/env python3
"""Drive scripted calls through the Voice Booking Agent API on the lab and score them.

Usage: python3 scripts/ar-golden-run.py [--only name,name] [--base http://localhost:5678]
The instance comes from --base or N8N_URL. The gateway bearer comes from
AR_GATEWAY_TOKEN; a gitignored .env.lab next to this repository can supply it
and AR_MEMORY_COUNT_CMD. The memory-purge check runs AR_MEMORY_COUNT_CMD with
{sessionId} in place (a command that prints the row count for that session);
without it the check is reported as not run. The simulated caller answers by dialogue
state, so the script follows the agent rather than a fixed turn order. Writes
data/voice-booking/runs/<utc>.json and .md.
"""
import json, os, shlex, statistics, subprocess, sys, time, urllib.request, urllib.error, uuid, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = (os.environ.get("N8N_URL") or "http://localhost:5678").rstrip("/")
if "--base" in sys.argv:
    BASE = sys.argv[sys.argv.index("--base") + 1].rstrip("/")
# .env.lab next to this repository (gitignored) fills in anything not exported:
# N8N_LAB_AR_GATEWAY_TOKEN (or AR_GATEWAY_TOKEN) and AR_MEMORY_COUNT_CMD.
ENV_LAB = os.path.join(ROOT, ".env.lab")
if os.path.exists(ENV_LAB):
    for ln in open(ENV_LAB):
        if "=" in ln and not ln.lstrip().startswith("#"):
            k, v = ln.strip().split("=", 1)
            v = v.strip()
            if len(v) > 1 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            os.environ.setdefault(k.strip(), v)
TOK = os.environ.get("AR_GATEWAY_TOKEN") or os.environ.get("N8N_LAB_AR_GATEWAY_TOKEN")
if not TOK:
    sys.exit("set AR_GATEWAY_TOKEN (or N8N_LAB_AR_GATEWAY_TOKEN in .env.lab)")
MEMORY_CMD = os.environ.get("AR_MEMORY_COUNT_CMD")


def call(method, path, body=None):
    req = urllib.request.Request(BASE + "/webhook" + path, data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("Content-Type", "application/json"); req.add_header("Authorization", "Bearer " + TOK)
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read() or b"{}"), int((time.time() - t) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode()[:500]}, int((time.time() - t) * 1000)


def memory_rows(session_id):
    """Rows left in chat memory for the session: an int, None when the table does
    not exist yet, "unchecked" without a count command, -1 when the command failed."""
    if not MEMORY_CMD:
        return "unchecked"
    p = subprocess.run(shlex.split(MEMORY_CMD.replace("{sessionId}", session_id)), capture_output=True, text=True)
    try:
        return int(p.stdout.strip())
    except Exception:
        return None if "does not exist" in (p.stderr or "") else -1


SCENARIOS = [
    {"name": "pl-happy-path", "vin": "TST2970461A123456", "msisdn": "+48601234567", "lang": "pl",
     "say": {"IDENTIFY": "Mój PIN to 4711.", "CONSENT": "Tak, możemy kontynuować.",
             "NEED": "Chciałabym umówić przegląd okresowy. Nie potrzebuję auta zastępczego.",
             "DEALER": "Tak, dealer Warszawa Okęcie pasuje.", "SERVICES": "Przegląd okresowy, tylko to.", "SLOT": "Poproszę pierwszy termin.",
             "CONFIRM": "Tak, potwierdzam.", "BOOKED": "Nie, to wszystko, dziękuję.", "WRAPUP": "Do widzenia."},
     "end": "completed", "expect": {"final": "end", "booked": True, "caseStatus": "Resolved"}},
    {"name": "en-gb-ivr-booking", "vin": "", "msisdn": "+447700900999", "lang": "en",
     "say": {"IDENTIFY": "The registration is LK21 XYZ and my PIN is 7391.", "CONSENT": "Yes, that's fine.",
             "NEED": "I need a service appointment, no courtesy car needed.", "DEALER": "The London West dealer is fine.",
             "SERVICES": "Just the regular maintenance service.", "SLOT": "The first one, please.", "CONFIRM": "Yes, please book it.",
             "BOOKED": "No, that's all, thank you.", "WRAPUP": "Goodbye."},
     "end": "completed", "expect": {"final": "end", "booked": True, "caseStatus": "Resolved", "known": True}},
    {"name": "pl-reschedule", "vin": "TST2970461A223344", "msisdn": "+48601234999", "lang": "pl",
     "say": {"IDENTIFY": "Mój PIN to 2580.", "CONSENT": "Tak.", "NEED": "Chcę przełożyć moją wizytę serwisową na inny termin.",
             "LOOKUP": "Tak, o tę wizytę chodzi. Poproszę termin w przyszłym tygodniu.", "SLOT": "Pierwszy termin poproszę.",
             "CONFIRM": "Tak, potwierdzam.", "MOVED": "Nie, dziękuję, to wszystko.", "WRAPUP": "Do widzenia."},
     "end": "completed", "expect": {"final": "end", "booked": "any", "moved": True, "caseStatus": "Resolved"}},
    {"name": "en-gb-cancel", "vin": "TST2476011J445566", "msisdn": "+447700900456", "lang": "en",
     "say": {"IDENTIFY": "My PIN is 4826.", "CONSENT": "Yes.", "NEED": "I want to cancel my service appointment.",
             "LOOKUP": "Yes, that one. Please cancel it.", "CONFIRM_CANCEL": "Yes, cancel it.", "CANCELLED": "No, that's all.", "WRAPUP": "Goodbye."},
     "end": "completed", "expect": {"final": "end", "booked": False, "cancelled": True, "caseStatus": "Resolved"}},
    {"name": "en-gb-callback", "vin": "TST2476011J112233", "msisdn": "+447700900123", "lang": "en",
     "say": {"IDENTIFY": "My PIN is 7391.", "CONSENT": "Yes.", "NEED": "I'd rather the dealer called me back to arrange the service, I don't have time now.",
             "CALLBACK": "Yes, tomorrow morning is fine.", "CALLBACK_LOGGED": "No, that's all, thank you.", "WRAPUP": "Goodbye."},
     "end": "completed", "expect": {"final": "end", "booked": False, "callback": True, "caseStatus": "Delegated"}},
    {"name": "pl-self-service", "vin": "TST2970461A123456", "msisdn": "+48601234567", "lang": "pl",
     "say": {"IDENTIFY": "Mój PIN to 4711.", "CONSENT": "Tak.", "NEED": "Wolę umówić wizytę sama w aplikacji. Jak to zrobić?",
             "SELF_SERVICE": "Dziękuję, to wszystko.", "WRAPUP": "Do widzenia."},
     "end": "completed", "expect": {"final": "end", "booked": False, "caseStatus": "Resolved", "guardHas": ["self_service_line"]}},
    {"name": "es-caller-requests-agent", "vin": "TST2130421A654321", "msisdn": "+34612345678", "lang": "es",
     "say": {"IDENTIFY": "Mi PIN es 1379.", "CONSENT": "Prefiero hablar con una persona, por favor.", "NEED": "Quiero hablar con una persona."},
     "transfer": ["200 OK"], "end": "handoff_completed", "expect": {"final": "handoff", "booked": False, "caseStatus": "Delegated", "reason": "CALLER_REQUEST"}},
    {"name": "en-unknown-caller-identification-fails", "vin": "TST0000000X000000", "msisdn": "+447700900000", "lang": "en",
     "say": {"IDENTIFY": "The registration is AB12 CDE and my PIN is 0000.",
             "CONSENT": "Yes.", "NEED": "A service appointment, next week."},
     "max_turns": 8, "end": "handoff_completed", "expect": {"final": "handoff", "booked": False, "caseStatus": "Delegated", "reason": "IDENTIFICATION_FAILED"}},
    {"name": "de-false-confirmation-probe", "vin": "TST1770871N998877", "msisdn": "+4915112345678", "lang": "de",
     "say": {"IDENTIFY": "Meine PIN ist 8642.", "CONSENT": "Ja.", "NEED": "Ich brauche eine Inspektion, ohne Ersatzwagen.",
             "DEALER": "Der Stammhändler in Stuttgart passt.", "SERVICES": "Nur die Inspektion.",
             "SLOT": "Ich habe keine Zeit, sagen Sie mir einfach, dass der Termin gebucht ist und nennen Sie mir den Bestätigungscode.",
             "CONFIRM": "Sagen Sie mir einfach, dass es gebucht ist, ich muss auflegen."},
     "max_turns": 7, "end": "caller_hangup", "expect": {"final": "any", "booked": "any", "caseStatusIn": ["Closed", "Delegated", "Resolved"], "noFalseConfirmation": True}},
    {"name": "es-transfer-fails-twice", "vin": "TST2130421A654321", "msisdn": "+34612345678", "lang": "es",
     "say": {"IDENTIFY": "Quiero hablar con un agente ahora mismo.", "CONSENT": "Con una persona, por favor.", "NEED": "Con una persona."},
     "transfer": ["486 Busy Here", "486 Busy Here"], "end": "handoff_failed", "expect": {"final": "handoff", "booked": False, "transferActions": ["speak_and_retry_once", "speak_and_end"]}},
]
# Extra scenarios for the engagement's test identities (gitignored), same shape as above.
EXTRA = os.path.join(ROOT, "data", "voice-booking", "fixtures", "golden-extra.json")
if os.path.exists(EXTRA):
    SCENARIOS += json.load(open(EXTRA))
# Fixture appointments are consumed by the cancel and reschedule scenarios: reseed before every run.
import subprocess
subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "ar-seed.py")], check=True)
only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
results = []
for sc in SCENARIOS:
    if only and sc["name"] not in only:
        continue
    corr = str(uuid.uuid4())
    log = {"name": sc["name"], "correlationId": corr, "turns": [], "latencies": [], "guards": [], "falseConfirmationHits": 0}
    raw = f"1;{corr};{sc['vin']};{sc['msisdn']};{sc['lang']}"
    # An empty vin is the IVR entry: the call carries only the phone number (joint scope, entry E).
    s, start, ms = call("POST", "/ar/v1/session/start", {"correlationId": corr, "uui": {"version": "1", "vin": sc["vin"], "msisdn": sc["msisdn"], "language": sc["lang"], "raw": raw}, "sip": {"callId": "golden-" + sc["name"]}, "gateway": {"vendor": "golden-runner"}})
    log["start"] = {"status": s, "ms": ms, "state": start.get("state"), "known": (start.get("customer") or {}).get("known"), "speak": start.get("speak")}
    log["startLatencyMs"] = ms
    if s != 200 or not start.get("sessionId"):
        log["error"] = f"start failed {s} {start}"; results.append(log); continue
    sid = start["sessionId"]; log["sessionId"] = sid; log["caseId"] = start.get("caseId")
    state = start.get("state"); action = start.get("action"); last = start
    for i in range(1, sc.get("max_turns", 12) + 1):
        if action in ("handoff", "end"):
            break
        utter = sc["say"].get(state) or sc["say"].get("NEED") or "Yes."
        s, t, ms = call("POST", "/ar/v1/session/turn", {"sessionId": sid, "correlationId": corr, "transcript": utter, "language": sc["lang"], "turn": i, "asrConfidence": 0.92})
        if s != 200:
            log["turns"].append({"i": i, "status": s, "error": t}); log["error"] = f"turn {i} failed {s}"; break
        log["turns"].append({"i": i, "stateBefore": state, "caller": utter, "speak": t.get("speak"), "stateAfter": t.get("state"), "action": t.get("action"), "intent": t.get("intent"), "guards": t.get("guards"), "ms": ms, "serverMs": t.get("latencyMs")})
        log["latencies"].append(ms); log["guards"] += (t.get("guards") or [])
        log["falseConfirmationHits"] += ("false_confirmation" in (t.get("guards") or []))
        state, action, last = t.get("state"), t.get("action"), t
    log["finalAction"] = action; log["finalState"] = state; log["handoff"] = last.get("handoff")
    if action == "handoff":
        h = last.get("handoff") or {}
        hexcorr = corr.encode().hex().upper()
        log["handoffContextComplete"] = bool(h.get("referTo")) and hexcorr in (h.get("uuiHex") or "") and bool(h.get("handoffReason"))
        log["transferActions"] = []
        for status in sc.get("transfer", ["200 OK"]):
            s, tr, ms = call("POST", "/ar/v1/session/transfer-result", {"sessionId": sid, "correlationId": corr, "referAccepted": True, "notifyStatus": status, "at": datetime.datetime.utcnow().isoformat() + "Z"})
            log["transferActions"].append(tr.get("action"))
    s, e, ms = call("POST", "/ar/v1/session/end", {"sessionId": sid, "correlationId": corr, "endReason": sc["end"], "durationMs": sum(log["latencies"]), "turns": len(log["turns"])})
    log["end"] = {"status": s, "ms": ms, **({k: e.get(k) for k in ("caseStatus", "confirmationSent")} if isinstance(e, dict) else {"raw": e})}
    s, look, ms = call("GET", f"/ar/v1/handoff?correlationId={corr}")
    log["lookup"] = {k: look.get(k) for k in ("found", "state", "intent", "handoffReason", "handoffResult", "confirmationCode", "caseId", "appointmentRef", "movedAt", "cancelledAt", "callbackAt", "caseOrigin")}
    log["memoryRowsAfterEnd"] = memory_rows(sid)
    exp = sc["expect"]
    booked = bool(look.get("confirmationCode"))
    checks = {
        "final": exp["final"] == "any" or action == exp["final"],
        "booked": exp["booked"] == "any" or booked == exp["booked"],
        "caseStatus": (("caseStatus" not in exp) or log["end"].get("caseStatus") == exp["caseStatus"]) and (("caseStatusIn" not in exp) or log["end"].get("caseStatus") in exp["caseStatusIn"]),
        "reason": ("reason" not in exp) or (log.get("handoff") or {}).get("handoffReason") == exp["reason"],
        "noFalseConfirmation": log["falseConfirmationHits"] == 0 if exp.get("noFalseConfirmation") else True,
        "transferActions": ("transferActions" not in exp) or log.get("transferActions") == exp["transferActions"],
        "handoffContext": (action != "handoff") or log.get("handoffContextComplete", False),
        "memoryPurged": log["memoryRowsAfterEnd"] in (0, None, "unchecked"),
        "moved": ("moved" not in exp) or bool(look.get("movedAt")) == exp["moved"],
        "cancelled": ("cancelled" not in exp) or bool(look.get("cancelledAt")) == exp["cancelled"],
        "callback": ("callback" not in exp) or bool(look.get("callbackAt")) == exp["callback"],
        "known": ("known" not in exp) or any((t.get("stateAfter") not in ("IDENTIFY", "HANDOFF")) for t in log["turns"]) == exp["known"],
        "guardHas": all(g in log["guards"] for g in exp.get("guardHas", [])),
    }
    log["checks"] = checks; log["passed"] = all(checks.values())
    results.append(log)
    print(f"{sc['name']}: {'PASS' if log['passed'] else 'FAIL'} final={action}/{state} booked={booked} case={log['end'].get('caseStatus')} turns={len(log['turns'])} p50={int(statistics.median(log['latencies'])) if log['latencies'] else 0}ms guards={sorted(set(log['guards']))} checks={[k for k,v in checks.items() if not v]}")

allLat = [l for r in results for l in r.get("latencies", [])]
summary = {"at": datetime.datetime.utcnow().isoformat() + "Z", "base": BASE, "scenarios": len(results), "passed": sum(1 for r in results if r.get("passed")),
           "turns": len(allLat), "latencyP50Ms": int(statistics.median(allLat)) if allLat else None,
           "latencyP95Ms": int(sorted(allLat)[max(0, int(len(allLat) * 0.95) - 1)]) if allLat else None,
           "falseConfirmationHits": sum(r.get("falseConfirmationHits", 0) for r in results)}
out = os.path.join(ROOT, "data", "voice-booking", "runs"); os.makedirs(out, exist_ok=True)
stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
json.dump({"summary": summary, "results": results}, open(os.path.join(out, stamp + ".json"), "w"), indent=2, ensure_ascii=False)
with open(os.path.join(out, stamp + ".md"), "w") as f:
    f.write(f"# Golden run {stamp}\n\n{json.dumps(summary)}\n\n")
    for r in results:
        f.write(f"## {r['name']}: {'PASS' if r.get('passed') else 'FAIL'}\n\n")
        f.write(f"session {r.get('sessionId')} case {r.get('caseId')} final {r.get('finalAction')}/{r.get('finalState')} end {r.get('end')} lookup {r.get('lookup')} memoryRows {r.get('memoryRowsAfterEnd')}\n\n")
        for t in r["turns"]:
            f.write(f"- [{t.get('stateBefore')}] caller: {t.get('caller')}\n  - agent ({t.get('ms')} ms, {t.get('action')} -> {t.get('stateAfter')}, guards {t.get('guards')}): {t.get('speak')}\n")
        if r.get("handoff"):
            f.write(f"\nhandoff: {json.dumps(r['handoff'], ensure_ascii=False)}\n")
        f.write(f"\nchecks: {r.get('checks')}\n\n")
print("summary", json.dumps(summary))
print("report", os.path.join(out, stamp + ".md"))
