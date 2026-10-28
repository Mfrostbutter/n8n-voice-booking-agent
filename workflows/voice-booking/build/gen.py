#!/usr/bin/env python3
"""Generate the voice booking workflow set (AR-*) as n8n workflow JSON.

Run from anywhere: python3 workflows/voice-booking/build/gen.py
Reads the ids file (lab-ids.json, or AR_IDS_FILE): credential, Data Table and
workflow ids plus the per-instance settings build/setup.py writes. Workflow ids
are learned from previously deployed JSON files, so a second run after a deploy
resolves every sub-workflow reference.
"""
import json, os, sys, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(OUT))
IDS_PATH = os.environ.get("AR_IDS_FILE") or os.path.join(OUT, "lab-ids.json")
# The ids file is the per-instance configuration: credential and Data Table ids,
# names, placement, model and the instance's own URL. build/setup.py writes it.
IDS = json.load(open(IDS_PATH)) if os.path.exists(IDS_PATH) else {}
LAB = (IDS.get("selfUrl") or os.environ.get("N8N_URL") or "http://localhost:5678").rstrip("/")
MOCK = LAB + "/webhook/ar-mock"
CRED_PG = {"id": IDS.get("credPostgres", "PENDING"), "name": IDS.get("credPostgresName", "Chat memory Postgres")}
CRED_GW = {"id": IDS.get("credGatewayHeader", "PENDING"), "name": IDS.get("credGatewayHeaderName", "Gateway bearer")}
CRED_LM = {"id": IDS.get("credLmStudio", "PENDING"), "name": IDS.get("credLmStudioName", "Model endpoint")}
# PoC credentials (decision doc 13 proof). Attached only once the id is known, so an unconfigured node shows an empty selector.
CRED_OPENAI = {"id": IDS["credOpenAi"], "name": IDS.get("credOpenAiName", "AR OpenAI Realtime")} if IDS.get("credOpenAi") else None
CRED_MCP = {"id": IDS["credMcpBearer"], "name": IDS.get("credMcpBearerName", "AR MCP bearer")} if IDS.get("credMcpBearer") else None
MODEL = IDS.get("model", "qwen/qwen3-coder-30b")
# Empty: the model node uses the URL stored on the credential.
MODEL_BASE_URL = IDS.get("modelBaseUrl", "http://host.docker.internal:1234/v1")
NAMES = {}  # key -> workflow name, filled as workflows are declared

# Placement on the instance: one project per customer, one folder per demo,
# a customer tag, a demo tag and a role tag. deploy.py applies it; the deploy
# gate refuses a workflow without it.
PROJECT = IDS.get("project", "Voice Booking")
FOLDER = IDS.get("folder", "Voice Booking Agent")
TAGS = ["voice-booking"]
ROLE_TAG = {"ar-01": "api", "ar-02": "api", "ar-03": "api", "ar-04": "api", "ar-05": "api",
            "ar-10": "sub-workflow", "ar-11": "sub-workflow", "ar-12": "sub-workflow", "ar-13": "sub-workflow",
            "ar-14": "sub-workflow", "ar-15": "sub-workflow", "ar-16": "sub-workflow", "ar-17": "sub-workflow",
            "ar-18": "sub-workflow", "ar-19": "sub-workflow", "ar-24": "sub-workflow", "ar-25": "sub-workflow", "ar-26": "sub-workflow", "ar-27": "sub-workflow",
            "ar-20": "lab-stand-in", "ar-21": "lab-stand-in", "ar-22": "lab-stand-in", "ar-23": "lab-stand-in",
            "ar-30": "ops", "ar-90": "ops",
            "ar-50": "poc", "ar-51": "poc", "ar-52": "poc", "ar-53": "poc", "ar-54": "poc", "ar-55": "poc", "ar-56": "poc", "ar-57": "poc"}

TABLE_COLS = {
    "ar_sessions": ["sessionId","correlationId","caseId","market","language","state","intent","customerRef","vehicleRef","greetingName","vehicleLabel","dealerId","dealerName","offeredSlots","slotId","bookingRef","confirmationCode","confirmationChannel","confirmationSent","handoffReason","handoffResult","handoffSummary","turns","startedAt","endedAt","endReason","lastLatencyMs",
                    "caseOrigin","identifyAttempts","reservationId","services","selectedServices","appointmentRef","appointmentLabel","appointmentDealerId","appointmentDealerName","cancelledAt","movedAt","callbackAt","voiceCallId","appointmentOfferId","fulfilments"],
    "ar_events": ["at","correlationId","sessionId","caseId","workflow","event","state","tool","latencyMs","outcome","errorCode","detail"],
    "ar_markets": ["market","languages","defaultLanguage","bookable","disclosure_pl","disclosure_es","disclosure_en","disclosure_de","handoverLine","closingLine","confirmationChannels","returnNumber","slotWindowDays","maxSlotsOffered"],
    "ar_codes": ["kind","code","short","ccValue","description"],
    "ar_prompts": ["key","pl","es","de","en","note"],
    "ar_mock_bookings": ["bookingRef","confirmationCode","slotId","dealerId","dealerName","vin","serviceType","mobility","startsAt","status","bookedAt","reservationId","serviceIds","movedFrom","customerRef","fulfilmentId"],
    "ar_mock_cases": ["caseId","correlationId","status","subject","classification","solution","delegatedReason","openedAt","lastUpdate","accountName","caseOrigin","description","serviceType","delegatedTo","callbackNote",
                      "caseCountry","accountId","assetId","vinOrFin","licensePlate","mileage","mileageUnit","assignedDealerId","type","topic","reason","primaryContact"],
    "ar_mock_customers": ["vin","customerRef","accountName","greetingName","vehicleLabel","preferredDealerId","preferredDealerName","market","language","msisdn","plate","postcode","meId","cvrId","fin"],
    "ar_mock_dealers": ["dealerId","name","city","market","bookable"],
    "ar_proof_config": ["key","value"],
}
NUMERIC = {"turns","lastLatencyMs","latencyMs","slotWindowDays","maxSlotsOffered","identifyAttempts"}
BOOLEAN = {"confirmationSent","bookable"}


def coltype(c):
    return "number" if c in NUMERIC else "boolean" if c in BOOLEAN else "string"


def schema(cols, table=None):
    # Sub-workflow inputs and Data Table columns share one typing rule so a number declared on the trigger is sent as a number.
    return [{"id": c, "displayName": c, "required": False, "defaultMatch": False, "display": True,
             "type": coltype(c), "canBeUsedToMatch": True, "removed": False} for c in cols]


def learn_existing_ids():
    for f in os.listdir(OUT):
        if f.endswith(".json") and f.startswith("ar-"):
            try:
                w = json.load(open(os.path.join(OUT, f)))
            except Exception:
                continue
            key = f[:-5]
            if w.get("id") and not str(w["id"]).startswith("PENDING"):
                IDS["wf_" + key] = w["id"]


def wfref(key):
    return {"__rl": True, "value": IDS.get("wf_" + key, "PENDING_" + key), "mode": "list", "cachedResultName": NAMES.get(key, key)}


def snap16(v):
    return int(round(v / 16.0)) * 16


class WF:
    def __init__(self, key, name, description):
        self.key, self.name, self.description = key, name, description
        self.nodes, self.conns = [], {}
        self.panels = []
        NAMES[key] = name

    def add(self, name, ntype, ver, params, x, y, **extra):
        node = {"parameters": params, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"ar/{self.key}/{name}")),
                "name": name, "type": ntype, "typeVersion": ver, "position": [x, y]}
        node.update(extra)
        self.nodes.append(node)
        return name

    def link(self, src, dst, out=0, ctype="main", dst_in=0):
        lst = self.conns.setdefault(src, {}).setdefault(ctype, [])
        while len(lst) <= out:
            lst.append([])
        lst[out].append({"node": dst, "type": ctype, "index": dst_in})

    def chain(self, *names):
        for a, b in zip(names, names[1:]):
            self.link(a, b)

    # ---- node constructors -------------------------------------------------
    def sticky(self, content, x, y, w=560, h=120, color=7):
        """Overview text for the workflow. Rendered as the doc panel above the zone row by the canvas layout."""
        title, _, body = content.partition("\n")
        self.panels.append({"title": title.lstrip("# ").strip(), "body": body.strip(), "color": 6, "width": max(560, snap16(w))})
        return None

    def webhook(self, name, path, x, y, method="POST", auth=True):
        p = {"httpMethod": method, "path": path, "responseMode": "responseNode", "options": {}}
        extra = {"webhookId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"ar/{self.key}/{path}/{method}"))}
        if auth:
            p["authentication"] = "headerAuth"
            extra["credentials"] = {"httpHeaderAuth": CRED_GW}
        return self.add(name, "n8n-nodes-base.webhook", 2.1, p, x, y, **extra)

    def respond(self, name, body, x, y, code=200):
        return self.add(name, "n8n-nodes-base.respondToWebhook", 1.5,
                        {"respondWith": "json", "responseBody": body, "options": {"responseCode": code}}, x, y)

    def code(self, name, js, x, y, **extra):
        return self.add(name, "n8n-nodes-base.code", 2, {"jsCode": js}, x, y, **extra)

    def set_fields(self, name, fields, x, y, keep=False):
        """Edit Fields, manual mapping. fields: (name, value, type). keep=True passes the other input fields through."""
        assigns = [{"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"ar/{self.key}/{name}/{f}")), "name": f, "value": v, "type": t}
                   for f, v, t in fields]
        p = {"assignments": {"assignments": assigns}, "options": {}}
        if keep:
            p["includeOtherFields"] = True
        return self.add(name, "n8n-nodes-base.set", 3.4, p, x, y)

    def set_json(self, name, expr, x, y, **extra):
        """Edit Fields in JSON mode: the expression's object becomes the whole item. Runs once per input item unless executeOnce=True."""
        return self.add(name, "n8n-nodes-base.set", 3.4, {"mode": "raw", "jsonOutput": expr, "options": {}}, x, y, **extra)

    # ---- conditions ----------------------------------------------------------
    SINGLE_OPS = {"true", "false", "empty", "notEmpty", "exists", "notExists"}

    @classmethod
    def cond(cls, left, op, right="", kind="string"):
        """One filter condition. kind string|boolean|number; op per n8n's filter operators (equals, notEquals,
        regex, notRegex, empty, notEmpty, true, false, gt, lt, ...). For regex: lower-case the left side and pass a bare pattern."""
        operator = {"type": kind, "operation": op}
        if op in cls.SINGLE_OPS:
            operator["singleValue"] = True
        return {"leftValue": left, "rightValue": right, "operator": operator}

    def _rule(self, name, conds, combinator="and", version=2):
        out = []
        for i, c in enumerate(conds):
            out.append({"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"ar/{self.key}/{name}/c{i}")), **c})
        return {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": version},
                "conditions": out, "combinator": combinator}

    def if_node(self, name, left, x, y, kind="boolean"):
        """Single-condition If: boolean true, or string not empty."""
        c = self.cond(left, "true", kind="boolean") if kind == "boolean" else self.cond(left, "notEmpty")
        return self.if_rules(name, [c], x, y)

    def if_rules(self, name, conds, x, y, combinator="and"):
        """If with several conditions (see cond). Output 0 true, 1 false."""
        return self.add(name, "n8n-nodes-base.if", 2.2, {"conditions": self._rule(name, conds, combinator), "options": {}}, x, y)

    def switch(self, name, rules, x, y, fallback=None):
        """Switch in rules mode. rules: list of (outputName, conds, combinator); first match wins, in order.
        fallback names the extra output for items no rule matched (index len(rules))."""
        vals = [{"conditions": self._rule(f"{name}/{o}", c, comb, version=3), "renameOutput": True, "outputKey": o} for o, c, comb in rules]
        opts = {"fallbackOutput": "extra", "renameFallbackOutput": fallback} if fallback else {}
        return self.add(name, "n8n-nodes-base.switch", 3.4, {"rules": {"values": vals}, "looseTypeValidation": True, "options": opts}, x, y)

    def guard_chain(self, srcs, guards):
        """Named rule chain: for each (ifName, conds, combinator, setName, fields) an If and an Edit Fields that applies the rule.
        Both outcomes continue to the next rule. srcs: [(node, output)] feeding the first If. Returns the sources for the next node."""
        for ifn, conds, comb, setn, fields in guards:
            i = self.if_rules(ifn, conds, 0, 0, comb)
            for s, out in srcs:
                self.link(s, i, out)
            st = self.set_fields(setn, fields, 0, 0, keep=True)
            self.link(i, st, 0)
            srcs = [(st, 0), (i, 1)]
        return srcs

    def return_error(self, name, x, y):
        """Shapes a failed HTTP call into the sub-workflow's error contract. No throw: the caller branches on ok."""
        conflict = "JSON.stringify($json.error || {}).includes('409')"
        return self.set_fields(name, [
            ("ok", False, "boolean"),
            ("error", f"={{{{ {conflict} ? 'slot_taken' : 'backend_unavailable' }}}}", "string"),
            ("message", f"={{{{ {conflict} ? 'That slot was taken in the meantime. Call FindSlots again and offer new options.' : 'The backend system is unavailable. Apologise once and hand the caller to an agent (BACKEND_UNAVAILABLE).' }}}}", "string"),
            ("detail", "={{ String(($json.error || {}).message || '').slice(0, 200) }}", "string"),
        ], x, y)

    def dt(self, name, table, op, x, y, conditions=None, values=None, limit=1, always=True, all=False):
        p = {"operation": op, "dataTableId": {"__rl": True, "mode": "id", "value": IDS["dt_" + table]}}
        if conditions is not None:
            p["matchType"] = "allConditions"
            p["filters"] = {"conditions": [{"keyName": k, "condition": "eq", "keyValue": v} for k, v in conditions]}
        if op == "get":
            p["returnAll"] = all
            if not all:
                p["limit"] = limit
        if values is not None:
            p["columns"] = {"mappingMode": "defineBelow", "value": values, "matchingColumns": [],
                            "schema": schema(TABLE_COLS[table], table), "attemptToConvertTypes": False, "convertFieldsToString": False}
        extra = {"alwaysOutputData": True} if always and op in ("get", "update", "upsert") else {}
        return self.add(name, "n8n-nodes-base.dataTable", 1.1, p, x, y, **extra)

    def aggregate(self, name, field, x, y):
        """All input items into one item holding a list under `field`."""
        return self.add(name, "n8n-nodes-base.aggregate", 1, {"aggregate": "aggregateAllItemData", "destinationFieldName": field, "options": {}}, x, y)

    def split_out(self, name, field, x, y):
        return self.add(name, "n8n-nodes-base.splitOut", 1, {"fieldToSplitOut": field, "options": {}}, x, y)

    def load_prompts(self, x, y):
        """ar_prompts rows aggregated into one item: $('Prompts').first().json.prompts. Place before the session is loaded."""
        g = self.dt("LoadPrompts", "ar_prompts", "get", x, y, all=True, always=False)
        a = self.aggregate("Prompts", "prompts", x + 208, y)
        self.link(g, a)
        return g, a

    def sub_trigger(self, fields, x=0, y=0, name="ToolInput"):
        return self.add(name, "n8n-nodes-base.executeWorkflowTrigger", 1.2,
                        {"inputSource": "workflowInputs", "workflowInputs": {"values": [{"name": n, "type": t} for n, t in fields]}}, x, y)

    def exec_wf(self, name, key, inputs, x, y, wait=True):
        p = {"workflowId": wfref(key),
             "workflowInputs": {"mappingMode": "defineBelow", "value": inputs, "matchingColumns": [],
                                "schema": schema(list(inputs.keys())), "attemptToConvertTypes": False, "convertFieldsToString": True},
             "mode": "once", "options": {"waitForSubWorkflow": wait}}
        return self.add(name, "n8n-nodes-base.executeWorkflow", 1.3, p, x, y)

    def tool_wf(self, name, key, description, inputs, x, y):
        p = {"description": description, "workflowId": wfref(key),
             "workflowInputs": {"mappingMode": "defineBelow", "value": inputs, "matchingColumns": [],
                                "schema": schema(list(inputs.keys())), "attemptToConvertTypes": False, "convertFieldsToString": True}}
        return self.add(name, "@n8n/n8n-nodes-langchain.toolWorkflow", 2.2, p, x, y)

    def http_post(self, name, url, json_expr, x, y, fatal=False):
        p = {"method": "POST", "url": url, "sendBody": True, "specifyBody": "json", "jsonBody": json_expr,
             "options": {"timeout": 15000}}
        extra = {"retryOnFail": True, "maxTries": 2, "waitBetweenTries": 1000}
        if not fatal:
            extra["onError"] = "continueErrorOutput"
        return self.add(name, "n8n-nodes-base.httpRequest", 4.2, p, x, y, **extra)

    def respond_text(self, name, body, x, y, content_type="text/xml", code=200):
        return self.add(name, "n8n-nodes-base.respondToWebhook", 1.5,
                        {"respondWith": "text", "responseBody": body,
                         "options": {"responseCode": code, "responseHeaders": {"entries": [{"name": "Content-Type", "value": content_type}]}}}, x, y)

    def http_json(self, name, method, url, json_expr, x, y, cred=None, timeout=15000):
        """HTTP Request with a JSON body on an optional header-auth credential. Errors go to output 1."""
        p = {"method": method, "url": url, "sendBody": True, "specifyBody": "json", "jsonBody": json_expr, "options": {"timeout": timeout}}
        extra = {"onError": "continueErrorOutput"}
        if cred:
            p["authentication"] = "genericCredentialType"; p["genericAuthType"] = "httpHeaderAuth"
            extra["credentials"] = {"httpHeaderAuth": cred}
        return self.add(name, "n8n-nodes-base.httpRequest", 4.2, p, x, y, **extra)

    def load_config(self, x, y):
        """ar_proof_config rows as one object: $('ProofConfig').first().json.<key>."""
        g = self.dt("LoadProofConfig", "ar_proof_config", "get", x, y, all=True)
        a = self.aggregate("ProofRows", "rows", x + 208, y)
        c = self.set_json("ProofConfig", "={{ Object.fromEntries(($json.rows || []).filter(r => r.key).map(r => [r.key, r.value])) }}", x + 416, y)
        self.chain(g, a, c)
        return g, c

    def model(self, name, x, y, temperature=0):
        return self.add(name, "@n8n/n8n-nodes-langchain.lmChatOpenAi", 1.3,
                        {"model": {"__rl": True, "value": MODEL, "mode": "id"},
                         "options": {**({"baseURL": MODEL_BASE_URL} if MODEL_BASE_URL else {}), "temperature": temperature},
                         "responsesApiEnabled": False}, x, y, credentials={"openAiApi": CRED_LM})

    def layout(self):
        """Positions from the zone spec: 208 px pitch, branch rows 272 px lower, <= 8 columns per board row.
        Zones never split across board rows; a zone wider than the remaining columns starts a new row."""
        spec = ZONES[self.key]
        by_name = {n["name"]: n for n in self.nodes}
        seen = set()
        col, board_row = 0, 0
        for z in spec:
            width = max(len(r) for r in z["rows"])
            if col and col + width > MAX_COLS:
                board_row += 1; col = 0
            for ri, row in enumerate(z["rows"]):
                for ci, name in enumerate(row):
                    if name is None:
                        continue
                    if name not in by_name:
                        raise SystemExit(f"{self.key}: zone '{z['title']}' names unknown node {name}")
                    if name in seen:
                        raise SystemExit(f"{self.key}: node {name} is in two zones")
                    seen.add(name)
                    by_name[name]["position"] = [(col + ci) * PITCH, board_row * BOARD_ROW_PITCH + ri * ROW_PITCH]
            col += width
        orphans = sorted(set(by_name) - seen)
        if orphans:
            raise SystemExit(f"{self.key}: nodes outside every zone: {orphans}")
        return {"gutter": 16,
                "zones": [{"title": z["title"], "body": z["body"], "color": z["color"],
                           "nodes": [n for r in z["rows"] for n in r if n]} for z in spec],
                "panels": self.panels}

    def dump(self):
        spec = self.layout()
        wf = {"name": self.name, "nodes": self.nodes, "connections": self.conns,
              "settings": {"executionOrder": "v1"}, "meta": {"description": self.description},
              "tags": TAGS + [ROLE_TAG[self.key[:5]]], "project": PROJECT, "folder": FOLDER}
        wid = IDS.get("wf_" + self.key)
        if wid:
            wf = {"id": wid, **wf}
        path = os.path.join(OUT, self.key + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(wf, f, indent=2, ensure_ascii=False)
            f.write("\n")
        zpath = os.path.join(OUT, self.key + ".zones.json")
        with open(zpath, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2, ensure_ascii=False)
            f.write("\n")
        canvas_layout(path, zpath)
        return path


PITCH, ROW_PITCH, BOARD_ROW_PITCH, MAX_COLS = 208, 272, 1600, 8


def canvas_layout(path, zpath):
    """Zone stickies and the layout gate from the canvas layout scripts (scripts/canvas next to the repository, or N8N_CANVAS_SCRIPTS)."""
    import subprocess
    cands = [os.environ.get("N8N_CANVAS_SCRIPTS", ""),
             os.path.join(ROOT, "scripts", "canvas")]
    d = next((c for c in cands if c and os.path.exists(os.path.join(c, "zone_layout.py"))), None)
    if not d:
        raise SystemExit("n8n-canvas-docs scripts not found (set N8N_CANVAS_SCRIPTS)")
    subprocess.run([sys.executable, os.path.join(d, "zone_layout.py"), path, zpath, "-o", path], check=True, stdout=subprocess.DEVNULL)
    r = subprocess.run([sys.executable, os.path.join(d, "layout_check.py"), path], capture_output=True, text=True)
    if "RESULT: ok" not in r.stdout:
        raise SystemExit(f"canvas gate failed for {os.path.basename(path)}:\n{r.stdout[-1500:]}{r.stderr[-500:]}")


# Journeys of the joint scope (2026-10-22): BOOK, RESCHEDULE, CANCEL, CALLBACK (retailer callback via the case), SELF_SERVICE (spoken instructions).
STATES = ["IDENTIFY", "CONSENT", "NEED", "DEALER", "SERVICES", "SLOT", "CONFIRM", "BOOKED", "LOOKUP", "CONFIRM_CANCEL", "CANCELLED", "MOVED",
          "CALLBACK", "CALLBACK_LOGGED", "SELF_SERVICE", "WRAPUP", "HANDOFF"]
INTENTS = ["BOOK", "RESCHEDULE", "CANCEL", "CALLBACK", "SELF_SERVICE", "OTHER", "UNKNOWN"]
# States a call may end from without a handoff.
END_OK = ["BOOKED", "CANCELLED", "MOVED", "CALLBACK_LOGGED", "SELF_SERVICE", "WRAPUP"]
REASONS = ["CALLER_REQUEST", "SCENARIO_REQUIRES_AGENT", "NOT_UNDERSTOOD", "BACKEND_UNAVAILABLE", "IDENTIFICATION_FAILED", "NO_SLOT", "POLICY", "PLATFORM_ERROR"]
STATES_JS, INTENTS_JS, REASONS_JS, END_OK_JS = (json.dumps(x) for x in (STATES, INTENTS, REASONS, END_OK))


def prompt_text(key, lang_expr="$json.language"):
    """JS expression (no braces) picking an ar_prompts row's text in the session language, English fallback.
    Needs the Prompts aggregate node in the workflow (WF.load_prompts)."""
    row = f"($('Prompts').first().json.prompts.find(p => p.key === '{key}') || {{}})"
    return f"({row}[{lang_expr}] || {row}.en || '')"


def lang_pick(key, lang_expr="$json.language"):
    """Expression that speaks an ar_prompts row in the session language."""
    return f"={{{{ {prompt_text(key, lang_expr)} }}}}"


def with_guard(code, when=None):
    """Expression appending a guard code to $json.guards, optionally only when a JS condition holds."""
    if when:
        return f"={{{{ ({when}) ? $json.guards.concat(['{code}']) : $json.guards }}}}"
    return f"={{{{ $json.guards.concat(['{code}']) }}}}"


# =============================================================================
# AR-17 log event
# =============================================================================
def build_ar17():
    w = WF("ar-17-log-event", "AR-17 Log event", "Append one row to ar_events. Inputs: correlationId, sessionId, caseId, workflow, event, state, tool, latencyMs, outcome, errorCode, detail. Returns {ok}. No PII.")
    w.sticky("## AR-17 Log event\nOne append per event. Columns carry ids, states and codes only, never caller text.", 0, -400, 620, 100)
    t = w.sub_trigger([("correlationId","string"),("sessionId","string"),("caseId","string"),("workflow","string"),("event","string"),("state","string"),("tool","string"),("latencyMs","number"),("outcome","string"),("errorCode","string"),("detail","string")])
    vals = {c: f"={{{{ $json.{c} }}}}" for c in TABLE_COLS["ar_events"] if c != "at"}
    vals["at"] = "={{ $now.toUTC().toISO() }}"
    ins = w.dt("InsertEvent", "ar_events", "insert", 260, 0, values=vals, always=False)
    ret = w.set_fields("ReturnOk", [("ok", True, "boolean")], 520, 0)
    w.chain(t, ins, ret)
    return w

# =============================================================================
# Mocks
# =============================================================================
MOCK_CUSTOMERS = [
    {"vin": "TST2970461A123456", "customerRef": "C-PL-004711", "accountName": "Anna Kowalska", "greetingName": "Pani Kowalska", "vehicleLabel": "GLC 300 4MATIC", "preferredDealerId": "PL-WAW-01", "preferredDealerName": "Dealer Warszawa Okęcie", "market": "PL", "language": "pl", "msisdn": "+48601234567", "plate": "WA12345", "postcode": "00-001", "meId": "", "cvrId": "", "fin": ""},
    {"vin": "TST2970461A223344", "customerRef": "C-PL-004812", "accountName": "Piotr Nowak", "greetingName": "Panie Nowak", "vehicleLabel": "C 200", "preferredDealerId": "PL-WAW-02", "preferredDealerName": "Dealer Warszawa Wola", "market": "PL", "language": "pl", "msisdn": "+48601234999", "plate": "WB98765", "postcode": "01-100", "meId": "", "cvrId": "", "fin": ""},
    {"vin": "TST2130421A654321", "customerRef": "C-ES-002310", "accountName": "Luis García", "greetingName": "Señor García", "vehicleLabel": "Clase E 220 d", "preferredDealerId": "ES-MAD-02", "preferredDealerName": "Dealer Madrid Castellana", "market": "ES", "language": "es", "msisdn": "+34612345678", "plate": "1234ABC", "postcode": "28001", "meId": "", "cvrId": "", "fin": ""},
    {"vin": "TST1770871N998877", "customerRef": "C-DE-009902", "accountName": "Thomas Weber", "greetingName": "Herr Weber", "vehicleLabel": "A 200", "preferredDealerId": "DE-STR-01", "preferredDealerName": "Dealer Stuttgart Mitte", "market": "DE", "language": "de", "msisdn": "+4915112345678", "plate": "SAB1234", "postcode": "70173", "meId": "", "cvrId": "", "fin": ""},
    {"vin": "TST2476011J112233", "customerRef": "C-GB-001177", "accountName": "Priya Patel", "greetingName": "Ms Patel", "vehicleLabel": "EQB 300", "preferredDealerId": "GB-LON-01", "preferredDealerName": "Dealer London West", "market": "GB", "language": "en", "msisdn": "+447700900123", "plate": "LK21XYZ", "postcode": "SW1A 1AA", "meId": "", "cvrId": "", "fin": ""},
    {"vin": "TST2476011J445566", "customerRef": "C-GB-001290", "accountName": "David Hughes", "greetingName": "Mr Hughes", "vehicleLabel": "GLA 200", "preferredDealerId": "GB-LON-01", "preferredDealerName": "Dealer London West", "market": "GB", "language": "en", "msisdn": "+447700900456", "plate": "LM70ABC", "postcode": "W1A 1AA", "meId": "", "cvrId": "", "fin": ""},
]
# Existing appointments for the reschedule and cancel scenarios. Re-seeding restores them.
MOCK_BOOKINGS = [
    {"bookingRef": "f1a00001-0000-4000-a000-000000000001", "confirmationCode": "FIXPLA", "slotId": "PL-WAW-02-20261126-1030", "dealerId": "PL-WAW-02", "dealerName": "Dealer Warszawa Wola", "vin": "TST2970461A223344", "serviceType": "MAINT", "mobility": "NONE", "startsAt": "2026-11-26T10:30:00", "status": "booked", "bookedAt": "2026-10-20T09:00:00Z", "reservationId": "RSV-f1a001-PL-WAW-02", "serviceIds": "MAINT", "movedFrom": "", "customerRef": "C-PL-004812", "fulfilmentId": "NONE"},
    {"bookingRef": "f1a00002-0000-4000-a000-000000000002", "confirmationCode": "FIXGBA", "slotId": "GB-LON-01-20261127-1400", "dealerId": "GB-LON-01", "dealerName": "Dealer London West", "vin": "TST2476011J445566", "serviceType": "MAINT", "mobility": "NONE", "startsAt": "2026-11-27T14:00:00", "status": "booked", "bookedAt": "2026-10-20T09:00:00Z", "reservationId": "RSV-f1a002-GB-LON-01", "serviceIds": "MAINT", "movedFrom": "", "customerRef": "C-GB-001290", "fulfilmentId": "NONE"},
]
# DealerBook services catalogue (API V2 returns the services for a vehicle and dealer; 89 % of cases are maintenance).
DEALERBOOK_SERVICES = [{"serviceId": "MAINT", "name": "Maintenance service"}, {"serviceId": "INSP", "name": "Inspection"}, {"serviceId": "TYRE", "name": "Tyre change"},
                {"serviceId": "REPAIR", "name": "Repair"}, {"serviceId": "WARN", "name": "Warning light check"}]
# Spoken service words (pl, es, de, en) to serviceIds, for the ServicesChosen gate; a plain yes means maintenance.
SERVICE_KEYWORDS = {"MAINT": r"przegl|serwis|maintenance|regular service|mantenimiento|revisi|wartung|service", "INSP": r"inspek|inspection|inspecci",
                    "TYRE": r"opon|tyre|tire|neum|reifen", "REPAIR": r"napraw|repair|reparac|reparatur", "WARN": r"kontrolk|warning|lamp|testigo|warnleucht"}
# Spoken intents in NEED (pl, es, de, en); first match wins, in this order.
INTENT_PATTERNS = [("CANCEL", r"anul|odwo[łl]a|cancel|anular|stornier|absag"),
                   ("RESCHEDULE", r"prze[łl]o[żz]|przesun|inny termin|reschedul|move (my|the)|change (my|the) (appointment|time|date)|another (time|date)|cambiar|aplazar|verschieb|umbuch"),
                   ("CALLBACK", r"oddzwon|zadzwoni|call(ed)? me back|call back|callback|llam(e|en) de vuelta|devuelv|zur[üu]ckruf|r[üu]ckruf"),
                   ("SELF_SERVICE", r"sam[ao]?\b.*(aplikacj|stron)|aplikacj|na stronie|myself|the app\b|website|online|yo mism|la app\b|selbst|die app\b|webseite")]
# States the model may move to from each state; outcome states are entered only by OutcomeSync after a tool wrote the result.
CHAIN = ["NEED", "DEALER", "SERVICES", "SLOT", "CONFIRM"]
TRANSITIONS = {
    "IDENTIFY": ["IDENTIFY", "CONSENT", "HANDOFF"], "CONSENT": ["CONSENT", "NEED", "HANDOFF"],
    **{st: CHAIN + ["LOOKUP", "CALLBACK", "SELF_SERVICE", "HANDOFF"] for st in CHAIN},
    "BOOKED": ["BOOKED", "WRAPUP", "HANDOFF"], "LOOKUP": ["LOOKUP", "CONFIRM_CANCEL", "SLOT", "CONFIRM", "DEALER", "NEED", "CALLBACK", "HANDOFF"],
    "CONFIRM_CANCEL": ["CONFIRM_CANCEL", "LOOKUP", "SLOT", "NEED", "HANDOFF"], "CANCELLED": ["CANCELLED", "WRAPUP", "HANDOFF"], "MOVED": ["MOVED", "WRAPUP", "HANDOFF"],
    "CALLBACK": ["CALLBACK", "NEED", "HANDOFF"], "CALLBACK_LOGGED": ["CALLBACK_LOGGED", "WRAPUP", "HANDOFF"],
    "SELF_SERVICE": ["SELF_SERVICE", "WRAPUP", "NEED", "HANDOFF"], "WRAPUP": ["WRAPUP", "HANDOFF"], "HANDOFF": ["HANDOFF"],
}
# Spoken agreement to a proposed dealer, beyond a plain yes; and words that name a day or a week for a slot search.
DEALER_AGREE = r"(fine|good|great|works|suits|perfect|that one|pasuje|odpowiada|dobrze|passt|gut|vale|bien|perfecto|me va)"
DATE_WORDS = r"(tydz|tygod|przysz|jutro|pojutrze|poniedzia|wtorek|[śs]rod|czwart|pi[ąa]t|sobot|rano|po ?po[łl]udniu|week|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|morning|afternoon|next|soon|asap|earliest|semana|ma[ñn]ana|lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|pronto|woche|morgen|montag|dienstag|mittwoch|donnerstag|freitag|samstag|bald|fr[üu]h)"
FROM_DATE = "={{ /przysz|tygod|next week|pr[óo]xima semana|n[äa]chste woche/.test($json.utter) ? $today.plus({ days: 7 }).toFormat('yyyy-MM-dd') : $today.plus({ days: 1 }).toFormat('yyyy-MM-dd') }}"
JOURNEY_INTENTS = ["BOOK", "RESCHEDULE", "CANCEL", "CALLBACK", "SELF_SERVICE"]
MOCK_DEALERS = [
    {"dealerId": "PL-WAW-01", "name": "Dealer Warszawa Okęcie", "city": "Warszawa", "market": "PL", "bookable": True},
    {"dealerId": "PL-WAW-02", "name": "Dealer Warszawa Wola", "city": "Warszawa", "market": "PL", "bookable": True},
    {"dealerId": "PL-KRK-01", "name": "Dealer Kraków", "city": "Kraków", "market": "PL", "bookable": True},
    {"dealerId": "PL-GDA-01", "name": "Dealer Gdańsk", "city": "Gdańsk", "market": "PL", "bookable": False},
    {"dealerId": "ES-MAD-02", "name": "Dealer Madrid Castellana", "city": "Madrid", "market": "ES", "bookable": True},
    {"dealerId": "ES-BCN-01", "name": "Dealer Barcelona", "city": "Barcelona", "market": "ES", "bookable": True},
    {"dealerId": "DE-STR-01", "name": "Dealer Stuttgart Mitte", "city": "Stuttgart", "market": "DE", "bookable": True},
    {"dealerId": "GB-LON-01", "name": "Dealer London West", "city": "London", "market": "GB", "bookable": True},
]

def build_ar20():
    w = WF("ar-20-mock-crm", "AR-MOCK-CASE Customer account", "Lab stand-in for the CASE customer account. POST ar-mock/crm/customer-lookup {vin, msisdn} -> {found, ...} (VIN first, then the phone number). POST ar-mock/crm/customer-identify {plate, lastName, postcode} -> {found, ...} for spoken identification. Synthetic rows in ar_mock_customers.")
    w.sticky("## Mock CASE customer account\nSynthetic customers in ar_mock_customers (seeded by AR-90). Lookup: VIN, then phone number. Identify: registration plate plus surname or postcode, a placeholder until the identification rule is agreed (question 20; CASE holds me ID, name, e-mail, mobile, postcode, VIN or FIN and plate). Real build: CASE account search in place of the table reads.", 0, -400, 700, 110)
    shape = lambda name, x, y: w.set_json(name, "={{ $json.customerRef ? { found: true, vin: $json.vin, customerRef: $json.customerRef, accountName: $json.accountName, greetingName: $json.greetingName, vehicleLabel: $json.vehicleLabel, preferredDealerId: $json.preferredDealerId, preferredDealerName: $json.preferredDealerName, market: $json.market, language: $json.language, meId: $json.meId || '', cvrId: $json.cvrId || '', fin: $json.fin || '', plate: $json.plate || '' } : { found: false } }}", x, y)
    a = w.webhook("ReceiveLookup", "ar-mock/crm/customer-lookup", 0, 0, auth=False)
    g = w.dt("LookupByVin", "ar_mock_customers", "get", 260, 0, conditions=[("vin", "={{ String($json.body.vin || '-').toUpperCase().trim() }}")])
    f = w.if_node("FoundByVin", "={{ $json.customerRef }}", 390, 0, kind="string")
    gp = w.dt("LookupByPhone", "ar_mock_customers", "get", 520, 120, conditions=[("msisdn", "={{ String($('ReceiveLookup').first().json.body.msisdn || '-').trim() }}")])
    s = shape("ShapeFixture", 650, 0)
    c = w.respond("RespondCustomer", "={{ JSON.stringify($json) }}", 780, 0)
    w.chain(a, g, f)
    w.link(f, s, 0); w.link(f, gp, 1); w.link(gp, s); w.link(s, c)
    # identify: plate normalised (upper, no spaces); then surname inside the account name or the postcode must match
    a2 = w.webhook("ReceiveIdentify", "ar-mock/crm/customer-identify", 0, 400, auth=False)
    gi = w.dt("LookupByPlate", "ar_mock_customers", "get", 260, 400, conditions=[("plate", "={{ String($json.body.plate || '-').toUpperCase().replace(/[^A-Z0-9]/g, '') }}")])
    B = "$('ReceiveIdentify').first().json.body"
    mi = w.if_rules("MatchesIdentity", [
        w.cond("={{ $json.customerRef || '' }}", "notEmpty"),
        w.cond(f"={{{{ ({B}.lastName && String($json.accountName || '').toLowerCase().includes(String({B}.lastName).toLowerCase().trim())) || ({B}.postcode && String($json.postcode || '').replace(/\\s/g, '').toUpperCase() === String({B}.postcode).replace(/\\s/g, '').toUpperCase()) }}}}", "true", kind="boolean"),
    ], 390, 400)
    si = shape("ShapeIdentified", 520, 320)
    nf = w.set_json("ShapeNotFound", "={{ { found: false } }}", 520, 480)
    ci = w.respond("RespondIdentify", "={{ JSON.stringify($json) }}", 780, 400)
    w.chain(a2, gi, mi)
    w.link(mi, si, 0); w.link(mi, nf, 1); w.link(si, ci); w.link(nf, ci)
    return w

DEALERBOOK_FULFILMENTS = [{"fulfilmentId": "NONE", "name": "No mobility needed"}, {"fulfilmentId": "COURTESY_CAR", "name": "Courtesy car"},
                   {"fulfilmentId": "PICKUP", "name": "Pick-up and return"}, {"fulfilmentId": "WAIT", "name": "Wait at the retailer"}]
# The mock reservation id carries the dealer, so services, fulfilments, patches and timeslots resolve it without state.
RSV_DEALER = "((String(%s || '').match(/^RSV-[0-9a-f]{6}-(.+)$/) || [])[1] || '')"

DEALERBOOK_SLOTS_JS = """// Mock of DealerBook timeslots. Deterministic from dealerId and date so tests repeat. The dealer row comes from ar_mock_dealers (LoadDealer).
const b = $('ReceiveTimeslots').first().json.body || {};
const dealer = $json;
const dealerId = String(dealer.dealerId || b.dealerId || '');
if (!dealer.dealerId || dealer.bookable !== true) return [{ json: { dealerId, dealerName: dealer.name || null, slots: [], reason: 'dealer_not_dealerbook_enabled' } }];
let from = new Date(String(b.fromDateTime || b.fromDate || '').slice(0, 10) || Date.now());
if (isNaN(from.getTime())) from = new Date();
from.setUTCHours(0, 0, 0, 0);
let to = new Date(String(b.toDateTime || b.todateTime || '').slice(0, 10) || 0);
to = isNaN(to.getTime()) || to <= from ? new Date(from.getTime() + 14 * 86400000) : new Date(to.getTime() + 86400000 - 1);
const seed = [...dealerId].reduce((a, ch) => a + ch.charCodeAt(0), 0);
const times = ['08:00', '10:30', '14:00'];
const slots = [];
for (let d = 1; d <= 30 && slots.length < 6; d++) {
  const day = new Date(from.getTime() + d * 86400000);
  if (day > to) break;
  if (day.getUTCDay() === 0) continue;
  times.forEach((t, i) => { if ((seed + d + i) % 3 !== 0) slots.push({ slotId: `${dealerId}-${day.toISOString().slice(0,10).replace(/-/g,'')}-${t.replace(':','')}`, startsAt: `${day.toISOString().slice(0,10)}T${t}:00`, dealerId }); });
}
return [{ json: { dealerId, dealerName: dealer.name, slots: slots.slice(0, 6) } }];"""

def build_ar21():
    w = WF("ar-21-mock-dealerbook", "AR-MOCK-DealerBook", "Lab stand-in for DealerBook API V2 in the order of the endpoint sheet (2026-10-23): dealers, POST reservations, GET services, GET fulfilments, PATCH reservations (services, fulfilment, timeslot), GET timeslots, POST bookings, bookings by customerId, chassisNumber, offerId or status, cancel, reschedule. Dealers in ar_mock_dealers, deterministic slots, bookings in ar_mock_bookings. Replace with the DealerBook API.")
    w.sticky("## Mock DealerBook\nEndpoints under ar-mock/dealerbook/* follow the API V2 lifecycle of the endpoint sheet (2026-10-23): a reservation (vehicle and dealer), its services and fulfilments, a PATCH per selection, timeslots in a window, then the booking. The reservation id carries the dealer so the mock stays stateless; a PATCH is acknowledged, not stored. A slotId containing FULL returns 409. Lookup keys are customerId, chassisNumber, offerId and status; a booking has a DealerBook booking id and a six-letter reference code. Bookings persist in ar_mock_bookings. Payload shapes are the mock's own until question 23 is answered.", 0, -400, 760, 150)
    # dealers: every row, then the request's market, dealerId and city (accent-insensitive) narrow the list
    a = w.webhook("ReceiveDealers", "ar-mock/dealerbook/dealers", 0, 0, auth=False)
    ld = w.dt("LoadDealers", "ar_mock_dealers", "get", 260, 0, all=True, always=False)
    ag = w.aggregate("AllDealers", "dealers", 390, 0)
    B = "$('ReceiveDealers').first().json.body"
    norm = lambda x: f"String({x} || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')"
    f = w.set_fields("FilterDealers", [("dealers", f"={{{{ $json.dealers.filter(d => (!{B}.market || d.market === String({B}.market).toUpperCase()) && (!{B}.dealerId || d.dealerId === {B}.dealerId) && (!{B}.city || {norm('d.city')}.includes({norm(B + '.city')}))).map(d => ({{ dealerId: d.dealerId, name: d.name, city: d.city, market: d.market, bookable: d.bookable === true }})) }}}}", "array")], 520, 0)
    c = w.respond("RespondDealers", "={{ JSON.stringify($json) }}", 780, 0)
    w.chain(a, ld, ag, f, c)
    # POST reservations: vehicle plus dealer -> reservation id (the id carries the dealer)
    a = w.webhook("ReceiveReservation", "ar-mock/dealerbook/reservations", 0, 200, auth=False)
    rd = w.dt("LoadReservationDealer", "ar_mock_dealers", "get", 260, 200, conditions=[("dealerId", "={{ String($json.body.dealerId || '') }}")])
    rh = w.add("HashReservation", "n8n-nodes-base.crypto", 2, {"action": "hash", "type": "SHA256", "value": "={{ String($('ReceiveReservation').first().json.body.vin || '') + ':' + String($json.dealerId || '') + ':' + Date.now() }}", "dataPropertyName": "hash", "encoding": "hex"}, 390, 200)
    RB = "$('ReceiveReservation').first().json.body"
    rs = w.set_json("ComputeReservation", f"={{{{ $json.dealerId && $json.bookable === true ? {{ ok: true, reservationId: 'RSV-' + $json.hash.slice(0, 6) + '-' + $json.dealerId, dealerId: $json.dealerId, dealerName: $json.name, vin: {RB}.vin || '', customerId: {RB}.customerId || '' }} : {{ ok: false, error: $json.dealerId ? 'dealer_not_dealerbook_enabled' : 'dealer_unknown', dealerId: String({RB}.dealerId || ''), dealerName: $json.name || '' }} }}}}", 520, 200)
    rr = w.respond("RespondReservation", "={{ JSON.stringify($json) }}", 780, 200)
    w.chain(a, rd, rh, rs, rr)
    # GET services and GET fulfilments of a reservation (POST on the lab); an id that names no dealer is 404
    for label, path, y, items in (("Services", "services", 400, "services: " + json.dumps(DEALERBOOK_SERVICES)), ("Fulfilments", "fulfilments", 540, "fulfilments: " + json.dumps(DEALERBOOK_FULFILMENTS))):
        a = w.webhook("Receive" + label, "ar-mock/dealerbook/" + path, 0, y, auth=False)
        d = w.dt("Load" + label + "Dealer", "ar_mock_dealers", "get", 260, y, conditions=[("dealerId", "={{ " + RSV_DEALER % "$json.body.reservationId" + " }}")])
        R = f"$('Receive{label}').first().json.body"
        s = w.set_json("Compute" + label, f"={{{{ $json.dealerId ? {{ ok: true, reservationId: {R}.reservationId, dealerId: $json.dealerId, {items} }} : {{ ok: false, error: 'reservation_unknown', reservationId: String({R}.reservationId || '') }} }}}}", 390, y)
        c = w.respond("Respond" + label, "={{ JSON.stringify($json) }}", 520, y, code="={{ $json.ok ? 200 : 404 }}")
        w.chain(a, d, s, c)
    # PATCH reservations: the selected services, fulfilment or timeslot; acknowledged, not stored
    a = w.webhook("ReceivePatch", "ar-mock/dealerbook/reservations", 0, 700, auth=False, method="PATCH")
    d = w.dt("LoadPatchDealer", "ar_mock_dealers", "get", 260, 700, conditions=[("dealerId", "={{ " + RSV_DEALER % "$json.body.reservationId" + " }}")])
    P = "$('ReceivePatch').first().json.body"
    s = w.set_json("ComputePatch", f"={{{{ $json.dealerId ? {{ ok: true, reservationId: {P}.reservationId, patched: ['serviceIds', 'fulfilmentId', 'timeslotId'].filter(k => {P}[k] !== undefined && {P}[k] !== '' && !(Array.isArray({P}[k]) && !{P}[k].length)), serviceIds: {P}.serviceIds || [], fulfilmentId: {P}.fulfilmentId || '', timeslotId: {P}.timeslotId || '' }} : {{ ok: false, error: 'reservation_unknown', reservationId: String({P}.reservationId || '') }} }}}}", 390, 700)
    c = w.respond("RespondPatch", "={{ JSON.stringify($json) }}", 520, 700, code="={{ $json.ok ? 200 : 404 }}")
    w.chain(a, d, s, c)
    # GET timeslots: the reservation's dealer (or an explicit dealerId) in the window fromDateTime..toDateTime
    a = w.webhook("ReceiveTimeslots", "ar-mock/dealerbook/timeslots", 0, 900, auth=False)
    dl = w.dt("LoadDealer", "ar_mock_dealers", "get", 260, 900, conditions=[("dealerId", "={{ String($json.body.dealerId || " + RSV_DEALER % "$json.body.reservationId" + ") }}")])
    b = w.code("GenerateSlots", DEALERBOOK_SLOTS_JS, 390, 900)
    c = w.respond("RespondSlots", "={{ JSON.stringify($json) }}", 520, 900)
    w.chain(a, dl, b, c)
    # POST bookings: required fields, the FULL probe, then the booking id and the six-letter reference code from the idempotency key
    a = w.webhook("ReceiveBook", "ar-mock/dealerbook/bookings", 0, 1100, auth=False)
    slot_expr = "String($json.body.timeslotId || $json.body.slotId || '')"
    inv = w.if_rules("BookInvalid", [w.cond("={{ String($json.body.idempotencyKey || '') }}", "empty"), w.cond("={{ " + slot_expr + " }}", "empty"),
                                     w.cond("={{ String($json.body.reservationId || '') }}", "empty")], 260, 1100, combinator="or")
    ri = w.set_fields("ReplyInvalid", [("conflict", False, "boolean"), ("invalid", True, "boolean"), ("message", "reservationId, timeslotId and idempotencyKey are required", "string")], 390, 1260)
    full = w.if_rules("SlotFull", [w.cond("={{ " + slot_expr + " }}", "contains", "FULL")], 390, 1100)
    rc = w.set_fields("ReplyConflict", [("conflict", True, "boolean"), ("invalid", False, "boolean"), ("message", "slot no longer available", "string")], 520, 1260)
    hk = w.add("HashKey", "n8n-nodes-base.crypto", 2, {"action": "hash", "type": "SHA256", "value": "={{ $json.body.idempotencyKey }}", "dataPropertyName": "hash", "encoding": "hex"}, 520, 1100)
    starts = "={{ (m => m ? m[1].slice(0, 4) + '-' + m[1].slice(4, 6) + '-' + m[1].slice(6, 8) + 'T' + m[2].slice(0, 2) + ':' + m[2].slice(2, 4) + ':00' : '')(" + slot_expr + ".match(/-(\\d{8})-(\\d{4})$/)) }}"
    b = w.set_fields("ComputeBooking", [
        ("conflict", False, "boolean"), ("invalid", False, "boolean"),
        ("bookingRef", "={{ (h => h.slice(0, 8) + '-' + h.slice(8, 12) + '-4' + h.slice(13, 16) + '-a' + h.slice(17, 20) + '-' + h.slice(20, 32))($json.hash) }}", "string"),
        ("confirmationCode", "={{ [0, 1, 2, 3, 4, 5].map(i => 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'[parseInt($json.hash.slice(32 + i * 2, 34 + i * 2), 16) % 26]).join('') }}", "string"),
        ("slotId", "={{ " + slot_expr + " }}", "string"),
        ("dealerId", "={{ $json.body.dealerId || " + RSV_DEALER % "$json.body.reservationId" + " }}", "string"),
        ("dealerName", "={{ $json.body.dealerName || '' }}", "string"), ("vin", "={{ String($json.body.chassisNumber || $json.body.vin || '').toUpperCase() }}", "string"),
        ("customerRef", "={{ $json.body.customerId || '' }}", "string"),
        ("serviceType", "={{ String($json.body.serviceIds || $json.body.serviceType || '') }}", "string"), ("serviceIds", "={{ String($json.body.serviceIds || '') }}", "string"),
        ("fulfilmentId", "={{ $json.body.fulfilmentId || 'NONE' }}", "string"), ("mobility", "={{ $json.body.fulfilmentId || $json.body.mobility || 'NONE' }}", "string"),
        ("startsAt", starts, "string"),
        ("status", "booked", "string"), ("bookedAt", "={{ $now.toUTC().toISO() }}", "string"),
        ("reservationId", "={{ $json.body.reservationId || '' }}", "string"), ("movedFrom", "", "string"),
    ], 650, 1100)
    i = w.if_node("IsConflict", "={{ $json.conflict === true || $json.invalid === true }}", 780, 1100)
    r409 = w.respond("RespondConflict", "={{ JSON.stringify({ error: $json.invalid ? 'validation_error' : 'slot_taken', message: $json.message }) }}", 1040, 1000, code="={{ $json.invalid ? 400 : 409 }}")
    up = w.dt("UpsertBooking", "ar_mock_bookings", "upsert", 1040, 1200, conditions=[("bookingRef", "={{ $json.bookingRef }}")],
              values={c2: f"={{{{ $json.{c2} }}}}" for c2 in TABLE_COLS["ar_mock_bookings"]})
    rb = w.respond("RespondBooked", "={{ (b => JSON.stringify({ bookingId: b.bookingRef, referenceCode: b.confirmationCode, ...b }))($('ComputeBooking').first().json) }}", 1300, 1200)
    w.chain(a, inv)
    w.link(inv, ri, 0); w.link(ri, i)
    w.link(inv, full, 1)
    w.link(full, rc, 0); w.link(rc, i)
    w.link(full, hk, 1); w.chain(hk, b, i)
    w.link(i, r409, 0)
    w.link(i, up, 1)
    w.link(up, rb)
    # GET bookings by customerId, chassisNumber, offerId, status (POST on the lab); referenceCode narrows to the spoken code
    a = w.webhook("ReceiveSearch", "ar-mock/dealerbook/bookings/search", 0, 1400, auth=False)
    g = w.dt("LoadAllBookings", "ar_mock_bookings", "get", 260, 1400, all=True)
    ab = w.aggregate("AllBookings", "rows", 390, 1400)
    BB = "$('ReceiveSearch').first().json.body"
    s = w.set_fields("FilterBookings", [("bookings", "={{ ((q, rows) => (q.customerId || q.chassisNumber || q.offerId) ? rows.filter(r => r.bookingRef && (!q.status || r.status === q.status) && (!q.customerId || r.customerRef === q.customerId) && (!q.chassisNumber || r.vin === String(q.chassisNumber).toUpperCase()) && (!q.offerId || r.reservationId === q.offerId) && (!q.referenceCode || r.confirmationCode === String(q.referenceCode).toUpperCase().replace(/[^A-Z]/g, ''))).map(r => ({ bookingId: r.bookingRef, referenceCode: r.confirmationCode, ...r })) : [])(" + BB + " || {}, $json.rows || []) }}", "array")], 520, 1400)
    c = w.respond("RespondBookings", "={{ JSON.stringify($json) }}", 780, 1400)
    w.chain(a, g, ab, s, c)
    # POST bookings/{bookingId} cancel
    a = w.webhook("ReceiveCancel", "ar-mock/dealerbook/bookings/cancel", 0, 1600, auth=False)
    u = w.dt("CancelBooking", "ar_mock_bookings", "update", 260, 1600, conditions=[("bookingRef", "={{ String($json.body.bookingId || $json.body.bookingRef || '') }}")], values={"status": "cancelled"})
    c = w.respond("RespondCancelled", "={{ JSON.stringify({ ok: true, bookingId: $('ReceiveCancel').first().json.body.bookingId || $('ReceiveCancel').first().json.body.bookingRef || '', status: 'cancelled' }) }}", 520, 1600)
    w.chain(a, u, c)
    # POST bookings/{bookingId}/reschedule: the booking keeps its id and reference code, gets the new timeslot
    a = w.webhook("ReceiveReschedule", "ar-mock/dealerbook/bookings/reschedule", 0, 1800, auth=False)
    lb = w.dt("LoadBookingToMove", "ar_mock_bookings", "get", 260, 1800, conditions=[("bookingRef", "={{ String($json.body.bookingId || $json.body.bookingRef || '-') }}")])
    fb = w.if_rules("BookingFound", [w.cond("={{ $json.bookingRef || '' }}", "notEmpty"), w.cond("={{ $json.status || '' }}", "equals", "booked")], 390, 1800)
    RQ = "$('ReceiveReschedule').first().json.body"
    mslot = f"String({RQ}.timeslotId || {RQ}.slotId || '')"
    mv = w.dt("RescheduleBooking", "ar_mock_bookings", "update", 520, 1800, conditions=[("bookingRef", "={{ $json.bookingRef }}")],
              values={"slotId": "={{ " + mslot + " }}", "startsAt": starts.replace(slot_expr, mslot), "movedFrom": "={{ $json.slotId }}", "status": "booked",
                      "dealerName": f"={{{{ {RQ}.dealerName || $json.dealerName || '' }}}}"})
    rm = w.respond("RespondRescheduled", "={{ JSON.stringify({ ok: true, bookingId: $json.bookingRef, referenceCode: $json.confirmationCode, bookingRef: $json.bookingRef, confirmationCode: $json.confirmationCode, slotId: $json.slotId, startsAt: $json.startsAt, dealerId: $json.dealerId, dealerName: $json.dealerName, movedFrom: $json.movedFrom, status: $json.status }) }}", 780, 1800)
    r404 = w.respond("RespondNoBooking", "={{ JSON.stringify({ error: 'not_found', message: 'no booked appointment with that id' }) }}", 520, 1960, code=404)
    w.chain(a, lb, fb)
    w.link(fb, mv, 0); w.link(mv, rm); w.link(fb, r404, 1)
    return w


def build_ar22():
    w = WF("ar-22-mock-case-system", "AR-MOCK-CASE Case", "Lab stand-in for the CASE case API. POST ar-mock/casesys/case {op: create|update|close|delegate, correlationId, caseId, ...} -> case (mandatory fields: accountName, caseOrigin, description, serviceType, plus the CASE booking prerequisites of 2026-10-23). POST ar-mock/casesys/appointments {vin, bookingRef} -> the account's booked appointments. Persists in ar_mock_cases.")
    w.sticky("## Mock CASE case\nOne endpoint, op in the body. Case numbers are deterministic from the correlation ID (SHA-256), so a re-created case is the same case. delegate sets Delegated and delegatedTo (XCCC for a retailer callback). The appointment search reads the lab booking store. Real build: the case system's REST with OAuth2 client credentials.", 0, -400, 700, 110)
    a = w.webhook("ReceiveCaseOp", "ar-mock/casesys/case", 0, 0, auth=False)
    h = w.add("HashCorrelation", "n8n-nodes-base.crypto", 2, {"action": "hash", "type": "SHA256", "value": "={{ String($json.body.correlationId || Date.now()) }}", "dataPropertyName": "hash", "encoding": "hex"}, 260, 0)
    B = "$('ReceiveCaseOp').first().json.body"
    OP = f"({B}.op || 'create')"
    keep = lambda f, default="''": f"={{{{ {B}.{f} || $json.{f} || {default} }}}}"
    lc = w.dt("LoadCase", "ar_mock_cases", "get", 390, 0, conditions=[("caseId", f"={{{{ String({B}.caseId || '-') }}}}")])
    b = w.set_fields("ComputeCase", [
        ("op", f"={{{{ {OP} }}}}", "string"),
        ("caseId", f"={{{{ ({OP} === 'create' || !{B}.caseId) ? 'CS-' + (10000000 + (parseInt($('HashCorrelation').first().json.hash.slice(0, 8), 16) % 90000000)) : {B}.caseId }}}}", "string"),
        ("correlationId", keep("correlationId"), "string"),
        ("status", f"={{{{ {B}.status || ({OP} === 'create' ? 'In Progress' : {OP} === 'delegate' ? 'Delegated' : {OP} === 'close' ? 'Closed' : ($json.status || 'In Progress')) }}}}", "string"),
        ("subject", keep("subject", "'Appointment booking via voice assistant'"), "string"),
        ("classification", keep("classification", "'Appointment Booking'"), "string"),
        ("solution", keep("solution"), "string"), ("delegatedReason", keep("delegatedReason"), "string"),
        ("accountName", keep("accountName"), "string"), ("caseOrigin", keep("caseOrigin"), "string"),
        ("description", keep("description"), "string"), ("serviceType", keep("serviceType", "'CS'"), "string"),
        ("delegatedTo", f"={{{{ {B}.delegatedTo || ({OP} === 'delegate' ? 'XCCC' : ($json.delegatedTo || '')) }}}}", "string"),
        ("callbackNote", keep("callbackNote"), "string"),
        ("caseCountry", keep("caseCountry"), "string"), ("accountId", keep("accountId"), "string"), ("assetId", keep("assetId"), "string"), ("vinOrFin", keep("vinOrFin"), "string"),
        ("licensePlate", keep("licensePlate"), "string"), ("mileage", keep("mileage"), "string"), ("mileageUnit", keep("mileageUnit"), "string"), ("assignedDealerId", keep("assignedDealerId"), "string"),
        ("type", keep("type"), "string"), ("topic", keep("topic"), "string"), ("reason", keep("reason"), "string"), ("primaryContact", keep("primaryContact"), "string"),
        ("openedAt", f"={{{{ {OP} === 'create' ? $now.toUTC().toISO() : ($json.openedAt || '') }}}}", "string"),
        ("lastUpdate", "={{ $now.toUTC().toISO() }}", "string"),
    ], 520, 0)
    vals = {c: f"={{{{ $json.{c} }}}}" for c in TABLE_COLS["ar_mock_cases"]}
    vals["openedAt"] = "={{ $json.op === 'create' ? $json.openedAt : undefined }}"
    up = w.dt("UpsertCase", "ar_mock_cases", "upsert", 650, 0, conditions=[("caseId", "={{ $json.caseId }}")], values=vals)
    c = w.respond("RespondCase", "={{ JSON.stringify($('ComputeCase').first().json) }}", 780, 0)
    w.chain(a, h, lc, b, up, c)
    # appointments of the account: forwarded to the lab booking store (CASE searches account cases, VIN or booking number)
    a2 = w.webhook("ReceiveAppointments", "ar-mock/casesys/appointments", 0, 300, auth=False)
    fw = w.http_post("ForwardToBookings", MOCK + "/dealerbook/bookings", "={{ JSON.stringify({ vin: $json.body.vin || '', bookingRef: $json.body.bookingRef || '' }) }}", 260, 300, fatal=True)
    sa = w.set_fields("ShapeAppointments", [("appointments", "={{ ($json.bookings || []).map(b => ({ bookingRef: b.bookingRef, confirmationCode: b.confirmationCode, slotId: b.slotId, startsAt: b.startsAt, dealerId: b.dealerId, dealerName: b.dealerName || '', serviceIds: b.serviceIds || b.serviceType || '' })) }}", "array")], 520, 300)
    ra = w.respond("RespondAppointments", "={{ JSON.stringify($json) }}", 780, 300)
    w.chain(a2, fw, sa, ra)
    return w

def build_ar23():
    w = WF("ar-23-mock-comms", "AR-MOCK-COMMS SMS and email", "Lab stand-in for the communication service. POST ar-mock/comms/send {channel, customerRef, language, text} -> {messageId}. Sends nothing.")
    w.sticky("## Mock communication service\nReturns a message id. The real service resolves the customer's number or address from the CRM reference, so n8n never stores it.", 0, -400, 700, 100)
    a = w.webhook("ReceiveSend", "ar-mock/comms/send", 0, 0, auth=False)
    hr = w.if_rules("HasRequired", [w.cond("={{ String($json.body.customerRef || '') }}", "notEmpty"), w.cond("={{ String($json.body.text || '') }}", "notEmpty")], 260, 0)
    acc = w.set_fields("AcceptMessage", [("accepted", True, "boolean"), ("messageId", "={{ 'msg-' + Date.now().toString(36) }}", "string"),
        ("channel", "={{ $json.body.channel || 'sms' }}", "string"), ("customerRef", "={{ $json.body.customerRef }}", "string"), ("length", "={{ String($json.body.text).length }}", "number")], 390, -80)
    rej = w.set_fields("RejectMessage", [("accepted", False, "boolean"), ("error", "customerRef and text are required", "string")], 390, 120)
    c = w.respond("RespondSend", "={{ JSON.stringify($json) }}", 520, 0, code="={{ $json.accepted ? 200 : 400 }}")
    w.chain(a, hr)
    w.link(hr, acc, 0); w.link(hr, rej, 1)
    w.link(acc, c); w.link(rej, c)
    return w

# =============================================================================
# Tools and sub-workflows
# =============================================================================
def build_ar10():
    w = WF("ar-10-identify-customer", "AR-10 Identify customer by VIN and MSISDN", "Looks the caller up in the CASE customer account from the UUI identifiers (VIN first, then the phone number). Inputs: vin, msisdn, correlationId. Returns {ok, known, customerRef, accountName, greetingName, vehicleLabel, preferredDealerId, preferredDealerName, market, language}. Called by AR-01, never by the model.")
    w.sticky("## AR-10 Identify customer\nIdentity from the UUI: VIN for an in-car call, the phone number for an IVR call. The model never sees either. An unknown caller is identified by spoken data points through IdentifyCaller (AR-18).", 0, -400, 700, 100)
    t = w.sub_trigger([("vin","string"),("msisdn","string"),("correlationId","string")])
    h = w.http_post("CallCrm", MOCK + "/crm/customer-lookup", "={{ JSON.stringify({ vin: $json.vin, msisdn: $json.msisdn, correlationId: $json.correlationId }) }}", 260, 0)
    s = w.set_fields("ShapeCustomer", [("ok", True, "boolean"), ("known", "={{ !!$json.found }}", "boolean"), ("customerRef", "={{ $json.customerRef || '' }}", "string"),
        ("accountName", "={{ $json.accountName || '' }}", "string"),
        ("vehicleRef", "={{ $json.vin || '' }}", "string"), ("greetingName", "={{ $json.greetingName || '' }}", "string"), ("vehicleLabel", "={{ $json.vehicleLabel || '' }}", "string"),
        ("preferredDealerId", "={{ $json.preferredDealerId || '' }}", "string"), ("preferredDealerName", "={{ $json.preferredDealerName || '' }}", "string"),
        ("market", "={{ $json.market || '' }}", "string"), ("language", "={{ $json.language || '' }}", "string"),
        ("meId", "={{ $json.meId || '' }}", "string"), ("cvrId", "={{ $json.cvrId || '' }}", "string"), ("fin", "={{ $json.fin || '' }}", "string"), ("plate", "={{ $json.plate || '' }}", "string")], 520, -80)
    e = w.return_error("ReturnCrmError", 520, 120)
    w.chain(t, h)
    w.link(h, s, 0)
    w.link(h, e, 1)
    return w

def build_ar11():
    w = WF("ar-11-find-dealers", "AR-11 Tool: Find dealers", "Agent tool. Lists dealers for the session's market, optionally filtered by city. Inputs: sessionId (plumbed), city. Returns {ok, dealers:[{dealerId, name, city, bookable}]}.")
    w.sticky("## AR-11 FindDealers\nMarket comes from the session row, city from the model. Non-bookable dealers are returned flagged so the agent routes them to a handoff.", 0, -400, 700, 100)
    t = w.sub_trigger([("sessionId","string"),("city","string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    h = w.http_post("CallDealerBookDealers", MOCK + "/dealerbook/dealers", "={{ JSON.stringify({ market: $json.market, city: $('ToolInput').first().json.city || '' }) }}", 520, 0)
    s = w.set_fields("ShapeDealers", [("ok", True, "boolean"), ("count", "={{ ($json.dealers || []).slice(0, 5).length }}", "number"),
        ("dealers", "={{ ($json.dealers || []).slice(0, 5) }}", "array"),
        ("note", "={{ ($json.dealers || []).length ? 'Offer at most two by name. Dealers with bookable false cannot be booked here.' : 'No dealer matched. Ask for another city or offer the preferred dealer.' }}", "string")], 780, -80)
    e = w.return_error("ReturnDealersError", 780, 120)
    w.chain(t, g, h)
    w.link(h, s, 0)
    w.link(h, e, 1)
    return w

def build_ar12():
    w = WF("ar-12-find-slots", "AR-12 Tool: Find appointment slots", "Agent tool. Writes the chosen services to the DealerBook reservation (PATCH) and returns up to maxSlotsOffered (market row, default three) bookable timeslots in the market's window from a date, for the session's reservation (new booking) or the existing booking's offer (reschedule). Inputs: sessionId (plumbed), serviceIds, fromDate (YYYY-MM-DD). Side effect: stores the selected services and the offered slots on the session so BookAppointment and MoveAppointment can validate the choice.")
    w.sticky("## AR-12 FindSlots\nDealerBook API V2 order (endpoint sheet, 2026-10-23): the selected services are a PATCH on the reservation, then GET timeslots for the reservation in a window (fromDateTime to toDateTime, slotWindowDays of the market row). A reschedule searches the existing booking's offer id and skips the PATCH. The offered slots are written to the session row; a booking or a move can only name one of them. Labels are spoken-form weekday, date and time in the session language.", 0, -400, 760, 130)
    t = w.sub_trigger([("sessionId","string"),("serviceIds","string"),("fromDate","string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    m = w.dt("LoadMarket", "ar_markets", "get", 390, 0, conditions=[("market", "={{ $json.market }}")])
    S, I, M = "$('LoadSession').first().json", "$('ToolInput').first().json", "$('LoadMarket').first().json"
    # A new booking without a reservation (the dialogue skipped DEALER) gets one here from the session's dealer: the API needs it before any PATCH.
    hr = w.if_node("HasReservation", "={{ !!(" + S + ".reservationId || " + S + ".appointmentRef) || !" + S + ".dealerId }}", 520, 0)
    cr = w.exec_wf("CreateReservationFirst", "ar-19-create-reservation", {"sessionId": "={{ " + S + ".sessionId }}", "dealerId": "={{ " + S + ".dealerId || '' }}"}, 520, 200)
    rsv = "(" + S + ".reservationId || ($('CreateReservationFirst').isExecuted ? ($('CreateReservationFirst').first().json.reservationId || '') : ''))"
    from_dt = "(/^\\d{4}-\\d{2}-\\d{2}$/.test(String(" + I + ".fromDate || '')) ? DateTime.fromISO(" + I + ".fromDate) : $today.plus({ days: 1 }))"
    # Reschedule: the existing booking's offer id and dealer. New booking: the reservation in hand. $json here is the market row.
    ws = w.set_fields("SlotWindow", [
        ("sessionId", "={{ " + S + ".sessionId }}", "string"), ("market", "={{ " + S + ".market }}", "string"), ("language", "={{ " + S + ".language || 'en' }}", "string"),
        ("isNew", "={{ !" + S + ".appointmentRef && !!" + rsv + " }}", "boolean"),
        ("reservationId", "={{ " + S + ".appointmentRef ? (" + S + ".appointmentOfferId || '') : " + rsv + " }}", "string"),
        ("dealerId", "={{ " + S + ".appointmentRef ? (" + S + ".appointmentDealerId || '') : (" + S + ".dealerId || '') }}", "string"),
        ("dealerName", "={{ " + S + ".appointmentRef ? (" + S + ".appointmentDealerName || '') : (" + S + ".dealerName || '') }}", "string"),
        ("serviceIds", "={{ String(" + I + ".serviceIds || " + S + ".selectedServices || '').toUpperCase().replace(/\\s/g, '') }}", "string"),
        ("fromDateTime", "={{ " + from_dt + ".toFormat(\"yyyy-MM-dd'T'00:00:00\") }}", "string"),
        ("toDateTime", "={{ " + from_dt + ".plus({ days: Number(" + M + ".slotWindowDays) || 14 }).toFormat(\"yyyy-MM-dd'T'23:59:59\") }}", "string"),
        ("maxSlots", "={{ Number(" + M + ".maxSlotsOffered) || 3 }}", "number"),
    ], 650, 0)
    nb = w.if_node("NewBooking", "={{ $json.isNew }}", 650, 0)
    W = "$('SlotWindow').first().json"
    ps = w.http_json("PatchServices", "PATCH", MOCK + "/dealerbook/reservations", "={{ JSON.stringify({ reservationId: $json.reservationId, serviceIds: String($json.serviceIds || 'MAINT').split(',').filter(Boolean) }) }}", 780, -100)
    h = w.http_post("CallDealerBookTimeslots", MOCK + "/dealerbook/timeslots", "={{ JSON.stringify({ reservationId: " + W + ".reservationId, dealerId: " + W + ".dealerId, fromDateTime: " + W + ".fromDateTime, toDateTime: " + W + ".toDateTime, market: " + W + ".market }) }}", 910, 0)
    # Keeps at most maxSlotsOffered slots and labels them for speech (Luxon, session locale: "wtorek 22.10. 08:00").
    s = w.set_fields("ShapeSlots", [
        ("slots", "={{ ($json.slots || []).slice(0, " + W + ".maxSlots).map(o => ({ slotId: o.slotId, startsAt: o.startsAt, dealerId: o.dealerId, dealerName: $json.dealerName || " + W + ".dealerName, label: DateTime.fromISO(o.startsAt, { zone: 'utc' }).setLocale(" + W + ".language).toFormat('cccc dd.MM. HH:mm') })) }}", "array"),
        ("dealerId", "={{ $json.dealerId || " + W + ".dealerId }}", "string"),
        ("dealerName", "={{ $json.dealerName || " + W + ".dealerName }}", "string"),
        ("selectedServices", "={{ " + W + ".serviceIds }}", "string"),
        ("reason", "={{ $json.reason || '' }}", "string"), ("sessionId", "={{ " + W + ".sessionId }}", "string"),
    ], 1170, -80)
    o = w.set_fields("OfferSlots", [
        ("offeredSlots", "={{ JSON.stringify($json.slots) }}", "string"),
        ("result", "={{ $json.slots.length ? { ok: true, dealerId: $json.dealerId, dealerName: $json.dealerName, services: $json.selectedServices, slots: $json.slots.map(o => ({ slotId: o.slotId, when: o.label })), note: 'Offer these by day and time, never by slotId. When the caller picks one, read it back in state CONFIRM with its slotId; book or move it only after the caller says yes.' } : { ok: true, dealerId: $json.dealerId, dealerName: $json.dealerName, slots: [], note: $json.reason === 'dealer_not_dealerbook_enabled' ? 'This dealer cannot be booked online. Offer a retailer callback (intent CALLBACK) or self-service instructions (SELF_SERVICE).' : 'No slots in this window. Try once more with a later fromDate, then hand off with NO_SLOT.' } }}", "object"),
    ], 1300, -80, keep=True)
    u = w.dt("StoreOffered", "ar_sessions", "update", 1430, -80, conditions=[("sessionId", "={{ $json.sessionId }}")],
             values={"offeredSlots": "={{ $json.offeredSlots }}", "selectedServices": "={{ $json.selectedServices }}"})
    rt = w.set_json("ReturnSlots", "={{ $('OfferSlots').first().json.result }}", 1690, -80)
    e = w.return_error("ReturnSlotsError", 1170, 120)
    w.chain(t, g, m, hr)
    w.link(hr, ws, 0); w.link(hr, cr, 1); w.link(cr, ws)
    w.chain(ws, nb)
    w.link(nb, ps, 0); w.link(nb, h, 1)
    w.link(ps, h, 0); w.link(ps, e, 1)
    w.link(h, s, 0); w.link(h, e, 1)
    w.chain(s, o, u, rt)
    return w

def build_ar13():
    w = WF("ar-13-book-appointment", "AR-13 Tool: Book appointment", "Agent tool. Writes the chosen timeslot and fulfilment to the DealerBook reservation (PATCH), submits the booking (POST bookings) and stores the DealerBook booking id and the six-letter reference code on the session. Inputs: sessionId (plumbed), slotId, mobility, notes. Returns {ok, confirmationCode (the reference code), bookingId, when, dealerName} or {ok:false, error}. Idempotent per session and slot.")
    w.sticky("## AR-13 BookAppointment\nBookingGate is the only place a booking can start: the slot must be one offered in this call, read back in CONFIRM, and confirmed in a later turn. Then the API V2 order: PATCH the timeslot (and the fulfilment when the caller asked for mobility) onto the reservation, POST the booking with an idempotency key. The booking id goes to the session as bookingRef, the six-letter reference code as confirmationCode; the spoken confirmation is built from this result, never from the model. A booking error is a retailer callback (CAC work instruction), not a handover.", 0, -400, 820, 140)
    t = w.sub_trigger([("sessionId","string"),("slotId","string"),("mobility","string"),("notes","string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    wanted = "String($('ToolInput').first().json.slotId || '').trim()"
    offered = "JSON.parse($json.offeredSlots || '[]')"
    gate = w.switch("BookingGate", [
        ("AlreadyBooked", [w.cond("={{ $json.bookingRef || '' }}", "notEmpty")], "and"),
        ("NoReservation", [w.cond("={{ $json.reservationId || '' }}", "empty")], "and"),
        ("SlotNotOffered", [w.cond(f"={{{{ {offered}.some(o => o.slotId === {wanted}) }}}}", "false", kind="boolean")], "and"),
        ("NotConfirmed", [w.cond("={{ $json.state || '' }}", "notEquals", "CONFIRM"),
                          w.cond("={{ $json.slotId || '' }}", "notEquals", f"={{{{ {wanted} }}}}")], "or"),
    ], 520, 0, fallback="Proceed")
    rn = w.set_fields("ReplyNoReservation", [
        ("ok", False, "boolean"), ("error", "no_reservation", "string"),
        ("message", "No DealerBook reservation in hand. Call CreateReservation with the agreed dealerId, then FindSlots, and book only a slot FindSlots offered.", "string")], 780, 560)
    r0 = w.set_fields("ReplyAlreadyBooked", [
        ("ok", True, "boolean"), ("already", True, "boolean"),
        ("confirmationCode", "={{ $json.confirmationCode || '' }}", "string"), ("bookingId", "={{ $json.bookingRef }}", "string"),
        ("message", "An appointment is already booked in this call. Read the existing reference code back; do not book again.", "string")], 780, 140)
    r1 = w.set_fields("ReplySlotNotOffered", [
        ("ok", False, "boolean"), ("error", "slot_not_offered", "string"),
        ("message", "slotId is not one of the slots offered in this call. Call FindSlots and use one of its slotIds.", "string"),
        ("offeredSlotIds", f"={{{{ {offered}.map(o => o.slotId) }}}}", "array")], 780, 280)
    r2 = w.set_fields("ReplyNotConfirmed", [
        ("ok", False, "boolean"), ("error", "not_confirmed", "string"),
        ("message", "The caller has not confirmed this slot yet. Read back dealer, day and time with this slotId in the slotId field (state CONFIRM), and call BookAppointment only in the next turn after the caller says yes.", "string"),
        ("pendingSlotId", "={{ $json.slotId || '' }}", "string")], 780, 420)
    # The spoken mobility wish maps to a DealerBook fulfilment id (pl, es, de, en); nothing said means none.
    mob = "String($('ToolInput').first().json.mobility || '').toLowerCase()"
    fulfil = "(/courtesy|replacement|loan|zast[eę]pcz|ersatz|leihwagen|sustitu|cortes[ií]a/.test(m) ? 'COURTESY_CAR' : /pick|collect|odbi[oó]r|abhol|recog/.test(m) ? 'PICKUP' : /wait|czeka|warte|esper/.test(m) ? 'WAIT' : 'NONE')"
    req = w.set_fields("BookingRequest", [
        ("slot", f"={{{{ {offered}.find(o => o.slotId === {wanted}) }}}}", "object"),
        ("sessionId", "={{ $json.sessionId }}", "string"), ("correlationId", "={{ $json.correlationId }}", "string"),
        ("customerRef", "={{ $json.customerRef || '' }}", "string"), ("vehicleRef", "={{ $json.vehicleRef || '' }}", "string"),
        ("reservationId", "={{ $json.reservationId || '' }}", "string"),
        ("serviceType", "={{ $json.selectedServices || 'MAINT' }}", "string"),
        ("fulfilmentId", "={{ (m => " + fulfil + ")(" + mob + ") }}", "string"),
        ("mobility", "={{ $('ToolInput').first().json.mobility || '' }}", "string"),
        ("notes", "={{ String($('ToolInput').first().json.notes || '').slice(0, 200) }}", "string"),
        ("idempotencyKey", f"={{{{ $json.sessionId + ':' + {wanted} }}}}", "string")], 780, 0)
    R = "$('BookingRequest').first().json"
    pr = w.http_json("PatchReservation", "PATCH", MOCK + "/dealerbook/reservations", "={{ JSON.stringify({ reservationId: $json.reservationId, timeslotId: $json.slot.slotId, fulfilmentId: $json.fulfilmentId }) }}", 1040, -100)
    h = w.http_post("CallDealerBookBook", MOCK + "/dealerbook/bookings", "={{ JSON.stringify({ reservationId: " + R + ".reservationId, timeslotId: " + R + ".slot.slotId, fulfilmentId: " + R + ".fulfilmentId, dealerId: " + R + ".slot.dealerId, dealerName: " + R + ".slot.dealerName || '', customerId: " + R + ".customerRef, chassisNumber: " + R + ".vehicleRef, serviceIds: " + R + ".serviceType, notes: " + R + ".notes, idempotencyKey: " + R + ".idempotencyKey, correlationId: " + R + ".correlationId }) }}", 1170, -100)
    u = w.dt("StoreBooking", "ar_sessions", "update", 1430, -180, conditions=[("sessionId", "={{ $('BookingRequest').first().json.sessionId }}")],
             values={"bookingRef": "={{ $json.bookingRef }}", "confirmationCode": "={{ $json.confirmationCode }}", "slotId": "={{ $json.slotId }}", "state": "BOOKED", "intent": "BOOK"})
    rb = w.set_fields("ReturnBooking", [
        ("ok", True, "boolean"),
        ("confirmationCode", "={{ $('CallDealerBookBook').first().json.confirmationCode }}", "string"),
        ("bookingId", "={{ $('CallDealerBookBook').first().json.bookingRef }}", "string"),
        ("when", "={{ $('BookingRequest').first().json.slot.label }}", "string"),
        ("dealerName", "={{ $('BookingRequest').first().json.slot.dealerName || '' }}", "string"),
        ("serviceType", "={{ $('BookingRequest').first().json.serviceType }}", "string"),
        ("mobility", "={{ $('BookingRequest').first().json.fulfilmentId }}", "string"),
        ("note", "Booked. Read the six-letter booking reference back to the caller slowly, letter by letter, and tell them DealerBook sends the confirmation by SMS or email.", "string")], 1690, -180)
    # A taken slot is retried with FindSlots; any other booking error becomes a retailer callback (CAC work instruction), not a handover.
    conflict = "JSON.stringify($json.error || {}).includes('409')"
    e = w.set_fields("ReturnBookError", [
        ("ok", False, "boolean"),
        ("error", f"={{{{ {conflict} ? 'slot_taken' : 'backend_unavailable' }}}}", "string"),
        ("message", f"={{{{ {conflict} ? 'That slot was taken in the meantime. Call FindSlots again and offer new options.' : 'The booking system did not accept the booking. Tell the caller the appointment cannot be booked right now and that the retailer will call them back: set intent CALLBACK and state CALLBACK, read back the dealer and the reason, and call RequestCallback after the caller says yes. Do not hand off for this.' }}}}", "string"),
        ("detail", "={{ String(($json.error || {}).message || '').slice(0, 200) }}", "string"),
    ], 1430, 0)
    w.chain(t, g, gate)
    w.link(gate, r0, 0); w.link(gate, rn, 1); w.link(gate, r1, 2); w.link(gate, r2, 3); w.link(gate, req, 4)
    w.link(req, pr)
    w.link(pr, h, 0); w.link(pr, e, 1)
    w.link(h, u, 0); w.link(h, e, 1)
    w.link(u, rb)
    return w


ENCODE_UUI_JS = """// Hex-encodes the return UUI and builds the SIP REFER targets. What goes in and what is dropped is decided upstream (AssemblePayload, FitsBudget).
const p = $json;
const hex = Buffer.from(p.payload, 'utf8').toString('hex').toUpperCase();
const addr = String(p.returnNumber || 'sip:111102@cc.invalid').replace(/^sip:/, '');
return [{ json: { ok: true, sessionId: p.sessionId, correlationId: p.correlationId, caseId: p.caseId, intent: p.intent, handoffReason: p.handoffReason, uuiPayload: p.payload, uuiAscii: p.payload, uuiHex: hex, pd: '00',
  referTo: `<sip:${addr}?User-to-User=${hex}%3Bpd%3D00%3Bencoding%3Dhex>`, referToAscii: `<sip:${addr}?User-to-User=${encodeURIComponent(p.payload)}%3Bencoding%3Dascii>`, truncated: p.truncated || [], summary: p.summary } }];"""

def utf8_bytes(expr):
    """JS expression: UTF-8 byte length of a string expression (one byte per percent-escape)."""
    return f"encodeURIComponent({expr}).replace(/%[0-9A-F]{{2}}/g, 'x').length"

def build_ar14():
    w = WF("ar-14-build-handoff", "AR-14 Build handoff context", "Builds the SIP REFER target with the return UUI (version, correlationId, sessionId, intent, handoffReason, language, caseId when it fits) and marks the session HANDOFF. Inputs: sessionId, intent, handoffReason, summary. Returns {ok, referTo, uuiHex, uuiAscii, uuiPayload, truncated}. Deterministic; called by AR-01 and AR-02, never by the model.")
    w.sticky("## AR-14 Build handoff\nThe one place that knows the contact-center UUI format. Short codes come from ar_codes. FitsBudget: 64 bytes hex-encoded; the case ID is dropped first and logged, the correlation ID is never truncated. EncodeUui only encodes.", 0, -400, 760, 110)
    t = w.sub_trigger([("sessionId","string"),("intent","string"),("handoffReason","string"),("summary","string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    m = w.dt("LoadMarket", "ar_markets", "get", 520, 0, conditions=[("market", "={{ $json.market }}")])
    I, S, M = "$('ToolInput').first().json", "$('LoadSession').first().json", "$('LoadMarket').first().json"
    ic = w.dt("LoadIntentCode", "ar_codes", "get", 650, 0, conditions=[("kind", "intent"), ("code", f"={{{{ {I}.intent || 'UNKNOWN' }}}}")])
    rc = w.dt("LoadReasonCode", "ar_codes", "get", 780, 0, conditions=[("kind", "reason"), ("code", f"={{{{ {I}.handoffReason || 'PLATFORM_ERROR' }}}}")])
    # Positional payload: version;correlationId;sessionId;intent;reason;language, plus caseId when it fits.
    base = f"['1', {S}.correlationId || '', {S}.sessionId || '', $('LoadIntentCode').first().json.short || 'U', $json.short || 'PE', {S}.language || {M}.defaultLanguage || 'en'].join(';')"
    ap = w.set_fields("AssemblePayload", [
        ("sessionId", f"={{{{ {S}.sessionId }}}}", "string"), ("correlationId", f"={{{{ {S}.correlationId || '' }}}}", "string"), ("caseId", f"={{{{ {S}.caseId || '' }}}}", "string"),
        ("intent", f"={{{{ {I}.intent || 'UNKNOWN' }}}}", "string"), ("handoffReason", f"={{{{ {I}.handoffReason || 'PLATFORM_ERROR' }}}}", "string"),
        ("summary", f"={{{{ String({I}.summary || '').slice(0, 500) }}}}", "string"), ("returnNumber", f"={{{{ {M}.returnNumber || '' }}}}", "string"),
        ("base", f"={{{{ {base} }}}}", "string"),
        ("payload", f"={{{{ {S}.caseId ? {base} + ';' + {S}.caseId : {base} }}}}", "string"),
        ("truncated", "={{ [] }}", "array"),
    ], 910, 0)
    fb = w.if_rules("FitsBudget", [w.cond(f"={{{{ {utf8_bytes('$json.payload')} }}}}", "lte", 64, kind="number")], 1040, 0)
    dc = w.set_fields("DropCaseId", [
        ("payload", "={{ $json.base }}", "string"),
        ("truncated", f"={{{{ ['caseId'].concat({utf8_bytes('$json.base')} > 64 ? ['over_budget_correlationId_kept'] : []) }}}}", "array"),
    ], 1040, 200, keep=True)
    b = w.code("EncodeUui", ENCODE_UUI_JS, 1170, 0)
    u = w.dt("StoreHandoff", "ar_sessions", "update", 1300, 0, conditions=[("sessionId", "={{ $json.sessionId }}")],
             values={"state": "HANDOFF", "handoffReason": "={{ $json.handoffReason }}", "handoffSummary": "={{ $json.summary }}", "intent": "={{ $json.intent }}"})
    # Joint scope step 8: the case is prepopulated with the full context before the call is transferred.
    E = "$('EncodeUui').first().json"
    uc = w.exec_wf("UpdateCaseContext", "ar-15-case-system", case_inputs(op="update", correlationId=f"={{{{ {E}.correlationId }}}}", caseId=f"={{{{ {E}.caseId }}}}",
        description=f"={{{{ ['Handover to agent', 'intent=' + {E}.intent, 'reason=' + {E}.handoffReason, 'state=' + ({S}.state || ''), {S}.dealerName ? 'dealer=' + {S}.dealerName : '', {S}.appointmentLabel ? 'appointment=' + {S}.appointmentLabel : '', {S}.slotId ? 'slot=' + {S}.slotId : '', 'summary=' + ({E}.summary || '')].filter(Boolean).join('; ').slice(0, 1000) }}}}"), 1430, 0)
    r = w.set_json("ReturnHandoff", "={{ $('EncodeUui').first().json }}", 1560, 0)
    w.chain(t, g, m, ic, rc, ap, fb)
    w.link(fb, b, 0); w.link(fb, dc, 1); w.link(dc, b)
    w.chain(b, u, uc, r)
    return w

CASE_FIELDS = ["op","correlationId","caseId","subject","classification","status","solution","delegatedReason","accountName","caseOrigin","description","serviceType","delegatedTo","callbackNote",
               # CASE case prerequisites (Confluence export 2026-10-23): country, account, vehicle, dealer, classification, primary contact
               "caseCountry","accountId","assetId","vinOrFin","licensePlate","mileage","mileageUnit","assignedDealerId","type","topic","reason","primaryContact"]
# Free-text fields of the case. The CAC work instruction forbids personal data in free text: e-mail addresses, phone numbers, VINs and postcodes are masked before the write.
CASE_FREE_TEXT = ["description", "solution", "delegatedReason", "callbackNote"]
# Classification placeholders: the PROD_GEN table with the values is an image in the export (question 30).
CASE_TYPE, CASE_TOPIC, CASE_REASON = "Appointment Booking", "Service appointment", "Maintenance"
SCRUB_JS = ("String(%s || '').replace(/[\\w.+-]+@[\\w-]+(\\.[\\w-]+)+/g, '[email]')"
            ".replace(/(?<![\\w-])(?:\\+|0)\\d[\\d\\s().-]{6,}\\d(?![\\w-])/g, '[phone]')"
            ".replace(/\\b[A-HJ-NPR-Z0-9]{17}\\b/g, '[vin]')"
            ".replace(/\\b[A-Z]{1,2}\\d[A-Z\\d]? ?\\d[A-Z]{2}\\b/g, '[postcode]')"
            ".replace(/(?<![\\w-])\\d{2}-\\d{3}(?![\\w-])/g, '[postcode]')")

def build_ar15():
    w = WF("ar-15-case-system", "AR-15 CASE case create, update, close or delegate", "Writes the CASE (CRM) case. Inputs: op (create|update|close|delegate), correlationId, caseId, subject, classification, status, solution, delegatedReason, accountName, caseOrigin (IN_CAR|IVR), description, serviceType (CS|DMS|RSA), delegatedTo (XCCC for a retailer callback), callbackNote, and the booking prerequisites of the CASE case data (caseCountry, accountId, assetId, vinOrFin, licensePlate, mileage, mileageUnit, assignedDealerId, type, topic, reason, primaryContact). Free text is scrubbed of personal data before the write. Returns {ok, caseId, status} or {ok:false, error}.")
    w.sticky("## AR-15 CASE case\nOne case per call, created at entry with the mandatory fields and the booking prerequisites of the CASE case data (2026-10-23): country, account, asset (CVR), VIN or FIN, plate, mileage, assigned dealer, status In Progress, type, topic, reason and the primary contact. Updated with the context before a handover, delegated to XCCC for a retailer callback, closed at the end. ScrubFreeText masks e-mail addresses, phone numbers, VINs and postcodes in the free-text fields (CAC rule: never personal data in free text). A standard REST API behind the mock.", 0, -400, 760, 140)
    t = w.sub_trigger([(f, "string") for f in CASE_FIELDS])
    sc = w.set_fields("ScrubFreeText", [(f, "={{ " + SCRUB_JS % ("$json." + f) + " }}", "string") for f in CASE_FREE_TEXT], 260, 0, keep=True)
    h = w.http_post("CallCaseSystem", MOCK + "/casesys/case", "={{ JSON.stringify($json) }}", 520, 0)
    s = w.set_fields("ShapeCase", [("ok", True, "boolean"), ("caseId", "={{ $json.caseId || '' }}", "string"), ("status", "={{ $json.status || '' }}", "string")], 780, -80)
    e = w.return_error("ReturnCaseError", 780, 120)
    w.chain(t, sc, h)
    w.link(h, s, 0)
    w.link(h, e, 1)
    return w


def case_inputs(**kw):
    """Execute Workflow inputs for AR-15 with every field present (empty string when not given)."""
    return {f: kw.get(f, "") for f in CASE_FIELDS}

def build_ar16():
    w = WF("ar-16-send-confirmation", "AR-16 Send booking confirmation", "Sends the SMS or email confirmation through the communication service. Inputs: channel, customerRef, language, text, correlationId. Returns {ok, messageId} or {ok:false, error}.")
    w.sticky("## AR-16 Send confirmation\nThe service resolves the destination from the CRM reference. n8n holds no phone number or e-mail address.", 0, -400, 700, 100)
    t = w.sub_trigger([("channel","string"),("customerRef","string"),("language","string"),("text","string"),("correlationId","string")])
    h = w.http_post("CallComms", MOCK + "/comms/send", "={{ JSON.stringify($json) }}", 260, 0)
    s = w.set_fields("ShapeComms", [("ok", "={{ !!$json.accepted }}", "boolean"), ("messageId", "={{ $json.messageId || '' }}", "string"), ("channel", "={{ $json.channel || '' }}", "string")], 520, -80)
    e = w.return_error("ReturnCommsError", 520, 120)
    w.chain(t, h)
    w.link(h, s, 0)
    w.link(h, e, 1)
    return w

# =============================================================================
# Tools added for the joint scope (2026-10-22): spoken identification, reservation, lookup, cancel, move, callback
# =============================================================================
def build_ar18():
    w = WF("ar-18-identify-caller", "AR-18 Tool: Identify caller by spoken data", "Agent tool. Matches the caller against the CASE customer account from spoken data points (registration plate plus surname or postcode; placeholder until the identification rule is agreed, question 20). Inputs: sessionId (plumbed), lastName, registrationPlate, postcode. Stores the identified customer on the session and the account name on the case. Two failed attempts: the dialogue hands off with IDENTIFICATION_FAILED.")
    w.sticky("## AR-18 IdentifyCaller\nFor IVR calls (no VIN) and unknown VINs. The data points are a placeholder until the identification rule is agreed (question 20; no document holds it); the rule lives in the mock and this tool, nowhere else. A match also writes account, asset, VIN or FIN, plate and dealer to the case. Attempts are counted on the session; the IdentifyExhausted guard in AR-02 hands off after two.", 0, -400, 760, 110)
    t = w.sub_trigger([("sessionId","string"),("lastName","string"),("registrationPlate","string"),("postcode","string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    I, S = "$('ToolInput').first().json", "$('LoadSession').first().json"
    h = w.http_post("CallIdentify", MOCK + "/crm/customer-identify", f"={{{{ JSON.stringify({{ plate: {I}.registrationPlate || '', lastName: {I}.lastName || '', postcode: {I}.postcode || '', correlationId: {S}.correlationId }}) }}}}", 520, 0)
    f = w.if_node("Identified", "={{ $json.found === true }}", 780, 0)
    st = w.dt("StoreIdentity", "ar_sessions", "update", 1040, -120, conditions=[("sessionId", f"={{{{ {S}.sessionId }}}}")],
              values={"customerRef": "={{ $json.customerRef }}", "vehicleRef": "={{ $json.vin || '' }}", "greetingName": "={{ $json.greetingName || '' }}", "vehicleLabel": "={{ $json.vehicleLabel || '' }}",
                      "dealerId": "={{ $json.preferredDealerId || '' }}", "dealerName": "={{ $json.preferredDealerName || '' }}"})
    C = "$('CallIdentify').first().json"
    uc = w.exec_wf("UpdateCaseAccount", "ar-15-case-system", case_inputs(op="update", correlationId=f"={{{{ {S}.correlationId }}}}", caseId=f"={{{{ {S}.caseId }}}}", accountName=f"={{{{ {C}.accountName || '' }}}}", accountId=f"={{{{ {C}.customerRef || '' }}}}", assetId=f"={{{{ {C}.cvrId || '' }}}}", vinOrFin=f"={{{{ {C}.vin || {C}.fin || '' }}}}",
        licensePlate=f"={{{{ {C}.plate || '' }}}}", assignedDealerId=f"={{{{ {C}.preferredDealerId || '' }}}}", primaryContact=f"={{{{ {C}.accountName || '' }}}}"), 1300, -120)
    ok = w.set_fields("ReturnIdentified", [("ok", True, "boolean"), ("known", True, "boolean"), ("greetingName", f"={{{{ {C}.greetingName || '' }}}}", "string"), ("vehicleLabel", f"={{{{ {C}.vehicleLabel || '' }}}}", "string"),
        ("preferredDealerName", f"={{{{ {C}.preferredDealerName || '' }}}}", "string"),
        ("note", "Identified. Greet the customer by the greeting name, confirm the vehicle in one sentence, then move to CONSENT.", "string")], 1560, -120)
    ca = w.dt("CountAttempt", "ar_sessions", "update", 1040, 120, conditions=[("sessionId", f"={{{{ {S}.sessionId }}}}")], values={"identifyAttempts": f"={{{{ (Number({S}.identifyAttempts) || 0) + 1 }}}}"})
    nf = w.set_fields("ReturnNotIdentified", [("ok", False, "boolean"), ("known", False, "boolean"), ("error", "not_identified", "string"),
        ("attempts", f"={{{{ (Number({S}.identifyAttempts) || 0) + 1 }}}}", "number"),
        ("message", f"={{{{ (Number({S}.identifyAttempts) || 0) + 1 >= 2 ? 'No match after two attempts. Say you cannot verify the details and set action handoff with handoffReason IDENTIFICATION_FAILED.' : 'No customer matched. Ask the caller to repeat the registration plate and the surname once, then call IdentifyCaller again.' }}}}", "string")], 1300, 120)
    e = w.return_error("ReturnIdentifyError", 780, 240)
    w.chain(t, g, h)
    w.link(h, f, 0); w.link(h, e, 1)
    w.link(f, st, 0); w.chain(st, uc, ok)
    w.link(f, ca, 1); w.link(ca, nf)
    return w


def build_ar19():
    w = WF("ar-19-create-reservation", "AR-19 Tool: Create reservation", "Agent tool. Creates the DealerBook reservation for the session's vehicle at the agreed dealer (POST reservations), then reads the services and the fulfilments available for it (GET services, GET fulfilments), the first three calls of the API V2 lifecycle. Inputs: sessionId (plumbed), dealerId. Stores reservationId, dealer, services and fulfilments on the session.")
    w.sticky("## AR-19 CreateReservation\nDealerBook API V2 order (endpoint sheet, 2026-10-23): POST reservations, GET services, GET fulfilments; the selections follow as PATCHes in FindSlots and BookAppointment. A dealer without online booking answers with no reservation and the note to offer a retailer callback or self-service.", 0, -400, 760, 110)
    t = w.sub_trigger([("sessionId","string"),("dealerId","string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    I, S = "$('ToolInput').first().json", "$('LoadSession').first().json"
    h = w.http_post("CallDealerBookReservation", MOCK + "/dealerbook/reservations", f"={{{{ JSON.stringify({{ vin: {S}.vehicleRef || '', dealerId: {I}.dealerId || {S}.dealerId || '', market: {S}.market, customerId: {S}.customerRef || '' }}) }}}}", 520, 0)
    ok = w.if_node("ReservationOk", "={{ $json.ok === true && !!$json.reservationId }}", 780, 0)
    hs = w.http_post("CallDealerBookServices", MOCK + "/dealerbook/services", "={{ JSON.stringify({ reservationId: $json.reservationId }) }}", 1040, -100)
    hf = w.http_post("CallDealerBookFulfilments", MOCK + "/dealerbook/fulfilments", "={{ JSON.stringify({ reservationId: $('CallDealerBookReservation').first().json.reservationId }) }}", 1300, -100)
    R, SV = "$('CallDealerBookReservation').first().json", "$('CallDealerBookServices').first().json"
    s = w.set_fields("ShapeReservation", [
        ("ok", True, "boolean"), ("reservationId", f"={{{{ {R}.reservationId }}}}", "string"),
        ("dealerId", f"={{{{ {R}.dealerId || {I}.dealerId || '' }}}}", "string"), ("dealerName", f"={{{{ {R}.dealerName || {S}.dealerName || '' }}}}", "string"),
        ("services", f"={{{{ {SV}.services || [] }}}}", "array"), ("servicesJson", f"={{{{ JSON.stringify({SV}.services || []) }}}}", "string"),
        ("fulfilments", "={{ $json.fulfilments || [] }}", "array"), ("fulfilmentsJson", "={{ JSON.stringify($json.fulfilments || []) }}", "string"),
        ("note", "Reservation created. Offer the services by name; maintenance service is the usual choice. When the caller has chosen, call FindSlots with the serviceIds and the earliest date.", "string"),
        ("sessionId", f"={{{{ {S}.sessionId }}}}", "string"),
    ], 1560, -100)
    u = w.dt("StoreReservation", "ar_sessions", "update", 1820, -100, conditions=[("sessionId", "={{ $json.sessionId }}")],
             values={"reservationId": "={{ $json.reservationId }}", "services": "={{ $json.servicesJson }}", "fulfilments": "={{ $json.fulfilmentsJson }}", "dealerId": "={{ $json.dealerId }}", "dealerName": "={{ $json.dealerName }}", "selectedServices": "", "offeredSlots": "[]"})
    r = w.set_json("ReturnReservation", "={{ (s => ({ ok: s.ok, reservationId: s.reservationId, dealerName: s.dealerName, services: s.services, note: s.note }))($('ShapeReservation').first().json) }}", 2080, -100)
    nr = w.set_json("ShapeNoReservation", f"={{{{ {{ ok: false, reservationId: '', dealerId: $json.dealerId || {I}.dealerId || '', dealerName: $json.dealerName || {S}.dealerName || '', services: [], error: $json.error || 'reservation_failed', note: $json.error === 'dealer_not_dealerbook_enabled' ? 'This dealer cannot be booked online. Offer a retailer callback (intent CALLBACK) or self-service instructions (SELF_SERVICE).' : 'Unknown dealer. Call FindDealers and use one of its dealerIds.' }} }}}}", 1040, 100)
    e = w.return_error("ReturnReservationError", 780, 240)
    w.chain(t, g, h)
    w.link(h, ok, 0); w.link(h, e, 1)
    w.link(ok, hs, 0); w.link(ok, nr, 1)
    w.link(hs, hf, 0); w.link(hs, e, 1)
    w.link(hf, s, 0); w.link(hf, e, 1)
    w.chain(s, u, r)
    return w


def build_ar24():
    w = WF("ar-24-find-appointment", "AR-24 Tool: Find appointment", "Agent tool. Finds the caller's booked appointments in DealerBook by the session's customer id and chassis number (GET bookings, status booked), for a reschedule or a cancellation. Inputs: sessionId (plumbed), bookingRef (optional: the six-letter reference code the caller names, used to narrow the result). One match is stored on the session as the appointment in hand, with its offer id for the slot search.")
    w.sticky("## AR-24 FindAppointment\nLookup keys of the endpoint sheet (2026-10-23): customerId, chassisNumber, offerId, status. The session's customer and vehicle are the keys; a spoken reference code only narrows. Exactly one booked appointment becomes the appointment in hand (DealerBook booking id, offer id, dealer); several are listed for the caller to choose from.", 0, -400, 760, 110)
    t = w.sub_trigger([("sessionId","string"),("bookingRef","string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    I, S = "$('ToolInput').first().json", "$('LoadSession').first().json"
    h = w.http_post("CallDealerBookBookings", MOCK + "/dealerbook/bookings/search", f"={{{{ JSON.stringify({{ customerId: {S}.customerRef || '', chassisNumber: {S}.vehicleRef || '', status: 'booked', referenceCode: String({I}.bookingRef || '').toUpperCase().replace(/[^A-Z]/g, ''), correlationId: {S}.correlationId }}) }}}}", 520, 0)
    s = w.set_fields("ShapeAppointments", [
        ("appointments", f"={{{{ ($json.bookings || []).map(a => ({{ bookingRef: a.bookingId || a.bookingRef, referenceCode: a.referenceCode || a.confirmationCode || '', offerId: a.reservationId || '', slotId: a.slotId, startsAt: a.startsAt, dealerId: a.dealerId, dealerName: a.dealerName || '', label: DateTime.fromISO(a.startsAt, {{ zone: 'utc' }}).setLocale({S}.language || 'en').toFormat('cccc dd.MM. HH:mm') }})) }}}}", "array"),
        ("sessionId", f"={{{{ {S}.sessionId }}}}", "string"),
    ], 780, -80)
    one = w.if_rules("ExactlyOne", [w.cond("={{ $json.appointments.length }}", "equals", 1, kind="number")], 910, -80)
    st = w.dt("StoreAppointment", "ar_sessions", "update", 1040, -160, conditions=[("sessionId", "={{ $json.sessionId }}")],
              values={"appointmentRef": "={{ $json.appointments[0].bookingRef }}", "appointmentLabel": "={{ $json.appointments[0].label + ' at ' + $json.appointments[0].dealerName }}",
                      "appointmentDealerId": "={{ $json.appointments[0].dealerId }}", "appointmentDealerName": "={{ $json.appointments[0].dealerName }}",
                      "appointmentOfferId": "={{ $json.appointments[0].offerId }}", "offeredSlots": "[]"})
    r = w.set_json("ReturnAppointments", "={{ (a => ({ ok: true, count: a.length, appointments: a.map(x => ({ bookingRef: x.bookingRef, referenceCode: x.referenceCode, when: x.label, dealerName: x.dealerName })), note: a.length === 1 ? 'This is the appointment in hand. Read it back (day, time, dealer). To cancel: move to CONFIRM_CANCEL and ask for an explicit yes. To reschedule: ask for the earliest new date and call FindSlots.' : a.length ? 'Several appointments. Ask which one by day and dealer, then call FindAppointment again with its reference code as bookingRef.' : 'No booked appointment found. Offer to book a new one (intent BOOK) or hand off with SCENARIO_REQUIRES_AGENT.' }))($('ShapeAppointments').first().json.appointments) }}", 1300, -80)
    e = w.return_error("ReturnAppointmentsError", 780, 120)
    w.chain(t, g, h)
    w.link(h, s, 0); w.link(h, e, 1)
    w.chain(s, one)
    w.link(one, st, 0); w.link(st, r); w.link(one, r, 1)
    return w


def build_ar25():
    w = WF("ar-25-cancel-appointment", "AR-25 Tool: Cancel appointment", "Agent tool. Cancels the appointment in hand in DealerBook (POST bookings/{bookingId}). Inputs: sessionId (plumbed), bookingRef. CancelGate: the appointment must be the one FindAppointment stored, read back in state CONFIRM_CANCEL, and confirmed in a later turn. Returns {ok, bookingRef} or {ok:false, error}.")
    w.sticky("## AR-25 CancelAppointment\nSame two-turn rule as a booking: read-back in CONFIRM_CANCEL, cancellation only after the caller's yes in the next turn. The call is a POST on the DealerBook booking id (endpoint sheet, 2026-10-23; host api-dev there, open). DealerBook confirms the cancellation to the customer.", 0, -400, 760, 110)
    t = w.sub_trigger([("sessionId","string"),("bookingRef","string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    wanted = "String($('ToolInput').first().json.bookingRef || '').trim().toUpperCase()"
    gate = w.switch("CancelGate", [
        ("AlreadyCancelled", [w.cond("={{ $json.cancelledAt || '' }}", "notEmpty")], "and"),
        ("NotInHand", [w.cond("={{ $json.appointmentRef || '' }}", "empty")], "and"),
        ("WrongAppointment", [w.cond(f"={{{{ {wanted} && {wanted} !== String($json.appointmentRef).toUpperCase() }}}}", "true", kind="boolean")], "and"),
        ("NotConfirmed", [w.cond("={{ $json.state || '' }}", "notEquals", "CONFIRM_CANCEL")], "and"),
    ], 520, 0, fallback="Proceed")
    r0 = w.set_fields("ReplyAlreadyCancelled", [("ok", True, "boolean"), ("already", True, "boolean"), ("bookingRef", "={{ $json.appointmentRef }}", "string"), ("message", "This appointment is already cancelled in this call. Do not cancel again; tell the caller it is done.", "string")], 780, 140)
    r1 = w.set_fields("ReplyNotInHand", [("ok", False, "boolean"), ("error", "no_appointment", "string"), ("message", "No appointment in hand. Call FindAppointment first.", "string")], 780, 280)
    r2 = w.set_fields("ReplyWrongAppointment", [("ok", False, "boolean"), ("error", "wrong_appointment", "string"), ("message", "={{ 'bookingRef does not match the appointment in hand (' + $json.appointmentRef + '). Use that reference or call FindAppointment again.' }}", "string")], 780, 420)
    r3 = w.set_fields("ReplyNotConfirmed", [("ok", False, "boolean"), ("error", "not_confirmed", "string"), ("message", "={{ 'The caller has not confirmed the cancellation yet. Read back the appointment (' + $json.appointmentLabel + '), set state CONFIRM_CANCEL, and call CancelAppointment only in the next turn after the caller says yes.' }}", "string")], 780, 560)
    h = w.http_post("CallDealerBookCancel", MOCK + "/dealerbook/bookings/cancel", "={{ JSON.stringify({ bookingId: $json.appointmentRef, correlationId: $json.correlationId }) }}", 780, 0)
    u = w.dt("StoreCancelled", "ar_sessions", "update", 1040, -100, conditions=[("sessionId", "={{ $('LoadSession').first().json.sessionId }}")],
             values={"cancelledAt": "={{ $now.toUTC().toISO() }}", "state": "CANCELLED", "intent": "CANCEL"})
    rb = w.set_fields("ReturnCancelled", [("ok", True, "boolean"), ("bookingRef", "={{ $('LoadSession').first().json.appointmentRef }}", "string"),
        ("note", "Cancelled. Tell the caller the appointment is cancelled and that DealerBook sends a confirmation by SMS or email; ask if there is anything else.", "string")], 1300, -100)
    e = w.return_error("ReturnCancelError", 1040, 100)
    w.chain(t, g, gate)
    w.link(gate, r0, 0); w.link(gate, r1, 1); w.link(gate, r2, 2); w.link(gate, r3, 3); w.link(gate, h, 4)
    w.link(h, u, 0); w.link(h, e, 1); w.link(u, rb)
    return w


def build_ar26():
    w = WF("ar-26-move-appointment", "AR-26 Tool: Move appointment", "Agent tool. Moves the appointment in hand to one of the slots offered in this call (POST bookings/{bookingId}/reschedule). Inputs: sessionId (plumbed), slotId. MoveGate: appointment in hand, slot offered by FindSlots, read back in CONFIRM with its slotId, confirmed in a later turn. Returns {ok, when, dealerName} or {ok:false, error}.")
    w.sticky("## AR-26 MoveAppointment\nRescheduling keeps the DealerBook booking id and the reference code and moves the timeslot (endpoint sheet, 2026-10-23: timeslots for the booking's offer, then POST reschedule; host api-dev there, open). Same two-turn gate as a booking. DealerBook confirms the move to the customer.", 0, -400, 760, 110)
    t = w.sub_trigger([("sessionId","string"),("slotId","string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    wanted = "String($('ToolInput').first().json.slotId || '').trim()"
    offered = "JSON.parse($json.offeredSlots || '[]')"
    gate = w.switch("MoveGate", [
        ("AlreadyMoved", [w.cond("={{ $json.movedAt || '' }}", "notEmpty")], "and"),
        ("NotInHand", [w.cond("={{ $json.appointmentRef || '' }}", "empty")], "and"),
        ("SlotNotOffered", [w.cond(f"={{{{ {offered}.some(o => o.slotId === {wanted}) }}}}", "false", kind="boolean")], "and"),
        ("NotConfirmed", [w.cond("={{ $json.state || '' }}", "notEquals", "CONFIRM"), w.cond("={{ $json.slotId || '' }}", "notEquals", f"={{{{ {wanted} }}}}")], "or"),
    ], 520, 0, fallback="Proceed")
    r0 = w.set_fields("ReplyAlreadyMoved", [("ok", True, "boolean"), ("already", True, "boolean"), ("message", "The appointment was already moved in this call. Read the new time back; do not move it again.", "string")], 780, 140)
    r1 = w.set_fields("ReplyNotInHand", [("ok", False, "boolean"), ("error", "no_appointment", "string"), ("message", "No appointment in hand. Call FindAppointment first.", "string")], 780, 280)
    r2 = w.set_fields("ReplySlotNotOffered", [("ok", False, "boolean"), ("error", "slot_not_offered", "string"), ("message", "slotId is not one of the slots offered in this call. Call FindSlots and use one of its slotIds.", "string"), ("offeredSlotIds", f"={{{{ {offered}.map(o => o.slotId) }}}}", "array")], 780, 420)
    r3 = w.set_fields("ReplyNotConfirmed", [("ok", False, "boolean"), ("error", "not_confirmed", "string"), ("message", "The caller has not confirmed the new time yet. Read back day, time and dealer with this slotId in the slotId field (state CONFIRM), and call MoveAppointment only in the next turn after the caller says yes.", "string")], 780, 560)
    req = w.set_fields("MoveRequest", [("slot", f"={{{{ {offered}.find(o => o.slotId === {wanted}) }}}}", "object"), ("sessionId", "={{ $json.sessionId }}", "string"), ("correlationId", "={{ $json.correlationId }}", "string"), ("appointmentRef", "={{ $json.appointmentRef }}", "string")], 780, 0)
    h = w.http_post("CallDealerBookReschedule", MOCK + "/dealerbook/bookings/reschedule", "={{ JSON.stringify({ bookingId: $json.appointmentRef, timeslotId: $json.slot.slotId, dealerName: $json.slot.dealerName || '', correlationId: $json.correlationId }) }}", 1040, -100)
    u = w.dt("StoreMoved", "ar_sessions", "update", 1300, -180, conditions=[("sessionId", "={{ $('MoveRequest').first().json.sessionId }}")],
             values={"movedAt": "={{ $now.toUTC().toISO() }}", "bookingRef": "={{ $json.bookingRef }}", "confirmationCode": "={{ $json.confirmationCode || '' }}", "slotId": "={{ $json.slotId }}", "state": "MOVED", "intent": "RESCHEDULE",
                     "dealerId": "={{ $json.dealerId || '' }}", "dealerName": "={{ $json.dealerName || '' }}"})
    rb = w.set_fields("ReturnMoved", [("ok", True, "boolean"), ("bookingRef", "={{ $('CallDealerBookReschedule').first().json.bookingRef }}", "string"), ("when", "={{ $('MoveRequest').first().json.slot.label }}", "string"),
        ("dealerName", "={{ $('MoveRequest').first().json.slot.dealerName || '' }}", "string"),
        ("note", "Moved. Read the new day and time back and tell the caller DealerBook sends the confirmation by SMS or email.", "string")], 1560, -180)
    e = w.return_error("ReturnMoveError", 1300, 0)
    w.chain(t, g, gate)
    w.link(gate, r0, 0); w.link(gate, r1, 1); w.link(gate, r2, 2); w.link(gate, r3, 3); w.link(gate, req, 4)
    w.link(req, h); w.link(h, u, 0); w.link(h, e, 1); w.link(u, rb)
    return w


def build_ar27():
    w = WF("ar-27-request-callback", "AR-27 Tool: Request retailer callback", "Agent tool. Logs the retailer callback in the CASE case and delegates the case to XCCC (joint scope step 5; no DealerBook). Inputs: sessionId (plumbed), reason, preferredTime. CallbackGate: read back in state CALLBACK, logged only after the caller's yes in a later turn. Returns {ok, caseId} or {ok:false, error}.")
    w.sticky("## AR-27 RequestCallback\nThe callback is a case delegation, not a transfer: the dealer calls the customer back. Used when the caller asks for it or when the dealer cannot be booked online.", 0, -400, 760, 100)
    t = w.sub_trigger([("sessionId","string"),("reason","string"),("preferredTime","string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    I, S = "$('ToolInput').first().json", "$('LoadSession').first().json"
    gate = w.switch("CallbackGate", [
        ("AlreadyLogged", [w.cond("={{ $json.callbackAt || '' }}", "notEmpty")], "and"),
        ("NotConfirmed", [w.cond("={{ $json.state || '' }}", "notEquals", "CALLBACK")], "and"),
    ], 520, 0, fallback="Proceed")
    r0 = w.set_fields("ReplyAlreadyLogged", [("ok", True, "boolean"), ("already", True, "boolean"), ("message", "The callback is already logged in this call. Tell the caller the dealer will call; do not log it again.", "string")], 780, 140)
    r1 = w.set_fields("ReplyNotConfirmed", [("ok", False, "boolean"), ("error", "not_confirmed", "string"), ("message", "The caller has not confirmed the callback yet. Read back the dealer, the reason and the preferred time, set state CALLBACK, and call RequestCallback only in the next turn after the caller says yes.", "string")], 780, 280)
    d = w.exec_wf("DelegateCase", "ar-15-case-system", case_inputs(op="delegate", correlationId="={{ $json.correlationId }}", caseId="={{ $json.caseId }}", status="Delegated", delegatedTo="XCCC",
        delegatedReason="={{ 'Retailer callback requested by the customer via voice assistant. Dealer ' + ($json.dealerName || $json.dealerId || 'preferred') + '.' }}",
        callbackNote=f"={{{{ ['reason=' + String({I}.reason || ''), 'preferredTime=' + String({I}.preferredTime || ''), 'dealer=' + ($json.dealerName || $json.dealerId || ''), 'vehicle=' + ($json.vehicleLabel || '')].join('; ').slice(0, 500) }}}}"), 780, 0)
    dk = w.if_node("DelegateOk", "={{ $json.ok === true }}", 1040, 0)
    u = w.dt("StoreCallback", "ar_sessions", "update", 1300, -100, conditions=[("sessionId", f"={{{{ {S}.sessionId }}}}")],
             values={"callbackAt": "={{ $now.toUTC().toISO() }}", "state": "CALLBACK_LOGGED", "intent": "CALLBACK"})
    rb = w.set_fields("ReturnLogged", [("ok", True, "boolean"), ("caseId", f"={{{{ {S}.caseId }}}}", "string"),
        ("note", "Logged and delegated to the dealer. Tell the caller the dealer will call them back, confirm the preferred time, ask if there is anything else.", "string")], 1560, -100)
    e = w.set_fields("ReturnDelegateError", [("ok", False, "boolean"), ("error", "backend_unavailable", "string"), ("message", "The case system is unavailable. Apologise once and hand the caller to an agent (BACKEND_UNAVAILABLE).", "string")], 1300, 100)
    w.chain(t, g, gate)
    w.link(gate, r0, 0); w.link(gate, r1, 1); w.link(gate, d, 2)
    w.chain(d, dk)
    w.link(dk, u, 0); w.link(u, rb); w.link(dk, e, 1)
    return w

# =============================================================================
# AR-01 session start
# =============================================================================
def build_ar01():
    w = WF("ar-01-session-start", "AR-01 API: Session start", "POST /webhook/ar/v1/session/start. Parses the contact-center UUI, identifies the customer, opens the case, creates the session and returns the AI disclosure plus greeting. Idempotent on correlationId.")
    w.sticky("## AR-01 Session start\nGateway calls this once the INVITE is answered. Header auth. In-car calls carry the VIN; IVR calls carry only the phone number and an unknown caller is identified by spoken data in the dialogue. Re-delivery of a known correlationId returns the existing session without speaking again.", 0, -400, 820, 110)
    a = w.webhook("ReceiveStart", "ar/v1/session/start", 0, 0)
    # The raw UUI string wins over pre-parsed fields: version;correlationId;vin;msisdn;language
    U = "($json.body.uui || {})"; parts = "String(($json.body.uui || {}).raw || '').split(';')"
    p = w.set_fields("ParseStart", [
        ("uuiRaw", f"={{{{ String({U}.raw || '') }}}}", "string"),
        ("version", f"={{{{ String({parts}[0] || {U}.version || '').trim() }}}}", "string"),
        ("correlationId", f"={{{{ String({parts}[1] || $json.body.correlationId || '').trim() }}}}", "string"),
        ("vin", f"={{{{ String({parts}[2] || {U}.vin || '').trim().toUpperCase() }}}}", "string"),
        ("msisdn", f"={{{{ String({parts}[3] || {U}.msisdn || '').trim() }}}}", "string"),
        ("language", f"={{{{ String({parts}[4] || {U}.language || '').trim().toLowerCase().slice(0, 2) }}}}", "string"),
        ("sipCallId", "={{ String(($json.body.sip || {}).callId || '') }}", "string"),
        ("gatewayVendor", "={{ String(($json.body.gateway || {}).vendor || '') }}", "string"),
        ("startedAt", "={{ Date.now() }}", "number"),
    ], 260, 0)
    # Market: the gateway's value, else the phone prefix, else the language, else PL.
    rm = w.set_fields("ResolveMarket", [
        ("market", "={{ String($('ReceiveStart').first().json.body.market || '').toUpperCase() || ({ '+48': 'PL', '+34': 'ES', '+49': 'DE', '+44': 'GB' })[$json.msisdn.slice(0, 3)] || ({ pl: 'PL', es: 'ES', de: 'DE', en: 'GB' })[$json.language] || 'PL' }}", "string"),
        ("message", "={{ [!$json.correlationId ? 'correlationId missing' : '', $json.version !== '1' ? 'unsupported uui version: ' + ($json.version || 'none') : ''].filter(Boolean).join('; ') }}", "string"),
    ], 390, 0, keep=True)
    iv = w.if_rules("IsValid", [w.cond("={{ $json.correlationId }}", "notEmpty"), w.cond("={{ $json.version }}", "equals", "1")], 520, 0)
    r400 = w.respond("RespondInvalid", "={{ JSON.stringify({ error: 'validation_error', message: $json.message }) }}", 780, 200, code=400)
    lp, pr = w.load_prompts(780, -100)
    ex = w.dt("CheckExisting", "ar_sessions", "get", 780, -100, conditions=[("correlationId", "={{ $('ResolveMarket').first().json.correlationId }}")])
    ie = w.if_node("AlreadyStarted", "={{ $json.sessionId }}", 1040, -100, kind="string")
    ce = w.set_json("ShapeExisting", "={{ { sessionId: $json.sessionId, correlationId: $json.correlationId, caseId: $json.caseId, market: $json.market, language: $json.language, state: $json.state, customer: { known: !!$json.customerRef, greetingName: $json.greetingName, vehicle: $json.vehicleLabel }, speak: '', action: 'continue', handoff: null, listen: true, duplicate: true } }}", 1300, -260)
    rdup = w.respond("RespondExisting", "={{ JSON.stringify($json) }}", 1560, -260)
    lm = w.dt("LoadMarket", "ar_markets", "get", 1300, -40, conditions=[("market", "={{ $('ResolveMarket').first().json.market }}")])
    ic = w.exec_wf("IdentifyCustomer", "ar-10-identify-customer", {"vin": "={{ $('ParseStart').first().json.vin }}", "msisdn": "={{ $('ParseStart').first().json.msisdn }}", "correlationId": "={{ $('ParseStart').first().json.correlationId }}"}, 1560, -40)
    # Joint scope step 1: case at entry with the mandatory fields. Origin: in-car when the UUI carries a VIN, else the IVR.
    cc = w.exec_wf("CreateCase", "ar-15-case-system", case_inputs(op="create", correlationId="={{ $('ParseStart').first().json.correlationId }}", subject="Appointment request via voice assistant",
        classification="Appointment Booking", status="In Progress", accountName="={{ ($('IdentifyCustomer').first().json.known ? $('IdentifyCustomer').first().json.accountName : '') || '' }}",
        caseOrigin="={{ $('ParseStart').first().json.vin ? 'IN_CAR' : 'IVR' }}", description="={{ 'Service appointment request via voice assistant (' + ($('ParseStart').first().json.vin ? 'in-car call' : 'IVR') + ', market ' + $('ResolveMarket').first().json.market + ')' }}", serviceType="CS",
        caseCountry="={{ $('ResolveMarket').first().json.market }}", accountId="={{ ($('IdentifyCustomer').first().json.known ? $('IdentifyCustomer').first().json.customerRef : '') || '' }}", assetId="={{ ($('IdentifyCustomer').first().json.known ? $('IdentifyCustomer').first().json.cvrId : '') || '' }}",
        vinOrFin="={{ $('ParseStart').first().json.vin || ($('IdentifyCustomer').first().json.known ? $('IdentifyCustomer').first().json.vehicleRef : '') || '' }}", licensePlate="={{ ($('IdentifyCustomer').first().json.known ? $('IdentifyCustomer').first().json.plate : '') || '' }}",
        mileageUnit="={{ $('ResolveMarket').first().json.market === 'GB' ? 'mi' : 'km' }}", assignedDealerId="={{ ($('IdentifyCustomer').first().json.known ? $('IdentifyCustomer').first().json.preferredDealerId : '') || '' }}",
        type=CASE_TYPE, topic=CASE_TOPIC, reason=CASE_REASON, primaryContact="={{ ($('IdentifyCustomer').first().json.known ? $('IdentifyCustomer').first().json.accountName : '') || '' }}"), 1820, -40)
    gid = w.add("GenerateSessionId", "n8n-nodes-base.crypto", 2, {"action": "generate", "encodingType": "hex", "stringLength": 8, "dataPropertyName": "sessionId"}, 1950, -40)
    P, RM, M, C, K = "$('ParseStart').first().json", "$('ResolveMarket').first().json", "$('LoadMarket').first().json", "$('IdentifyCustomer').first().json", "$('CreateCase').first().json"
    known = f"({C}.ok && {C}.known)"
    cs = w.set_fields("ComposeSession", [
        ("sessionId", "={{ String($json.sessionId).toUpperCase() }}", "string"), ("correlationId", f"={{{{ {P}.correlationId }}}}", "string"),
        ("caseId", f"={{{{ {K}.caseId || '' }}}}", "string"), ("market", f"={{{{ {M}.market || {RM}.market }}}}", "string"),
        ("language", f"={{{{ {P}.language && String({M}.languages || '').split(',').map(x => x.trim()).includes({P}.language) ? {P}.language : ({M}.defaultLanguage || 'en') }}}}", "string"),
        ("backendDown", f"={{{{ {C}.ok === false || {K}.ok === false }}}}", "boolean"),
        ("state", f"={{{{ ({C}.ok === false || {K}.ok === false) ? 'HANDOFF' : 'IDENTIFY' }}}}", "string"), ("intent", "UNKNOWN", "string"),
        ("handoffReason", f"={{{{ ({C}.ok === false || {K}.ok === false) ? 'BACKEND_UNAVAILABLE' : '' }}}}", "string"),
        ("known", f"={{{{ !!{known} }}}}", "boolean"), ("caseOrigin", f"={{{{ {P}.vin ? 'IN_CAR' : 'IVR' }}}}", "string"), ("identifyAttempts", 0, "number"),
        ("customerRef", f"={{{{ {known} ? ({C}.customerRef || '') : '' }}}}", "string"), ("vehicleRef", f"={{{{ {P}.vin || ({known} ? ({C}.vehicleRef || '') : '') }}}}", "string"),
        ("greetingName", f"={{{{ {known} ? ({C}.greetingName || '') : '' }}}}", "string"), ("vehicleLabel", f"={{{{ {known} ? ({C}.vehicleLabel || '') : '' }}}}", "string"),
        ("dealerId", f"={{{{ {known} ? ({C}.preferredDealerId || '') : '' }}}}", "string"), ("dealerName", f"={{{{ {known} ? ({C}.preferredDealerName || '') : '' }}}}", "string"),
        ("offeredSlots", "[]", "string"), ("turns", 0, "number"), ("startedAt", f"={{{{ new Date({P}.startedAt).toISOString() }}}}", "string"),
    ], 2080, -40)
    # Disclosure (market row) plus the greeting (ar_prompts) in the session language; the handover line when a backend is down.
    greeting = (f"{prompt_text('greeting')}.replace('{{name}}', $json.greetingName ? ', ' + $json.greetingName : '')"
                f".replace('{{vehicle}}', $json.vehicleLabel ? {prompt_text('vehiclePhrase')}.replace('{{vehicle}}', $json.vehicleLabel) : '')")
    cg = w.set_fields("ComposeGreeting", [
        ("speak", f"={{{{ $json.backendDown ? ({M}.handoverLine || '') : (({M}['disclosure_' + $json.language] || {M}.disclosure_en || '') + ' ' + {greeting}).trim() }}}}", "string"),
    ], 2210, -40, keep=True)
    ins = w.dt("InsertSession", "ar_sessions", "insert", 2340, -40, always=False,
               values={c: f"={{{{ $json.{c} }}}}" for c in ["sessionId","correlationId","caseId","market","language","state","intent","customerRef","vehicleRef","greetingName","vehicleLabel","dealerId","dealerName","offeredSlots","turns","startedAt","handoffReason","caseOrigin","identifyAttempts"]})
    lg = w.exec_wf("LogStart", "ar-17-log-event", {"correlationId": "={{ $('ComposeSession').first().json.correlationId }}", "sessionId": "={{ $('ComposeSession').first().json.sessionId }}", "caseId": "={{ $('ComposeSession').first().json.caseId }}", "workflow": "AR-01", "event": "session_start", "state": "={{ $('ComposeSession').first().json.state }}", "tool": "", "latencyMs": "={{ Date.now() - new Date($('ComposeSession').first().json.startedAt).getTime() }}", "outcome": "={{ $('ComposeSession').first().json.backendDown ? 'handoff' : 'continue' }}", "errorCode": "={{ $('ComposeSession').first().json.backendDown ? 'backend_unavailable' : '' }}", "detail": "={{ 'known=' + $('ComposeSession').first().json.known + ' market=' + $('ComposeSession').first().json.market + ' lang=' + $('ComposeSession').first().json.language }}"}, 2600, -40)
    ib = w.if_node("BackendDown", "={{ $('ComposeSession').first().json.backendDown }}", 2860, -40)
    bh = w.exec_wf("BuildStartHandoff", "ar-14-build-handoff", {"sessionId": "={{ $('ComposeSession').first().json.sessionId }}", "intent": "UNKNOWN", "handoffReason": "BACKEND_UNAVAILABLE", "summary": "Backend unavailable at session start"}, 3120, -140)
    # Response contract for session/start (section 4.1 of the spec). On the handoff path $json is BuildStartHandoff's output.
    sh = w.set_json("ShapeStart", "={{ (s => ({ sessionId: s.sessionId, correlationId: s.correlationId, caseId: s.caseId, market: s.market, language: s.language, state: s.state, customer: { known: s.known, greetingName: s.greetingName, vehicle: s.vehicleLabel }, speak: s.speak, action: s.backendDown ? 'handoff' : 'continue', handoff: s.backendDown ? { intent: 'UNKNOWN', handoffReason: 'BACKEND_UNAVAILABLE', caseId: s.caseId, uuiPayload: $json.uuiPayload, uuiHex: $json.uuiHex, uuiAscii: $json.uuiAscii, referTo: $json.referTo, error: $json.ok === false ? ($json.error || 'handoff_build_failed') : undefined } : null, listen: !s.backendDown, latencyMs: Date.now() - new Date(s.startedAt).getTime() }))($('ComposeGreeting').first().json) }}", 3380, -40)
    rs = w.respond("RespondStart", "={{ JSON.stringify($json) }}", 3640, -40)
    w.chain(a, p, rm, iv)
    w.link(iv, lp, 0); w.link(iv, r400, 1)
    w.chain(pr, ex, ie)
    w.link(ie, ce, 0); w.link(ce, rdup)
    w.link(ie, lm, 1)
    w.chain(lm, ic, cc, gid, cs, cg, ins, lg, ib)
    w.link(ib, bh, 0); w.link(bh, sh)
    w.link(ib, sh, 1)
    w.link(sh, rs)
    return w

# =============================================================================
# AR-02 dialogue turn
# =============================================================================
def validate_turn_node(w):
    """Normalises the gateway's turn body and flags what is missing. IsValid branches on `valid`."""
    B = "($json.body || {})"
    ok = f"(!!{B}.sessionId && typeof {B}.transcript === 'string')"
    return w.set_fields("ValidateTurn", [
        ("valid", f"={{{{ {ok} }}}}", "boolean"), ("statusCode", f"={{{{ {ok} ? 200 : 400 }}}}", "number"),
        ("error", f"={{{{ {ok} ? '' : 'validation_error' }}}}", "string"),
        ("message", f"={{{{ [!{B}.sessionId ? 'sessionId missing' : '', typeof {B}.transcript !== 'string' ? 'transcript missing' : ''].filter(Boolean).join('; ') }}}}", "string"),
        ("sessionId", f"={{{{ String({B}.sessionId || '') }}}}", "string"), ("correlationId", f"={{{{ String({B}.correlationId || '') }}}}", "string"),
        ("transcript", f"={{{{ String({B}.transcript || '').trim() }}}}", "string"), ("language", f"={{{{ String({B}.language || '').slice(0, 2) }}}}", "string"),
        ("asrConfidence", f"={{{{ typeof {B}.asrConfidence === 'number' ? {B}.asrConfidence : 1 }}}}", "number"),
        ("turn", f"={{{{ Number({B}.turn) || 0 }}}}", "number"), ("startedAt", "={{ Date.now() }}", "number"),
    ], 260, 0)


def validate_end_node(w):
    B = "($json.body || {})"
    return w.set_fields("ValidateEnd", [
        ("valid", f"={{{{ !!{B}.sessionId }}}}", "boolean"), ("statusCode", f"={{{{ {B}.sessionId ? 200 : 400 }}}}", "number"),
        ("error", f"={{{{ {B}.sessionId ? '' : 'validation_error' }}}}", "string"), ("message", f"={{{{ {B}.sessionId ? '' : 'sessionId missing' }}}}", "string"),
        ("sessionId", f"={{{{ String({B}.sessionId || '') }}}}", "string"), ("endReason", f"={{{{ String({B}.endReason || 'completed') }}}}", "string"),
        ("durationMs", f"={{{{ Number({B}.durationMs) || 0 }}}}", "number"), ("startedAt", "={{ Date.now() }}", "number"),
    ], 260, 0)

def add_prepare_turn(w, src):
    """Facts for this turn, the deterministic yes/no gates (YesNoGate: a small model cannot loop on a confirmed question),
    then the system prompt from ar_prompts rows. Rebuilt every turn from the session row, so state lives in n8n, not in the model.
    src: the LoadMarket node ($json is the market row). Returns the PrepareTurn node."""
    S = "$('LoadSession').first().json"; R = "$('ValidateTurn').first().json"
    sess = lambda f, default="''": f"={{{{ {S}.{f} || {default} }}}}"
    tf = w.set_fields("TurnFacts", [
        ("sessionId", f"={{{{ {S}.sessionId }}}}", "string"), ("correlationId", sess("correlationId"), "string"), ("caseId", sess("caseId"), "string"),
        ("market", sess("market"), "string"), ("language", f"={{{{ {S}.language || $json.defaultLanguage || 'en' }}}}", "string"),
        ("rowState", sess("state", "'IDENTIFY'"), "string"), ("state", sess("state", "'IDENTIFY'"), "string"),
        ("gateAdvanced", False, "boolean"), ("gateDecline", "", "string"), ("gateIntent", "", "string"), ("gateServices", "", "string"),
        ("gateAction", "", "string"), ("gateFromDate", "", "string"), ("prefetchNote", "", "string"), ("intent", sess("intent", "'UNKNOWN'"), "string"),
        ("utter", f"={{{{ String({R}.transcript || '').trim().toLowerCase() }}}}", "string"),
        ("chatInput", f"={{{{ {R}.transcript || '(silence)' }}}}", "string"),
        ("customerRef", sess("customerRef"), "string"), ("greetingName", sess("greetingName"), "string"), ("vehicleLabel", sess("vehicleLabel"), "string"),
        ("dealerId", sess("dealerId"), "string"), ("dealerName", sess("dealerName"), "string"),
        ("bookingRef", sess("bookingRef"), "string"), ("confirmationCode", sess("confirmationCode"), "string"), ("pendingSlotId", sess("slotId"), "string"),
        ("offered", f"={{{{ JSON.parse({S}.offeredSlots || '[]') }}}}", "array"),
        ("caseOrigin", sess("caseOrigin", "'IN_CAR'"), "string"), ("identifyAttempts", f"={{{{ Number({S}.identifyAttempts) || 0 }}}}", "number"),
        ("reservationId", sess("reservationId"), "string"), ("services", f"={{{{ JSON.parse({S}.services || '[]') }}}}", "array"), ("selectedServices", sess("selectedServices"), "string"),
        ("appointmentRef", sess("appointmentRef"), "string"), ("appointmentLabel", sess("appointmentLabel"), "string"),
        ("cancelledAt", sess("cancelledAt"), "string"), ("movedAt", sess("movedAt"), "string"), ("callbackAt", sess("callbackAt"), "string"),
        ("marketBookable", "={{ String($json.bookable) !== 'false' }}", "boolean"), ("handoverLine", "={{ $json.handoverLine || '' }}", "string"),
        ("lowConf", f"={{{{ {R}.asrConfidence !== null && {R}.asrConfidence < 0.5 }}}}", "boolean"),
        ("turn", f"={{{{ (Number({S}.turns) || 0) + 1 }}}}", "number"), ("startedAt", f"={{{{ {R}.startedAt }}}}", "number"),
    ], 0, 0)
    st = lambda v: w.cond("={{ $json.rowState }}", "equals", v)
    yes, no = w.cond("={{ $json.utter }}", "regex", SAID_YES), w.cond("={{ $json.utter }}", "regex", SAID_NO)
    # An unknown caller (IVR entry or unknown VIN) is identified in the dialogue by IdentifyCaller, so no gate fires for them.
    gate = w.switch("YesNoGate", [
        ("IdentityDenied", [st("IDENTIFY"), no, w.cond("={{ $json.customerRef }}", "notEmpty")], "and"),
        ("IdentityConfirmed", [st("IDENTIFY"), yes, w.cond("={{ $json.customerRef }}", "notEmpty")], "and"),
        ("ConsentDeclined", [st("CONSENT"), no], "and"),
        ("ConsentGiven", [st("CONSENT"), yes], "and"),
        ("DealerAgreed", [st("DEALER"), w.cond("={{ $json.utter }}", "regex", "(" + SAID_YES + ")|" + DEALER_AGREE), w.cond("={{ $json.dealerId }}", "notEmpty")], "and"),
    ] + [("Need" + i.title().replace("_", ""), [st("NEED"), w.cond("={{ $json.utter }}", "regex", pat)], "and") for i, pat in INTENT_PATTERNS] + [
        ("ServicesChosen", [st("SERVICES"), w.cond("={{ $json.dealerId }}", "notEmpty"),
                            w.cond("={{ $json.utter }}", "regex", "(" + SAID_YES + ")|(" + "|".join(SERVICE_KEYWORDS.values()) + ")")], "and"),
        ("RescheduleDate", [st("LOOKUP"), w.cond("={{ $json.intent }}", "equals", "RESCHEDULE"), w.cond("={{ $json.appointmentRef }}", "notEmpty"),
                            w.cond("={{ $json.utter }}", "regex", "(" + SAID_YES + ")|" + DATE_WORDS)], "and"),
        ("CancelInLookup", [st("LOOKUP"), w.cond("={{ $json.intent }}", "equals", "CANCEL"), w.cond("={{ $json.appointmentRef }}", "notEmpty"),
                            w.cond("={{ $json.utter }}", "regex", "(" + SAID_YES + ")|" + INTENT_PATTERNS[0][1])], "and"),
        ("CancelConfirmed", [st("CONFIRM_CANCEL"), yes, w.cond("={{ $json.appointmentRef }}", "notEmpty")], "and"),
        ("SlotConfirmed", [st("CONFIRM"), yes, w.cond("={{ $json.pendingSlotId }}", "notEmpty")], "and"),
    ], 0, 0, fallback="NoGate")
    kw = json.dumps(SERVICE_KEYWORDS)
    chosen = f"={{{{ Object.entries({kw}).filter(([id, re]) => new RegExp(re).test($json.utter)).map(([id]) => id).join(',') || 'MAINT' }}}}"
    on = {
        "OnIdentityFailed": [("gateDecline", "IDENTIFICATION_FAILED", "string")],
        "OnIdentityConfirmed": [("state", "CONSENT", "string"), ("gateAdvanced", True, "boolean")],
        "OnConsentDeclined": [("gateDecline", "POLICY", "string")],
        "OnConsentGiven": [("state", "NEED", "string"), ("gateAdvanced", True, "boolean")],
        "OnDealerAgreed": [("state", "SERVICES", "string"), ("gateAdvanced", True, "boolean")],
        "OnNeedCancel": [("state", "LOOKUP", "string"), ("gateAdvanced", True, "boolean"), ("gateIntent", "CANCEL", "string")],
        "OnNeedReschedule": [("state", "LOOKUP", "string"), ("gateAdvanced", True, "boolean"), ("gateIntent", "RESCHEDULE", "string")],
        "OnNeedCallback": [("state", "CALLBACK", "string"), ("gateAdvanced", True, "boolean"), ("gateIntent", "CALLBACK", "string")],
        "OnNeedSelfService": [("state", "SELF_SERVICE", "string"), ("gateAdvanced", True, "boolean"), ("gateIntent", "SELF_SERVICE", "string")],
        "OnServicesChosen": [("state", "SLOT", "string"), ("gateAdvanced", True, "boolean"), ("gateServices", chosen, "string"), ("gateFromDate", FROM_DATE, "string")],
        "OnRescheduleDate": [("state", "SLOT", "string"), ("gateAdvanced", True, "boolean"), ("gateFromDate", FROM_DATE, "string")],
        "OnCancelInLookup": [("state", "CONFIRM_CANCEL", "string"), ("gateAdvanced", True, "boolean")],
        "OnCancelConfirmed": [("gateAdvanced", True, "boolean"), ("gateAction", "cancel", "string")],
        "OnSlotConfirmed": [("gateAdvanced", True, "boolean"), ("gateAction", "={{ $json.appointmentRef ? 'move' : 'book' }}", "string")],
    }
    for name, fields in on.items():
        w.set_fields(name, fields, 0, 0, keep=True)
    w.chain(src, tf, gate)
    routes =["OnIdentityFailed", "OnIdentityConfirmed", "OnConsentDeclined", "OnConsentGiven", "OnDealerAgreed",
             "OnNeedCancel", "OnNeedReschedule", "OnNeedCallback", "OnNeedSelfService", "OnServicesChosen",
             "OnRescheduleDate", "OnCancelInLookup", "OnCancelConfirmed", "OnSlotConfirmed"]
    af = w.set_fields("AssembleFacts", [
        ("languageName", lang_pick("languageName"), "string"),
        ("persona", f"={{{{ {prompt_text('persona', chr(39) + 'en' + chr(39))}.replace('{{language}}', {prompt_text('languageName')}) }}}}", "string"),
        ("goalText", "={{ (($('Prompts').first().json.prompts.find(p => p.key === 'goal_' + $json.state) || $('Prompts').first().json.prompts.find(p => p.key === 'goal_NEED') || {}).en || '') }}", "string"),
        ("rulesText", f"={{{{ {prompt_text('rules', chr(39) + 'en' + chr(39))} }}}}", "string"),
        ("gateLine", "={{ $json.gateAdvanced ? 'The caller has just ' + ($json.gateAction ? 'confirmed, and the booking system was already updated this turn (see the tool result below): tell the caller the result' : $json.rowState === 'IDENTIFY' ? 'confirmed their identity' : $json.rowState === 'CONSENT' ? 'agreed to continue with the assistant' : $json.rowState === 'DEALER' ? 'agreed to the dealer ' + $json.dealerName + ' (dealerId ' + $json.dealerId + '); the reservation is already created: offer the available services by name, maintenance service first, and ask which they need' : $json.rowState === 'SERVICES' ? 'chosen the services ' + $json.gateServices + '; the slots are already found: offer them by day and time (see Offered slots) and ask which one fits' : $json.rowState === 'LOOKUP' ? ($json.state === 'SLOT' ? 'asked for a new date; the slots are already found: offer them by day and time (see Offered slots) and ask which one fits' : 'confirmed which appointment to cancel: read it back in full (day, time, dealer) and ask for an explicit yes to cancel it; do not say it is cancelled') : $json.gateIntent === 'CANCEL' ? 'asked to cancel an existing appointment: set intent CANCEL; the appointment search is already done: read the appointment in hand back (day, time, dealer) and ask whether that is the one to cancel' : $json.gateIntent === 'RESCHEDULE' ? 'asked to move an existing appointment: set intent RESCHEDULE; the appointment search is already done: read the appointment in hand back and ask for the earliest new date' : $json.gateIntent === 'CALLBACK' ? 'asked to be called back by the dealer: set intent CALLBACK, read back the dealer and the reason, and ask when they want the call' : 'asked to book the appointment themselves: set intent SELF_SERVICE and state SELF_SERVICE') + '. Do not ask that again; follow the current goal.' : '' }}", "string"),
        ("slotLines", "={{ $json.offered.length ? $json.offered.map(o => '- slotId ' + o.slotId + ': ' + o.label + ' at ' + (o.dealerName || $json.dealerName)).join('\\n') : '(none offered yet)' }}", "string"),
        ("serviceLines", "={{ $json.services.length ? $json.services.map(o => '- serviceId ' + o.serviceId + ': ' + o.name).join('\\n') : '(no reservation yet)' }}", "string"),
        ("selfServiceText", lang_pick("selfService"), "string"),
    ], 0, 0, keep=True)
    for out, target in enumerate(routes):
        w.link(gate, target, out)
    # Prefetch: the sub-workflow a gate implies runs here, deterministically, and its result goes into the facts.
    gf = w.set_fields("GateFacts", [("prefetchNote", "", "string")], 0, 0, keep=True)
    w.link(gate, gf, len(routes))
    for name in on:
        w.link(name, gf)
    pf = w.switch("Prefetch", [
        ("Appointment", [w.cond("={{ $json.gateIntent }}", "equals", "CANCEL"), w.cond("={{ $json.gateIntent }}", "equals", "RESCHEDULE")], "or"),
        ("Reservation", [w.cond("={{ $json.gateAdvanced }}", "true", kind="boolean"), w.cond("={{ $json.rowState }}", "equals", "DEALER")], "and"),
        ("Slots", [w.cond("={{ $json.gateAdvanced }}", "true", kind="boolean"), w.cond("={{ $json.state }}", "equals", "SLOT")], "and"),
        ("Cancel", [w.cond("={{ $json.gateAction }}", "equals", "cancel")], "and"),
        ("Book", [w.cond("={{ $json.gateAction }}", "equals", "book")], "and"),
        ("Move", [w.cond("={{ $json.gateAction }}", "equals", "move")], "and"),
    ], 0, 0, fallback="NoPrefetch")
    w.link(gf, pf)
    sid = "={{ $json.sessionId }}"
    G = "$('GateFacts').first().json"
    fetch = {
        "FetchAppointment": ("ar-24-find-appointment", {"sessionId": sid, "bookingRef": ""}, "WithAppointment",
                             f"={{{{ (g => ({{ ...g, appointmentRef: $json.count === 1 ? $json.appointments[0].bookingRef : g.appointmentRef, appointmentLabel: $json.count === 1 ? $json.appointments[0].when + ' at ' + $json.appointments[0].dealerName : g.appointmentLabel, prefetchNote: 'FindAppointment was already called this turn. Result: ' + JSON.stringify($json) + ' Do not call it again this turn.' }}))({G}) }}}}"),
        "FetchReservation": ("ar-19-create-reservation", {"sessionId": sid, "dealerId": "={{ $json.dealerId }}"}, "WithReservation",
                             f"={{{{ (g => ({{ ...g, reservationId: $json.reservationId || g.reservationId, dealerName: $json.dealerName || g.dealerName, services: ($json.services && $json.services.length) ? $json.services : g.services, prefetchNote: 'CreateReservation was already called this turn. Result: ' + JSON.stringify($json) + ' Do not call it again.' }}))({G}) }}}}"),
        "FetchSlots": ("ar-12-find-slots", {"sessionId": sid, "serviceIds": "={{ $json.gateServices || $json.selectedServices || 'MAINT' }}", "fromDate": "={{ $json.gateFromDate }}"}, "WithSlots",
                       f"={{{{ (g => ({{ ...g, offered: ($json.slots || []).map(o => ({{ slotId: o.slotId, label: o.when, dealerName: $json.dealerName || g.dealerName }})), selectedServices: g.gateServices || g.selectedServices, prefetchNote: 'FindSlots was already called this turn. Result: ' + JSON.stringify($json) + ' Do not call it again; offer the slots listed under Offered slots.' }}))({G}) }}}}"),
        "FetchCancel": ("ar-25-cancel-appointment", {"sessionId": sid, "bookingRef": "={{ $json.appointmentRef }}"}, "WithOutcome", None),
        "FetchBook": ("ar-13-book-appointment", {"sessionId": sid, "slotId": "={{ $json.pendingSlotId }}", "mobility": "", "notes": ""}, "WithOutcome", None),
        "FetchMove": ("ar-26-move-appointment", {"sessionId": sid, "slotId": "={{ $json.pendingSlotId }}"}, "WithOutcome", None),
    }
    w.set_json("WithOutcome", f"={{{{ (g => ({{ ...g, prefetchNote: 'The confirmation tool was already called this turn. Result: ' + JSON.stringify($json) + ' If ok is true, tell the caller it is done and read the result back; otherwise say what the message says. Do not call any tool this turn.' }}))({G}) }}}}", 0, 0)
    for out, (name, (key, inputs, with_name, expr)) in enumerate(fetch.items()):
        w.exec_wf(name, key, inputs, 0, 0)
        if expr:
            w.set_json(with_name, expr, 0, 0)
        w.link(pf, name, out)
        w.link(name, with_name)
    for name in ("WithAppointment", "WithReservation", "WithSlots", "WithOutcome"):
        w.link(name, af)
    w.link(pf, af, len(fetch))
    cf = w.set_fields("ComposeFacts", [
        ("factsText", "={{ [ $json.gateLine, $json.prefetchNote, 'Session state: ' + $json.state + '.', 'Call origin: ' + ($json.caseOrigin === 'IVR' ? 'phone call through the IVR (no vehicle identified from the car)' : 'in-car call') + '.', "
                      "'Customer identified: ' + ($json.customerRef ? 'yes' : 'no' + ($json.identifyAttempts ? ' (failed identification attempts: ' + $json.identifyAttempts + ' of 2)' : '')) + '.', "
                      "$json.greetingName ? 'Name to address the customer: ' + $json.greetingName + '.' : '', $json.vehicleLabel ? 'Vehicle: ' + $json.vehicleLabel + '.' : '', "
                      "$json.dealerName ? 'Preferred dealer: ' + $json.dealerName + ' (dealerId ' + $json.dealerId + ').' : 'Preferred dealer: none on file.', "
                      "'Market: ' + $json.market + '. Online booking enabled in this market: ' + ($json.marketBookable ? 'yes' : 'no') + '.', "
                      "$json.reservationId ? 'Reservation in hand at ' + $json.dealerName + '. Services available:\\n' + $json.serviceLines : 'No reservation yet (CreateReservation after the dealer is agreed).', "
                      "$json.selectedServices ? 'Selected services: ' + $json.selectedServices + '.' : '', "
                      "$json.appointmentRef ? 'Existing appointment in hand: ' + $json.appointmentLabel + ' (bookingRef ' + $json.appointmentRef + ').' : '', "
                      "$json.bookingRef && !$json.movedAt ? 'Booking already made in this call: confirmation code ' + $json.confirmationCode + '.' : $json.movedAt ? 'The appointment was moved in this call.' : 'No booking made yet in this call.', "
                      "$json.cancelledAt ? 'The appointment was cancelled in this call.' : '', $json.callbackAt ? 'The retailer callback is logged; the dealer will call the customer.' : '', "
                      "'Offered slots in this call:\\n' + $json.slotLines, 'Today is ' + new Date().toISOString().slice(0, 10) + ' (UTC).' ].filter(Boolean).join('\\n') }}", "string"),
    ], 0, 0, keep=True)
    pt = w.set_fields("PrepareTurn", [
        ("sessionId", "={{ $json.sessionId }}", "string"), ("correlationId", "={{ $json.correlationId }}", "string"), ("caseId", "={{ $json.caseId }}", "string"),
        ("market", "={{ $json.market }}", "string"), ("language", "={{ $json.language }}", "string"), ("state", "={{ $json.state }}", "string"),
        ("chatInput", "={{ $json.chatInput }}", "string"),
        ("systemPrompt", "={{ $json.persona + '\\n\\nCurrent goal (state ' + $json.state + '): ' + $json.goalText + '\\n\\nFacts you may rely on:\\n' + $json.factsText + '\\n\\nRules:\\n' + $json.rulesText + ($json.lowConf ? '\\n- The speech recogniser reported low confidence for this utterance; if it is unclear, ask the caller to repeat.' : '') }}", "string"),
        ("startedAt", "={{ $json.startedAt }}", "number"), ("turn", "={{ $json.turn }}", "number"), ("lowConf", "={{ $json.lowConf }}", "boolean"),
        ("handoverLine", "={{ $json.handoverLine }}", "string"), ("offeredSlotIds", "={{ $json.offered.map(o => o.slotId) }}", "array"),
        ("pendingSlotId", "={{ $json.pendingSlotId }}", "string"), ("gateAdvanced", "={{ $json.gateAdvanced }}", "boolean"), ("gateDecline", "={{ $json.gateDecline }}", "string"),
        ("selfServiceText", "={{ $json.selfServiceText }}", "string"), ("rowState", "={{ $json.rowState }}", "string"),
    ], 0, 0)
    w.chain(af, cf, pt)
    return pt

# Phrase patterns for the guard rules. Matched against the lower-cased text with the If node's "matches regex" operator.
ASKS_FOR_HUMAN = r"(\b(agent|human|person|operator|someone real|real person)\b|konsultant|cz[łl]owiek|pracownik|doradc|persona|humano|agente|operador|mitarbeiter|mensch|berater|echte person)"
DECLINES_HUMAN = r"\b(no|nie|nein|not|kein)\b[^.]{0,12}(agent|human|person|konsultant|persona|mitarbeiter)"
CLAIMS_BOOKING = r"((is|has been|was|are|'s) (now |all )?(booked|confirmed|reserved|scheduled|moved|rescheduled)|confirmation code|zosta[łl][ao]? (zarezerwowan|potwierdzon|umówion|przeniesion|przełożon)|kod potwierdzenia|(ha sido|está|queda) (reservad|confirmad|cambiad|trasladad)|código de confirmación|(ist|wurde) (gebucht|bestätigt|reserviert|verschoben)|bestätigungscode)"
CLAIMS_CANCEL = r"((is|has been|was|are|'s) (now )?cancell?ed|zosta[łl][ao]? (anulowan|odwo[łl]an)|(ha sido|está|queda) (cancelad|anulad)|(ist|wurde) (storniert|abgesagt))"

SAID_YES = r"^(tak|zgadza|owszem|dok[łl]adnie|to ja|oczywi[śs]cie|jasne|s[íi]|claro|correcto|as[íi] es|soy yo|exacto|vale|de acuerdo|por supuesto|ja|genau|richtig|korrekt|das bin ich|stimmt|nat[üu]rlich|gerne|in ordnung|yes|yeah|yep|correct|that'?s me|that is me|sure|of course|go ahead|ok(ay)?)(?=[\s,.!?;:]|$)"
SAID_NO = r"^(nie|no|nein|not me|wrong)(?=[\s,.!?;:]|$)"

# Fields every guard-chain result carries into the response stage.
GUARD_RESULT_FIELDS = ["sessionId", "correlationId", "caseId", "language", "speak", "action", "state", "intent", "handoffReason", "summary",
                       "guards", "needsHandoff", "turn", "startedAt", "bookingRef", "confirmationCode", "pendingSlotId", "errorCode"]
# Journey outcomes a tool wrote on the session in this turn; each forces its state (OutcomeSync).
OUTCOMES = [("Booked", "={{ $json.booked && !$json.movedAt }}", "BOOKED"), ("Moved", "={{ $json.movedAt }}", "MOVED"),
            ("Cancelled", "={{ $json.cancelledAt }}", "CANCELLED"), ("CallbackLogged", "={{ $json.callbackAt }}", "CALLBACK_LOGGED")]


def add_guards(w, src):
    """The deterministic guards on the model's structured output, as named If + Edit Fields pairs. Runs after the session
    was re-read, so a booking made by a tool in this turn is visible. src: the ReloadSession node. Returns the result node."""
    P = "$('PrepareTurn').first().json"
    A = "$('DialogueAgent').first().json.output"
    ro = w.set_fields("ReadOutput", [
        ("out", f"={{{{ typeof {A} === 'string' ? {{ speak: {A} }} : ({A} || {{}}) }}}}", "object"),
        ("sessionId", f"={{{{ {P}.sessionId }}}}", "string"), ("correlationId", f"={{{{ {P}.correlationId }}}}", "string"),
        ("caseId", f"={{{{ {P}.caseId || '' }}}}", "string"), ("language", f"={{{{ {P}.language }}}}", "string"),
        ("turn", f"={{{{ {P}.turn }}}}", "number"), ("startedAt", f"={{{{ {P}.startedAt }}}}", "number"),
        ("prepState", f"={{{{ {P}.state }}}}", "string"), ("rowState", f"={{{{ {P}.rowState || {P}.state }}}}", "string"), ("gateDecline", f"={{{{ {P}.gateDecline || '' }}}}", "string"),
        ("gateAdvanced", f"={{{{ {P}.gateAdvanced === true }}}}", "boolean"), ("handoverLine", f"={{{{ {P}.handoverLine || '' }}}}", "string"),
        ("chatInput", f"={{{{ String({P}.chatInput || '').toLowerCase() }}}}", "string"),
        ("offeredSlotIds", f"={{{{ {P}.offeredSlotIds || [] }}}}", "array"), ("prevPendingSlotId", f"={{{{ {P}.pendingSlotId || '' }}}}", "string"),
        ("booked", "={{ !!$json.bookingRef }}", "boolean"), ("bookingRef", "={{ $json.bookingRef || '' }}", "string"),
        ("confirmationCode", "={{ $json.confirmationCode || '' }}", "string"), ("sessIntent", "={{ $json.intent || 'UNKNOWN' }}", "string"),
        ("known", "={{ !!$json.customerRef }}", "boolean"), ("identifyAttempts", "={{ Number($json.identifyAttempts) || 0 }}", "number"),
        ("movedAt", "={{ $json.movedAt || '' }}", "string"), ("cancelledAt", "={{ $json.cancelledAt || '' }}", "string"), ("callbackAt", "={{ $json.callbackAt || '' }}", "string"),
        ("selfServiceText", f"={{{{ {P}.selfServiceText || '' }}}}", "string"),
    ], 0, 0)
    cl = w.set_fields("ClampOutput", [
        ("speak", "={{ String($json.out.speak || '').trim() }}", "string"),
        ("action", "={{ ['continue','handoff','end'].includes($json.out.action) ? $json.out.action : 'continue' }}", "string"),
        # Only a transition the table allows; outcome states are set by OutcomeSync after a tool wrote the result.
        ("state", f"={{{{ (({json.dumps(TRANSITIONS)})[$json.prepState] || {STATES_JS}).includes($json.out.state) ? $json.out.state : $json.prepState }}}}", "string"),
        # A journey intent sticks for the call: a later OTHER or UNKNOWN from the model does not erase it.
        ("intent", f"={{{{ ({INTENTS_JS}.includes($json.out.intent) && !(['OTHER','UNKNOWN'].includes($json.out.intent) && {json.dumps(JOURNEY_INTENTS)}.includes($json.sessIntent))) ? $json.out.intent : ({json.dumps(JOURNEY_INTENTS)}.includes($json.sessIntent) ? $json.sessIntent : 'UNKNOWN') }}}}", "string"),
        ("handoffReason", f"={{{{ {REASONS_JS}.includes($json.out.handoffReason) ? $json.out.handoffReason : '' }}}}", "string"),
        ("summary", "={{ String($json.out.summary || '').slice(0, 500) }}", "string"),
        ("slotId", "={{ $json.out.slotId ? String($json.out.slotId) : '' }}", "string"),
        ("guards", f"={{{{ (() => {{ const o = $json.out.state; const t = ({json.dumps(TRANSITIONS)})[$json.prepState] || {STATES_JS}; if (!{STATES_JS}.includes(o) || t.includes(o)) return []; return ($json.gateAdvanced && o === $json.rowState) ? ['gate_advance'] : ['illegal_transition']; }})() }}}}", "array"),
    ], 0, 0, keep=True)
    w.chain(src, ro, cl)
    booked_false = w.cond("={{ $json.booked }}", "false", kind="boolean")
    srcs = w.guard_chain([(cl, 0)], [
        ("GateDecline", [w.cond("={{ $json.gateDecline }}", "notEmpty"), booked_false, w.cond("={{ $json.action }}", "notEquals", "handoff")], "and",
         "OnGateDecline", [("action", "handoff", "string"), ("handoffReason", "={{ $json.gateDecline }}", "string"), ("guards", with_guard("gate_decline"), "array")]),
        ("GateAdvance", [w.cond("={{ $json.gateAdvanced }}", "true", kind="boolean"), w.cond("={{ $json.action }}", "equals", "continue"),
                         w.cond(f"={{{{ {STATES_JS}.indexOf($json.state) < {STATES_JS}.indexOf($json.prepState) }}}}", "true", kind="boolean")], "and",
         "OnGateAdvance", [("state", "={{ $json.prepState }}", "string"), ("guards", with_guard("gate_advance"), "array")]),
        ("ScenarioDowngrade", [w.cond("={{ $json.gateAdvanced }}", "true", kind="boolean"), w.cond("={{ $json.action }}", "equals", "handoff"),
                               w.cond("={{ $json.handoffReason }}", "equals", "SCENARIO_REQUIRES_AGENT")], "and",
         "OnScenarioDowngrade", [("action", "continue", "string"), ("state", "={{ $json.prepState }}", "string"), ("handoffReason", "", "string"),
                                 ("speak", "", "string"), ("guards", with_guard("scenario_downgrade"), "array")]),
        ("CallerAsksForHuman", [w.cond("={{ $json.chatInput }}", "regex", ASKS_FOR_HUMAN), w.cond("={{ $json.chatInput }}", "notRegex", DECLINES_HUMAN),
                                w.cond("={{ $json.action }}", "notEquals", "handoff"), booked_false], "and",
         "OnCallerRequest", [("action", "handoff", "string"), ("handoffReason", "CALLER_REQUEST", "string"), ("guards", with_guard("caller_request_rule"), "array")]),
        ("IdentifyExhausted", [w.cond("={{ $json.known }}", "false", kind="boolean"), w.cond("={{ $json.identifyAttempts }}", "gte", 2, kind="number"),
                               w.cond("={{ $json.action }}", "notEquals", "handoff")], "and",
         "OnIdentifyExhausted", [("action", "handoff", "string"), ("handoffReason", "IDENTIFICATION_FAILED", "string"), ("guards", with_guard("identify_exhausted"), "array")]),
    ])
    slot_set = "$json.state === 'CONFIRM' && $json.slotId && $json.offeredSlotIds.includes($json.slotId)"
    ps = w.set_fields("PendingSlot", [
        ("pendingSlotId", f"={{{{ ({slot_set}) ? $json.slotId : (($json.state !== 'CONFIRM' && !$json.booked) ? '' : $json.prevPendingSlotId) }}}}", "string"),
        ("guards", with_guard("pending_slot_set", slot_set), "array"),
    ], 0, 0, keep=True)
    for s, out in srcs:
        w.link(s, ps, out)
    # OutcomeSync: a booking, move, cancellation or callback written by a tool in this turn forces its state.
    osw = w.switch("OutcomeSync", [(name, [w.cond(cond, "true", kind="boolean"), w.cond(f"={{{{ ['{state}','WRAPUP','HANDOFF'].includes($json.state) }}}}", "false", kind="boolean")], "and")
                                   for name, cond, state in OUTCOMES], 0, 0, fallback="Unchanged")
    w.link(ps, osw)
    srcs = []
    for i, (name, cond, state) in enumerate(OUTCOMES):
        on = w.set_fields("OnSync" + name, [("state", state, "string"), ("guards", with_guard("state_forced_" + state.lower()), "array")], 0, 0, keep=True)
        w.link(osw, on, i); srcs.append((on, 0))
    srcs.append((osw, len(OUTCOMES)))
    settled_false = w.cond("={{ $json.booked || !!$json.movedAt }}", "false", kind="boolean")
    srcs = w.guard_chain(srcs, [
        # Reading back an existing appointment ("is scheduled for Friday") in a cancellation is not a booking claim.
        ("FalseConfirmation", [w.cond("={{ $json.speak.toLowerCase() }}", "regex", CLAIMS_BOOKING), settled_false,
                               w.cond("={{ $json.intent }}", "notEquals", "CANCEL"), w.cond("={{ $json.state }}", "notEquals", "LOOKUP"),
                               w.cond("={{ $json.state }}", "notEquals", "CONFIRM_CANCEL")], "and",
         "OnFalseConfirmation", [("speak", lang_pick("notBooked"), "string"),
                                 ("state", "={{ ['BOOKED','MOVED','WRAPUP'].includes($json.state) ? 'CONFIRM' : $json.state }}", "string"),
                                 ("action", "={{ $json.action === 'end' ? 'continue' : $json.action }}", "string"),
                                 ("guards", with_guard("false_confirmation"), "array")]),
        ("FalseCancellation", [w.cond("={{ $json.speak.toLowerCase() }}", "regex", CLAIMS_CANCEL), w.cond("={{ $json.cancelledAt }}", "empty")], "and",
         "OnFalseCancellation", [("speak", lang_pick("notCancelled"), "string"),
                                 ("state", "={{ ['CANCELLED','WRAPUP'].includes($json.state) ? 'CONFIRM_CANCEL' : $json.state }}", "string"),
                                 ("action", "={{ $json.action === 'end' ? 'continue' : $json.action }}", "string"),
                                 ("guards", with_guard("false_cancellation"), "array")]),
        ("SelfServiceLine", [w.cond("={{ $json.state }}", "equals", "SELF_SERVICE"), w.cond("={{ $json.rowState }}", "notEquals", "SELF_SERVICE"),
                             w.cond("={{ $json.selfServiceText }}", "notEmpty")], "and",
         "OnSelfService", [("speak", "={{ $json.selfServiceText }}", "string"), ("intent", "SELF_SERVICE", "string"), ("action", "continue", "string"),
                           ("guards", with_guard("self_service_line"), "array")]),
        ("EmptyReply", [w.cond("={{ $json.speak }}", "empty")], "and",
         "OnEmptyReply", [("speak", lang_pick("repeat"), "string"), ("guards", with_guard("empty_reply"), "array")]),
        ("ReplyTooLong", [w.cond("={{ $json.speak.length }}", "gt", 400, kind="number")], "and",
         "OnReplyTooLong", [("speak", "={{ $json.speak.slice(0, 397) + '...' }}", "string"), ("guards", with_guard("reply_truncated"), "array")]),
        ("HandoffNormalise", [w.cond("={{ $json.action }}", "equals", "handoff")], "and",
         "OnHandoff", [("state", "HANDOFF", "string"), ("handoffReason", "={{ $json.handoffReason || 'CALLER_REQUEST' }}", "string"),
                       ("speak", "={{ $json.handoverLine || $json.speak }}", "string"),
                       ("guards", "={{ $json.guards.concat($json.handoffReason ? [] : ['reason_defaulted']).concat($json.handoverLine ? ['handover_line'] : []) }}", "array")]),
        ("ForceHandoff", [w.cond("={{ $json.state }}", "equals", "HANDOFF"), w.cond("={{ $json.action }}", "notEquals", "handoff")], "and",
         "OnForceHandoff", [("action", "handoff", "string"), ("handoffReason", "={{ $json.handoffReason || 'SCENARIO_REQUIRES_AGENT' }}", "string"),
                            ("speak", "={{ $json.handoverLine || $json.speak }}", "string"), ("guards", with_guard("action_forced_handoff"), "array")]),
        ("EndBlocked", [w.cond("={{ $json.action }}", "equals", "end"), w.cond(f"={{{{ {END_OK_JS}.includes($json.state) }}}}", "false", kind="boolean")], "and",
         "OnEndBlocked", [("action", "continue", "string"), ("guards", with_guard("end_blocked"), "array")]),
    ])
    gr = w.set_fields("GuardResult", [
        (f, {"state": "={{ $json.action === 'end' ? 'WRAPUP' : $json.state }}",
             "needsHandoff": "={{ $json.action === 'handoff' }}",
             "errorCode": "={{ $json.guards.join(',') }}"}.get(f, f"={{{{ $json.{f} }}}}"),
         {"guards": "array", "needsHandoff": "boolean", "turn": "number", "startedAt": "number"}.get(f, "string"))
        for f in GUARD_RESULT_FIELDS], 0, 0)
    for s, out in srcs:
        w.link(s, gr, out)
    return gr


def agent_error_node(w):
    """The agent threw (model down, parser gave up). Deterministic apology and a PLATFORM_ERROR handoff; no model involved."""
    P = "$('PrepareTurn').first().json"
    fixed = {"speak": lang_pick("techError", f"{P}.language"), "action": "handoff", "state": "HANDOFF", "intent": "UNKNOWN", "handoffReason": "PLATFORM_ERROR",
             "summary": "={{ 'Dialogue agent failed: ' + String(($json.error || {}).message || ($json.error || {}).description || 'unknown').slice(0, 200) }}",
             "guards": "={{ ['agent_error'] }}", "needsHandoff": True, "bookingRef": "", "confirmationCode": "", "pendingSlotId": "", "errorCode": "agent_error",
             "caseId": f"={{{{ {P}.caseId || '' }}}}"}
    return w.set_fields("HandleAgentError", [
        (f, fixed.get(f, f"={{{{ {P}.{f} }}}}"), {"guards": "array", "needsHandoff": "boolean", "turn": "number", "startedAt": "number"}.get(f, "string"))
        for f in GUARD_RESULT_FIELDS], 0, 0)


def attach_handoff_expr():
    """JSON-mode expression: the guard result that went through NeedsHandoff plus the handoff object built from BuildHandoff's output."""
    G = "$('NeedsHandoff').first().json"
    return (f"={{{{ Object.assign({{}}, {G}, {{ handoff: $json.ok "
            f"? {{ intent: {G}.intent, handoffReason: {G}.handoffReason, caseId: {G}.caseId, uuiPayload: $json.uuiPayload, uuiHex: $json.uuiHex, uuiAscii: $json.uuiAscii, referTo: $json.referTo, truncated: $json.truncated, summary: {G}.summary }} "
            f": {{ intent: {G}.intent, handoffReason: {G}.handoffReason, caseId: {G}.caseId, error: $json.error || 'handoff_build_failed' }} }}) }}}}")


OUTPUT_SCHEMA = json.dumps({
    "type": "object",
    "required": ["speak", "action", "state", "intent"],
    "properties": {
        "speak": {"type": "string", "description": "What is read aloud to the caller, in the session language, under 45 words"},
        "action": {"type": "string", "enum": ["continue", "handoff", "end"]},
        "state": {"type": "string", "enum": STATES},
        "intent": {"type": "string", "enum": INTENTS},
        "handoffReason": {"type": ["string", "null"], "enum": ["CALLER_REQUEST","SCENARIO_REQUIRES_AGENT","NOT_UNDERSTOOD","BACKEND_UNAVAILABLE","IDENTIFICATION_FAILED","NO_SLOT","POLICY","PLATFORM_ERROR", None]},
        "slotId": {"type": ["string", "null"], "description": "In state CONFIRM: the slotId of the option being read back to the caller (new booking or move). Otherwise null."},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "summary": {"type": "string", "description": "One sentence for a human agent: what the caller wants and where the conversation is"}
    }
}, indent=2)

def build_ar02():
    w = WF("ar-02-dialogue-turn", "AR-02 API: Dialogue turn", "POST /webhook/ar/v1/session/turn. One call per caller utterance. Loads the session, runs the dialogue agent with its nine tools (identify, dealers, reservation, slots, book, find, cancel, move, callback), applies deterministic guards, builds the handoff context when needed, updates the session and logs. Returns speak, action, state and handoff.")
    w.sticky("## AR-02 Dialogue turn\nThe brain. State lives in ar_sessions; the system prompt is rebuilt every turn. Guards after the model: no booking claim without a confirmation code, no slot that was not offered, no end before WRAPUP.", 0, -448, 896, 160)
    w.sticky("### Tools\nIdentifyCaller, FindDealers, CreateReservation, FindSlots, BookAppointment, FindAppointment, CancelAppointment, MoveAppointment, RequestCallback. sessionId is plumbed, never a model parameter. Handoff is not a tool: it is a deterministic consequence of action=handoff.", 1296, 560, 704, 160, color=4)
    a = w.webhook("ReceiveTurn", "ar/v1/session/turn", 0, 0)
    v = validate_turn_node(w)
    iv = w.if_node("IsValid", "={{ $json.valid }}", 520, 0)
    r400 = w.respond("RespondInvalid", "={{ JSON.stringify({ error: $json.error, message: $json.message }) }}", 780, 200, code="={{ $json.statusCode }}")
    lp, pr = w.load_prompts(780, -100)
    ls = w.dt("LoadSession", "ar_sessions", "get", 1200, -100, conditions=[("sessionId", "={{ $('ValidateTurn').first().json.sessionId }}")])
    ifound = w.if_node("SessionFound", "={{ $json.sessionId }}", 1040, -100, kind="string")
    r404 = w.respond("RespondNotFound", "={{ JSON.stringify({ error: 'not_found', message: 'unknown sessionId' }) }}", 1300, 60, code=404)
    lm = w.dt("LoadMarket", "ar_markets", "get", 1300, -180, conditions=[("market", "={{ $json.market }}")])
    pt = add_prepare_turn(w, lm)
    ag = w.add("DialogueAgent", "@n8n/n8n-nodes-langchain.agent", 3.1,
               {"promptType": "define", "text": "={{ $json.chatInput }}", "hasOutputParser": True,
                "options": {"systemMessage": "={{ $json.systemPrompt }}", "maxIterations": 10}}, 1820, -180, onError="continueErrorOutput")
    mdl = w.model("DialogueModel", 1700, 60)
    mem = w.add("TurnMemory", "@n8n/n8n-nodes-langchain.memoryPostgresChat", 1.4,
                {"sessionIdType": "customKey", "sessionKey": "={{ $('PrepareTurn').first().json.sessionId }}", "tableName": "ar_chat_memory", "contextWindowLength": 30},
                1860, 60, credentials={"postgres": CRED_PG})
    prs = w.add("TurnOutputShape", "@n8n/n8n-nodes-langchain.outputParserStructured", 1.3,
                {"schemaType": "manual", "inputSchema": OUTPUT_SCHEMA, "autoFix": True}, 2020, 60)
    fix = w.model("ParserFixerModel", 2020, 240)
    sid = "={{ $('PrepareTurn').first().json.sessionId }}"
    t1 = w.tool_wf("FindDealers", "ar-11-find-dealers",
                   "Lists dealers the caller can choose for the appointment. Use it when the caller wants a dealer other than the preferred one, or no preferred dealer is on file. Pass the city the caller names; leave city empty to list the market's dealers. Returns dealerId, name, city and whether online booking (bookable) is possible there.",
                   {"sessionId": sid, "city": "={{ $fromAI('city', 'City the caller named for the dealer, as spoken, or empty for all dealers in the market', 'string') }}"}, 1300, 380)
    t2 = w.tool_wf("FindSlots", "ar-12-find-slots",
                   "Returns up to three free appointment slots from a given date for the reservation in hand (new booking) or the existing appointment's dealer (reschedule). Call it after the caller chose the services, and again with a later date if the caller rejects all options. Offer the returned slots by day and time; keep their slotId for BookAppointment or MoveAppointment.",
                   {"sessionId": sid, "serviceIds": "={{ $fromAI('serviceIds', 'serviceIds the caller chose from CreateReservation, comma separated, for example MAINT or MAINT,TYRE; empty when rescheduling', 'string') }}",
                    "fromDate": "={{ $fromAI('fromDate', 'Earliest acceptable date as YYYY-MM-DD, or empty for the earliest available', 'string') }}"}, 1560, 380)
    t3 = w.tool_wf("BookAppointment", "ar-13-book-appointment",
                   "Submits the booking for the reservation in hand and one of the slots offered by FindSlots in this call. Call it only after the caller explicitly confirmed dealer, day and time. Returns the confirmation code to read back, or ok false with the reason (slot not offered, slot taken, backend unavailable).",
                   {"sessionId": sid, "slotId": "={{ $fromAI('slotId', 'slotId of the option the caller chose, exactly as returned by FindSlots', 'string') }}",
                    "mobility": "={{ $fromAI('mobility', 'Replacement mobility the caller asked for: none, courtesy car, pick-up, or shuttle', 'string') }}",
                    "notes": "={{ $fromAI('notes', 'Anything the dealer should know, in one sentence, or empty', 'string') }}"}, 1820, 380)
    t0 = w.tool_wf("IdentifyCaller", "ar-18-identify-caller",
                   "Identifies a caller who is not yet identified (IVR call or unknown vehicle) from the data points they give: vehicle registration plate plus surname or postcode. Call it as soon as the caller has given the plate and one more data point. Returns the customer's greeting name and vehicle, or not_identified with what to do next.",
                   {"sessionId": sid, "lastName": "={{ $fromAI('lastName', 'Surname the caller gave, or empty', 'string') }}",
                    "registrationPlate": "={{ $fromAI('registrationPlate', 'Vehicle registration plate as spoken, letters and digits, or empty', 'string') }}",
                    "postcode": "={{ $fromAI('postcode', 'Postcode the caller gave, or empty', 'string') }}"}, 1040, 380)
    t4 = w.tool_wf("CreateReservation", "ar-19-create-reservation",
                   "Creates the DealerBook reservation for the caller's vehicle at the agreed dealer and returns the services available there. Call it once the dealer is agreed, before offering services or slots. Offer the services by name and keep their serviceId for FindSlots.",
                   {"sessionId": sid, "dealerId": "={{ $fromAI('dealerId', 'dealerId of the agreed dealer, exactly as given in the facts or by FindDealers, for example PL-WAW-01', 'string') }}"}, 1300, 620)
    t5 = w.tool_wf("FindAppointment", "ar-24-find-appointment",
                   "Finds the caller's existing booked appointments for a reschedule or a cancellation. Call it as soon as the caller wants to move or cancel an appointment; pass the booking number only if the caller named one. Returns the appointment in hand with day, time and dealer.",
                   {"sessionId": sid, "bookingRef": "={{ $fromAI('bookingRef', 'Booking number the caller named, or empty', 'string') }}"}, 1560, 620)
    t6 = w.tool_wf("CancelAppointment", "ar-25-cancel-appointment",
                   "Cancels the appointment in hand. Call it only in the turn after the caller explicitly confirmed the cancellation of the appointment read back in state CONFIRM_CANCEL. Returns ok, or not_confirmed with what to do next.",
                   {"sessionId": sid, "bookingRef": "={{ $fromAI('bookingRef', 'bookingRef of the appointment in hand, exactly as returned by FindAppointment', 'string') }}"}, 1820, 620)
    t7 = w.tool_wf("MoveAppointment", "ar-26-move-appointment",
                   "Moves the appointment in hand to one of the slots offered by FindSlots in this call. Call it only in the turn after the caller explicitly confirmed the new day and time read back in state CONFIRM. Returns the new time, or not_confirmed with what to do next.",
                   {"sessionId": sid, "slotId": "={{ $fromAI('slotId', 'slotId of the new option the caller chose, exactly as returned by FindSlots', 'string') }}"}, 2080, 620)
    t8 = w.tool_wf("RequestCallback", "ar-27-request-callback",
                   "Logs a retailer callback: the dealer calls the customer back to arrange the appointment. Use it when the caller asks to be called back, or when the dealer cannot be booked online. Call it only in the turn after the caller confirmed the callback read back in state CALLBACK.",
                   {"sessionId": sid, "reason": "={{ $fromAI('reason', 'What the caller needs from the dealer, in one sentence', 'string') }}",
                    "preferredTime": "={{ $fromAI('preferredTime', 'When the caller wants to be called back, as spoken, or empty', 'string') }}"}, 2340, 620)
    for t in (t0, t1, t4, t2, t3, t5, t6, t7, t8):
        w.link(t, ag, ctype="ai_tool")
    w.link(mdl, ag, ctype="ai_languageModel")
    w.link(mem, ag, ctype="ai_memory")
    w.link(prs, ag, ctype="ai_outputParser")
    w.link(fix, prs, ctype="ai_languageModel")
    rl = w.dt("ReloadSession", "ar_sessions", "get", 2340, -260, conditions=[("sessionId", "={{ $('PrepareTurn').first().json.sessionId }}")])
    gd = add_guards(w, rl)
    he = agent_error_node(w)
    ih = w.if_node("NeedsHandoff", "={{ $json.needsHandoff }}", 2860, -260)
    bh = w.exec_wf("BuildHandoff", "ar-14-build-handoff", {"sessionId": "={{ $json.sessionId }}", "intent": "={{ $json.intent }}", "handoffReason": "={{ $json.handoffReason }}", "summary": "={{ $json.summary }}"}, 3120, -140)
    ah = w.set_json("AttachHandoff", attach_handoff_expr(), 3250, -140)
    cr = w.set_fields("ComposeResponse", [
        ("response", "={{ { sessionId: $json.sessionId, correlationId: $json.correlationId, speak: $json.speak, action: $json.action, state: $json.state, intent: $json.intent, handoff: $json.handoff || null, endReason: $json.action === 'end' ? 'completed' : null, latencyMs: Date.now() - Number($json.startedAt), turn: $json.turn, guards: $json.guards } }}", "object"),
        ("latencyMs", "={{ Date.now() - Number($json.startedAt) }}", "number"),
    ], 3380, -260, keep=True)
    us = w.dt("UpdateSession", "ar_sessions", "update", 3640, -260, conditions=[("sessionId", "={{ $json.sessionId }}")],
              values={"state": "={{ $json.state }}", "intent": "={{ $json.intent }}", "turns": "={{ $json.turn }}", "lastLatencyMs": "={{ $json.latencyMs }}", "slotId": "={{ $json.bookingRef ? undefined : ($json.pendingSlotId || '') }}"})
    lg = w.exec_wf("LogTurn", "ar-17-log-event", {"correlationId": "={{ $('ComposeResponse').first().json.correlationId }}", "sessionId": "={{ $('ComposeResponse').first().json.sessionId }}", "caseId": "={{ $('ComposeResponse').first().json.caseId }}", "workflow": "AR-02", "event": "turn", "state": "={{ $('ComposeResponse').first().json.state }}", "tool": "", "latencyMs": "={{ $('ComposeResponse').first().json.latencyMs }}", "outcome": "={{ $('ComposeResponse').first().json.action }}", "errorCode": "={{ $('ComposeResponse').first().json.errorCode || '' }}", "detail": "={{ 'turn=' + $('ComposeResponse').first().json.turn + ' intent=' + $('ComposeResponse').first().json.intent + ($('ComposeResponse').first().json.handoffReason ? ' reason=' + $('ComposeResponse').first().json.handoffReason : '') }}"}, 3900, -260)
    rs = w.respond("RespondTurn", "={{ JSON.stringify($('ComposeResponse').first().json.response) }}", 4160, -260)
    w.chain(a, v, iv)
    w.link(iv, lp, 0); w.link(iv, r400, 1)
    w.chain(pr, ls, ifound)
    w.link(ifound, lm, 0); w.link(ifound, r404, 1)
    w.link(pt, ag)
    w.link(ag, rl, 0); w.link(ag, he, 1)
    w.link(gd, ih); w.link(he, ih)
    w.link(ih, bh, 0); w.link(ih, cr, 1)
    w.chain(bh, ah, cr)
    w.chain(cr, us, lg, rs)
    return w

# =============================================================================
# AR-03 session end
# =============================================================================
def build_ar03():
    w = WF("ar-03-session-end", "AR-03 API: Session end", "POST /webhook/ar/v1/session/end. Closes or delegates the CASE case with the journey's outcome (booked, moved, cancelled, callback, self-service, handed off, closed), purges the chat memory rows for the session, finalises the session row and logs. DealerBook sends the customer confirmation. Idempotent.")
    w.sticky("## AR-03 Session end\nEvery journey ends with the case closed in CASE. The outcome comes from the session row; DealerBook confirms bookings, cancellations and moves to the customer, so nothing is sent from here. Chat memory for the session is deleted: no transcript survives the call.", 0, -400, 760, 110)
    a = w.webhook("ReceiveEnd", "ar/v1/session/end", 0, 0)
    v = validate_end_node(w)
    iv = w.if_node("IsValid", "={{ $json.valid }}", 520, 0)
    r400 = w.respond("RespondInvalid", "={{ JSON.stringify({ error: $json.error, message: $json.message }) }}", 780, 200, code="={{ $json.statusCode }}")
    lp, pr = w.load_prompts(780, -100)
    R = "$('ValidateEnd').first().json"
    ls = w.dt("LoadSession", "ar_sessions", "get", 1200, -100, conditions=[("sessionId", f"={{{{ {R}.sessionId }}}}")])
    ifound = w.if_node("SessionFound", "={{ $json.sessionId }}", 1040, -100, kind="string")
    r404 = w.respond("RespondNotFound", "={{ JSON.stringify({ error: 'not_found', message: 'unknown sessionId' }) }}", 1300, 60, code=404)
    # Case outcome, in order: a booking exists; the session ended in HANDOFF or the gateway reports a handoff end; otherwise closed.
    co = w.switch("CaseOutcome", [
        ("Booked", [w.cond("={{ $json.bookingRef || '' }}", "notEmpty"), w.cond("={{ $json.movedAt || '' }}", "empty")], "and"),
        ("Moved", [w.cond("={{ $json.movedAt || '' }}", "notEmpty")], "and"),
        ("Cancelled", [w.cond("={{ $json.cancelledAt || '' }}", "notEmpty")], "and"),
        ("CallbackLogged", [w.cond("={{ $json.callbackAt || '' }}", "notEmpty")], "and"),
        ("SelfService", [w.cond("={{ $json.intent || '' }}", "equals", "SELF_SERVICE")], "and"),
        ("HandedOff", [w.cond("={{ $json.state || '' }}", "equals", "HANDOFF")], "and"),
        ("HandoffEnd", [w.cond(f"={{{{ String({R}.endReason || '') }}}}", "startsWith", "handoff")], "and"),
    ], 1300, -180, fallback="Closed")
    ob = w.set_fields("OnBooked", [("caseStatus", "Resolved", "string"),
        ("solution", "={{ 'Appointment booked via voice assistant. Confirmation code ' + $json.confirmationCode + '. Dealer ' + ($json.dealerName || $json.dealerId) + '. Slot ' + $json.slotId + '. Services ' + ($json.selectedServices || 'MAINT') + '. Confirmation sent by DealerBook.' }}", "string"),
        ("delegatedReason", "", "string")], 1300, 0, keep=True)
    om = w.set_fields("OnMoved", [("caseStatus", "Resolved", "string"),
        ("solution", "={{ 'Appointment ' + $json.bookingRef + ' moved via voice assistant to slot ' + $json.slotId + ' at ' + ($json.dealerName || $json.dealerId) + '. Confirmation sent by DealerBook.' }}", "string"),
        ("delegatedReason", "", "string")], 1300, 140, keep=True)
    oca = w.set_fields("OnCancelled", [("caseStatus", "Resolved", "string"),
        ("solution", "={{ 'Appointment ' + $json.appointmentRef + ' cancelled via voice assistant. Confirmation sent by DealerBook.' }}", "string"),
        ("delegatedReason", "", "string")], 1300, 280, keep=True)
    ocb = w.set_fields("OnCallbackLogged", [("caseStatus", "Delegated", "string"), ("solution", "", "string"),
        ("delegatedReason", "={{ 'Retailer callback requested; delegated to XCCC for ' + ($json.dealerName || $json.dealerId || 'the preferred dealer') + '.' }}", "string")], 1300, 420, keep=True)
    oss = w.set_fields("OnSelfService", [("caseStatus", "Resolved", "string"), ("solution", "Assisted self-service: the customer was given the instructions to book in the app or on the website. Nothing sent.", "string"),
        ("delegatedReason", "", "string")], 1300, 560, keep=True)
    oh = w.set_fields("OnHandedOff", [("caseStatus", "Delegated", "string"), ("solution", "", "string"),
        ("delegatedReason", "={{ ('Handed to agent: ' + ($json.handoffReason || 'unknown') + '. ' + ($json.handoffSummary || '')).trim() }}", "string")], 1300, 700, keep=True)
    oc = w.set_fields("OnClosed", [("caseStatus", "Closed", "string"),
        ("solution", f"={{{{ 'Call ended without an outcome (' + {R}.endReason + ') in state ' + ($json.state || '') + '.' }}}}", "string"),
        ("delegatedReason", "", "string")], 1300, 840, keep=True)
    d = w.set_fields("DecideOutcome", [
        ("sessionId", "={{ $json.sessionId }}", "string"), ("correlationId", "={{ $json.correlationId || '' }}", "string"), ("caseId", "={{ $json.caseId || '' }}", "string"),
        ("caseStatus", "={{ $json.caseStatus }}", "string"), ("solution", "={{ $json.solution }}", "string"), ("delegatedReason", "={{ $json.delegatedReason }}", "string"),
        ("booked", "={{ !!$json.bookingRef }}", "boolean"),
        ("language", "={{ $json.language || 'en' }}", "string"),
        ("endReason", f"={{{{ {R}.endReason }}}}", "string"), ("state", "={{ $json.state || '' }}", "string"), ("durationMs", f"={{{{ {R}.durationMs }}}}", "number"),
    ], 1500, -180)
    outs = [ob, om, oca, ocb, oss, oh, oh, oc]
    for i, n in enumerate(outs):
        w.link(co, n, i)
    for n in (ob, om, oca, ocb, oss, oh, oc):
        w.link(n, d)
    cc = w.exec_wf("CloseCase", "ar-15-case-system", case_inputs(op="close", correlationId="={{ $json.correlationId }}", caseId="={{ $json.caseId }}", status="={{ $json.caseStatus }}", solution="={{ $json.solution }}", delegatedReason="={{ $json.delegatedReason }}"), 1560, -180)
    pm = w.add("PurgeMemory", "n8n-nodes-base.postgres", 2.7,
               {"operation": "executeQuery", "query": "DELETE FROM ar_chat_memory WHERE session_id = $1", "options": {"queryReplacement": "={{ $('DecideOutcome').first().json.sessionId }}"}},
               2340, -180, credentials={"postgres": CRED_PG}, onError="continueRegularOutput", alwaysOutputData=True)
    fs = w.dt("FinalizeSession", "ar_sessions", "update", 2600, -180, conditions=[("sessionId", "={{ $('DecideOutcome').first().json.sessionId }}")],
              values={"endedAt": "={{ $now.toUTC().toISO() }}", "endReason": "={{ $('DecideOutcome').first().json.endReason }}", "greetingName": "", "offeredSlots": "[]", "services": "[]"})
    lg = w.exec_wf("LogEnd", "ar-17-log-event", {"correlationId": "={{ $('DecideOutcome').first().json.correlationId }}", "sessionId": "={{ $('DecideOutcome').first().json.sessionId }}", "caseId": "={{ $('DecideOutcome').first().json.caseId }}", "workflow": "AR-03", "event": "session_end", "state": "={{ $('DecideOutcome').first().json.state }}", "tool": "", "latencyMs": "={{ Date.now() - $('ValidateEnd').first().json.startedAt }}", "outcome": "={{ $('DecideOutcome').first().json.caseStatus }}", "errorCode": "={{ $('CloseCase').first().json.ok === false ? 'case_update_failed' : '' }}", "detail": "={{ 'endReason=' + $('DecideOutcome').first().json.endReason + ' booked=' + $('DecideOutcome').first().json.booked + ' memoryPurged=' + ($('PurgeMemory').first().json.error ? 'no' : 'yes') }}"}, 2860, -180)
    rs = w.respond("RespondEnd", "={{ JSON.stringify({ ok: true, sessionId: $('DecideOutcome').first().json.sessionId, caseId: $('DecideOutcome').first().json.caseId, caseStatus: $('DecideOutcome').first().json.caseStatus }) }}", 3120, -180)
    w.chain(a, v, iv)
    w.link(iv, lp, 0); w.link(iv, r400, 1)
    w.chain(pr, ls, ifound)
    w.link(ifound, co, 0); w.link(ifound, r404, 1)
    w.chain(d, cc, pm, fs, lg, rs)
    return w

# =============================================================================
# AR-04 transfer result
# =============================================================================
def build_ar04():
    w = WF("ar-04-transfer-result", "AR-04 API: Transfer result", "POST /webhook/ar/v1/session/transfer-result. Records the REFER NOTIFY outcome and tells the gateway what to do: release, speak and retry once, or speak and end.")
    w.sticky("## AR-04 Transfer result\nREFER acceptance is not completion. The outcome is stored on the session and the retry policy is one attempt: TransferOutcome routes to release, retry once, or end.", 0, -400, 700, 100)
    a = w.webhook("ReceiveTransferResult", "ar/v1/session/transfer-result", 0, 0)
    lp, pr = w.load_prompts(260, 0)
    B = "$('ReceiveTransferResult').first().json.body"
    ls = w.dt("LoadSession", "ar_sessions", "get", 680, 0, conditions=[("sessionId", f"={{{{ {B}.sessionId }}}}")])
    ifound = w.if_node("SessionFound", "={{ $json.sessionId }}", 520, 0, kind="string")
    r404 = w.respond("RespondNotFound", "={{ JSON.stringify({ error: 'not_found', message: 'unknown sessionId' }) }}", 780, 160, code=404)
    # One retry, never a loop (contact-center requirement): NOTIFY 2xx releases; a first failure retries once; a second failure ends the call.
    to = w.switch("TransferOutcome", [
        ("Released", [w.cond(f"={{{{ {B}.referAccepted !== false }}}}", "true", kind="boolean"),
                      w.cond(f"={{{{ String({B}.notifyStatus || '') }}}}", "regex", r"^2\d\d")], "and"),
        ("RetryOnce", [w.cond("={{ $json.handoffResult || '' }}", "notStartsWith", "failed")], "and"),
    ], 780, -80, fallback="Final")
    status = f"({B}.notifyStatus || 'unknown')"
    orl = w.set_fields("OnReleased", [("action", "release", "string"), ("speak", "", "string"),
                                       ("handoffResult", f"={{{{ 'completed:' + ({B}.notifyStatus || '') }}}}", "string")], 780, 60, keep=True)
    ort = w.set_fields("OnRetryOnce", [("action", "speak_and_retry_once", "string"), ("speak", lang_pick("retryTransfer"), "string"),
                                        ("handoffResult", f"={{{{ 'failed:' + {status} }}}}", "string")], 780, 200, keep=True)
    ofn = w.set_fields("OnFinal", [("action", "speak_and_end", "string"), ("speak", lang_pick("transferFailed"), "string"),
                                    ("handoffResult", f"={{{{ 'failed_final:' + {status} }}}}", "string")], 780, 340, keep=True)
    d = w.set_fields("DecideTransferAction", [
        ("sessionId", "={{ $json.sessionId }}", "string"), ("correlationId", "={{ $json.correlationId || '' }}", "string"), ("caseId", "={{ $json.caseId || '' }}", "string"),
        ("action", "={{ $json.action }}", "string"), ("speak", "={{ $json.speak }}", "string"), ("handoffResult", "={{ $json.handoffResult }}", "string"),
        ("notifyStatus", f"={{{{ String({B}.notifyStatus || '') }}}}", "string"), ("state", "={{ $json.state || '' }}", "string"),
    ], 900, -80)
    w.link(to, orl, 0); w.link(to, ort, 1); w.link(to, ofn, 2)
    for n in (orl, ort, ofn):
        w.link(n, d)
    u = w.dt("UpdateTransfer", "ar_sessions", "update", 1040, -80, conditions=[("sessionId", "={{ $json.sessionId }}")], values={"handoffResult": "={{ $json.handoffResult }}"})
    lg = w.exec_wf("LogTransfer", "ar-17-log-event", {"correlationId": "={{ $('DecideTransferAction').first().json.correlationId }}", "sessionId": "={{ $('DecideTransferAction').first().json.sessionId }}", "caseId": "={{ $('DecideTransferAction').first().json.caseId }}", "workflow": "AR-04", "event": "transfer_result", "state": "={{ $('DecideTransferAction').first().json.state }}", "tool": "", "latencyMs": 0, "outcome": "={{ $('DecideTransferAction').first().json.action }}", "errorCode": "={{ $('DecideTransferAction').first().json.action === 'release' ? '' : 'refer_failed' }}", "detail": "={{ 'notify=' + $('DecideTransferAction').first().json.notifyStatus }}"}, 1300, -80)
    rs = w.respond("RespondTransfer", "={{ JSON.stringify({ action: $('DecideTransferAction').first().json.action, speak: $('DecideTransferAction').first().json.speak }) }}", 1560, -80)
    w.chain(a, lp)
    w.chain(pr, ls, ifound)
    w.link(ifound, to, 0); w.link(ifound, r404, 1)
    w.chain(d, u, lg, rs)
    return w

# =============================================================================
# AR-05 health and handoff lookup
# =============================================================================
def build_ar05():
    w = WF("ar-05-health-and-handoff-lookup", "AR-05 API: Health and handoff context lookup", "GET /webhook/ar/v1/health for probes. GET /webhook/ar/v1/handoff?correlationId=... returns the handoff context (case, intent, reason, summary) for the agent desktop. No transcript.")
    w.sticky("## AR-05 Health and handoff lookup\nHealth is unauthenticated and cheap. The lookup is header-authenticated and returns only what the agent desktop needs.", 0, -400, 700, 100)
    h = w.webhook("HealthCheck", "ar/v1/health", 0, 0, method="GET", auth=False)
    rh = w.respond("RespondHealth", "={{ JSON.stringify({ ok: true, service: 'voice-booking', version: '0.1', at: $now.toUTC().toISO() }) }}", 260, 0)
    w.chain(h, rh)
    l = w.webhook("LookupHandoff", "ar/v1/handoff", 0, 200, method="GET")
    g = w.dt("LoadByCorrelation", "ar_sessions", "get", 260, 200, conditions=[("correlationId", "={{ $json.query.correlationId }}")])
    s = w.set_json("ShapeHandoffContext", "={{ $json.sessionId ? { found: true, statusCode: 200, correlationId: $json.correlationId, sessionId: $json.sessionId, caseId: $json.caseId, state: $json.state, intent: $json.intent, handoffReason: $json.handoffReason || null, handoffResult: $json.handoffResult || null, summary: $json.handoffSummary || '', dealer: $json.dealerName || null, slotId: $json.slotId || null, confirmationCode: $json.confirmationCode || null, appointmentRef: $json.appointmentRef || null, movedAt: $json.movedAt || null, cancelledAt: $json.cancelledAt || null, callbackAt: $json.callbackAt || null, caseOrigin: $json.caseOrigin || null, language: $json.language, market: $json.market, startedAt: $json.startedAt, endedAt: $json.endedAt || null } : { found: false, statusCode: 404 } }}", 520, 200)
    r = w.respond("RespondHandoffContext", "={{ JSON.stringify($json) }}", 780, 200, code="={{ $json.statusCode }}")
    w.chain(l, g, s, r)
    return w

# =============================================================================
# AR-30 error workflow
# =============================================================================
def build_ar30():
    w = WF("ar-30-error-workflow", "AR-30 Error workflow", "Catch-all for any AR-* workflow failure that escapes per-node handling. Writes an error event with workflow, node and message. Assign it as the Error Workflow in each AR-* workflow's settings (UI).")
    w.sticky("## AR-30 Error workflow\nAssign in Workflow Settings > Error Workflow on every AR-* workflow. Writes to ar_events; wire a Teams or e-mail notification here on the production instance (different channel from the monitored one).", 0, -400, 760, 110)
    t = w.add("OnWorkflowError", "n8n-nodes-base.errorTrigger", 1, {}, 0, 0)
    E = "($json.execution || {})"
    s = w.set_fields("ShapeError", [
        ("at", "={{ $now.toUTC().toISO() }}", "string"), ("correlationId", "", "string"), ("sessionId", "", "string"), ("caseId", "", "string"),
        ("workflow", "={{ ($json.workflow || {}).name || '' }}", "string"), ("event", "error", "string"), ("state", "", "string"),
        ("tool", f"={{{{ {E}.lastNodeExecuted || '' }}}}", "string"), ("latencyMs", 0, "number"), ("outcome", "failed", "string"), ("errorCode", "workflow_error", "string"),
        ("detail", f"={{{{ String(({E}.error || {{}}).message || '').slice(0, 300) + ' execution=' + ({E}.id || '') }}}}", "string"),
    ], 260, 0)
    ins = w.dt("InsertErrorEvent", "ar_events", "insert", 520, 0, always=False, values={c: f"={{{{ $json.{c} }}}}" for c in TABLE_COLS["ar_events"]})
    w.chain(t, s, ins)
    return w

# =============================================================================
# AR-90 seed configuration
# =============================================================================
MARKET_ROWS = [
    {"market": "PL", "languages": "pl,en", "defaultLanguage": "pl", "bookable": True,
     "disclosure_pl": "Dzień dobry, tu asystent cyfrowy serwisu. Ta rozmowa jest prowadzona przez sztuczną inteligencję. W każdej chwili mogą Państwo poprosić o rozmowę z konsultantem.",
     "disclosure_es": "", "disclosure_en": "Hello, this is the workshop's digital assistant. This conversation is handled by an artificial intelligence. You can ask for a human colleague at any time.", "disclosure_de": "",
     "handoverLine": "Łączę Państwa z konsultantem, proszę chwilę poczekać.", "closingLine": "Dziękuję za rozmowę. Do widzenia.",
     "confirmationChannels": "sms,email", "returnNumber": "sip:111102@cc.example", "slotWindowDays": 14, "maxSlotsOffered": 3},
    {"market": "ES", "languages": "es,en", "defaultLanguage": "es", "bookable": True,
     "disclosure_pl": "", "disclosure_es": "Hola, le atiende el asistente digital del taller. Esta conversación la gestiona una inteligencia artificial. Puede pedir hablar con una persona en cualquier momento.",
     "disclosure_en": "Hello, this is the workshop's digital assistant. This conversation is handled by an artificial intelligence. You can ask for a human colleague at any time.", "disclosure_de": "",
     "handoverLine": "Le paso con un agente, un momento por favor.", "closingLine": "Gracias por su llamada. Adiós.",
     "confirmationChannels": "sms,email", "returnNumber": "sip:111102@cc.example", "slotWindowDays": 14, "maxSlotsOffered": 3},
    {"market": "DE", "languages": "de,en", "defaultLanguage": "de", "bookable": True,
     "disclosure_pl": "", "disclosure_es": "", "disclosure_en": "Hello, this is the workshop's digital assistant. This conversation is handled by an artificial intelligence. You can ask for a human colleague at any time.",
     "disclosure_de": "Guten Tag, hier ist der digitale Assistent der Werkstatt. Dieses Gespräch wird von einer künstlichen Intelligenz geführt. Sie können jederzeit einen Mitarbeiter verlangen.",
     "handoverLine": "Ich verbinde Sie mit einem Mitarbeiter, einen Moment bitte.", "closingLine": "Vielen Dank für Ihren Anruf. Auf Wiederhören.",
     "confirmationChannels": "sms,email", "returnNumber": "sip:111102@cc.example", "slotWindowDays": 14, "maxSlotsOffered": 3},
    {"market": "GB", "languages": "en", "defaultLanguage": "en", "bookable": True,
     "disclosure_pl": "", "disclosure_es": "", "disclosure_en": "Hello, this is the workshop's digital assistant. This conversation is handled by an artificial intelligence. You can ask for a human colleague at any time.", "disclosure_de": "",
     "handoverLine": "I am connecting you to a colleague, one moment please.", "closingLine": "Thank you for calling. Goodbye.",
     "confirmationChannels": "sms,email", "returnNumber": "sip:111102@cc.example", "slotWindowDays": 14, "maxSlotsOffered": 3},
]
CODE_ROWS = [
    ("intent", "BOOK", "B", "APPT_BOOK", "New service appointment"), ("intent", "RESCHEDULE", "R", "APPT_RESCHEDULE", "Move an existing appointment"),
    ("intent", "CANCEL", "C", "APPT_CANCEL", "Cancel an existing appointment"), ("intent", "CALLBACK", "K", "RETAILER_CALLBACK", "Retailer callback via the case (XCCC)"),
    ("intent", "SELF_SERVICE", "S", "SELF_SERVICE", "Assisted self-service instructions"), ("intent", "OTHER", "O", "OTHER", "Outside appointment booking"), ("intent", "UNKNOWN", "U", "unknown", "Intent not identified"),
    ("reason", "CALLER_REQUEST", "CR", "CUSTOMER_REQUEST", "Caller asked for a person"), ("reason", "SCENARIO_REQUIRES_AGENT", "SA", "PROCESS", "Non-bookable dealer, callback or out-of-scope request"),
    ("reason", "NOT_UNDERSTOOD", "NU", "LOW_CONFIDENCE", "Two consecutive turns not understood"), ("reason", "BACKEND_UNAVAILABLE", "BU", "SYSTEM_ERROR", "CRM, DealerBook or case system unavailable"),
    ("reason", "IDENTIFICATION_FAILED", "IF", "IDENTIFICATION", "Customer or vehicle could not be confirmed"), ("reason", "NO_SLOT", "NS", "NO_AVAILABILITY", "No acceptable slot found"),
    ("reason", "POLICY", "PO", "CONSENT", "Caller declined the assistant or a policy trigger fired"), ("reason", "PLATFORM_ERROR", "PE", "SYSTEM_ERROR", "Dialogue platform failure"),
]


def _prompt(key, en, pl="", es="", de="", note=""):
    return {"key": key, "pl": pl, "es": es, "de": de, "en": en, "note": note}

GOALS = {
    "IDENTIFY": "If the facts say the customer is identified: confirm in one short question that you are speaking with the customer named in the facts about the vehicle named in the facts. Never read out a VIN or a phone number. If the caller says it is not them, set action handoff with handoffReason IDENTIFICATION_FAILED. If the customer is not identified: ask for the vehicle registration plate and the surname (a postcode also works), then call IdentifyCaller with what the caller gave. Do not ask for a VIN. When confirmed or identified, move to CONSENT.",
    "CONSENT": "The AI disclosure was already spoken. Ask once whether the caller is happy to continue with the assistant. Yes: move to NEED. No: set action handoff with handoffReason POLICY.",
    "NEED": "Find out what the caller needs: a new appointment (intent BOOK, move to DEALER), moving an existing one (RESCHEDULE, move to LOOKUP and call FindAppointment), cancelling one (CANCEL, move to LOOKUP and call FindAppointment), a call back from the dealer (CALLBACK, move to CALLBACK), instructions to book themselves in the app or on the website (SELF_SERVICE, move to SELF_SERVICE), or something else (OTHER, then action handoff with SCENARIO_REQUIRES_AGENT). For BOOK also ask whether replacement mobility is needed.",
    "DEALER": "If the facts say a tool was already called this turn, use its result and do not call that tool again. Propose the preferred dealer from the facts. If the caller wants a different one, ask for the city and call FindDealers. When a dealer is agreed, call CreateReservation with its dealerId and move to SERVICES. If CreateReservation says the dealer cannot be booked online, offer a retailer callback (CALLBACK) or self-service instructions (SELF_SERVICE).",
    "SERVICES": "If the facts say a tool was already called this turn, use its result and do not call that tool again. Offer the services from the reservation by name, maintenance service first (it is the usual case). When the caller has chosen, call FindSlots with the chosen serviceIds and the earliest date the caller wants, then move to SLOT.",
    "SLOT": "If the facts say a tool was already called this turn, use its result and do not call that tool again. Offer at most three of the slots FindSlots returned, by day and time, never by slotId. If none fits, call FindSlots once more with a later fromDate; if still none, set action handoff with NO_SLOT. When the caller picks one, move to CONFIRM.",
    "CONFIRM": "If the facts say a tool was already called this turn, use its result and do not call that tool again. Read back dealer, day, time and services (for a new booking also mobility), put the slotId of that option in the slotId field, and ask for an explicit yes. The booking or the move is only possible in the turn after the read-back, once the caller has said yes: then call BookAppointment (new appointment) or MoveAppointment (existing appointment in hand) with the same slotId. If the tool answers not_confirmed, read the slot back again and ask for a yes. Never say the appointment is booked or moved before the tool returns ok. After ok, move to BOOKED or MOVED.",
    "BOOKED": "Tell the caller the confirmation code slowly, that DealerBook sends a confirmation by SMS or email, and ask if there is anything else. If nothing else, say goodbye with action end and state WRAPUP.",
    "LOOKUP": "If the facts say a tool was already called this turn, use its result and do not call that tool again. Call FindAppointment if not done yet. Read the appointment in hand back (day, time, dealer). To cancel: move to CONFIRM_CANCEL and ask for an explicit yes. To reschedule: ask for the earliest new date, call FindSlots, then move to SLOT. If several appointments were found, ask which one and call FindAppointment again with its bookingRef. If none, offer a new appointment or hand off with SCENARIO_REQUIRES_AGENT.",
    "CONFIRM_CANCEL": "If the facts say a tool was already called this turn, use its result and do not call that tool again. The appointment was read back. Only when the caller says yes in this turn, call CancelAppointment with its bookingRef. If the caller hesitates, offer to keep or move it instead. Never say it is cancelled before CancelAppointment returns ok; then move to CANCELLED.",
    "CANCELLED": "Tell the caller the appointment is cancelled and that a confirmation by SMS or email follows. Ask if there is anything else; if not, say goodbye with action end and state WRAPUP.",
    "MOVED": "Read the new day and time back, say that a confirmation by SMS or email follows, ask if there is anything else. If nothing else, say goodbye with action end and state WRAPUP.",
    "CALLBACK": "Read back the dealer that will call, the reason and the preferred time, and ask for an explicit yes. Only in the turn after the yes, call RequestCallback. Then move to CALLBACK_LOGGED.",
    "CALLBACK_LOGGED": "Tell the caller the dealer will call them back at the preferred time. Ask if there is anything else; if not, say goodbye with action end and state WRAPUP.",
    "SELF_SERVICE": "The self-service instructions are read out automatically when you enter this state; set intent SELF_SERVICE and state SELF_SERVICE with a short lead-in. Nothing is sent to the caller. Afterwards ask if there is anything else; if not, say goodbye with action end and state WRAPUP.",
    "WRAPUP": "Close politely with action end.",
    "HANDOFF": "Say a colleague will take over and set action handoff.",
}
RULES_TEXT = "\n".join([
    "- Never invent a dealer, a time slot, a confirmation code or any customer data. Dealers come from FindDealers, slots from FindSlots, confirmation codes from BookAppointment.",
    "- Never say an appointment is booked, confirmed or reserved unless BookAppointment returned a confirmation code in this call; never say it is moved unless MoveAppointment returned ok; never say it is cancelled unless CancelAppointment returned ok.",
    "- If the caller asks for a person at any point, set action handoff with handoffReason CALLER_REQUEST.",
    "- If you could not understand the caller twice in a row, set action handoff with handoffReason NOT_UNDERSTOOD.",
    "- If a tool reports it is unavailable, apologise once and set action handoff with handoffReason BACKEND_UNAVAILABLE.",
    "- Move the state forward only when its goal is met. Allowed states: " + ", ".join(STATES) + ". Allowed intents: " + ", ".join(INTENTS) + ".",
    "- Return only json matching the schema. The speak field is read aloud to the caller.",
])
# Everything the assistant says or is told, one row per key. Language columns pl, es, de, en; English is the fallback.
PROMPT_ROWS = [
    _prompt("persona", "You are the workshop service appointment assistant on a live phone call. Speak {language} only. Keep every reply under 45 words, one question at a time, no lists, no markdown; say numbers, dates and times the way a person would on the phone.", note="First paragraph of the system prompt. {language} is the session language name."),
    _prompt("rules", RULES_TEXT, note="Rules block of the system prompt, one bullet per line."),
    _prompt("languageName", "English", "Polish", "Spanish", "German", note="Language name spoken into the persona line."),
] + [_prompt("goal_" + k, v, note="Goal for state " + k) for k, v in GOALS.items()] + [
    _prompt("greeting", "Hello{name}. How can I help with your service appointment{vehicle}?",
            "Dzień dobry{name}. W czym mogę pomóc w związku z wizytą serwisową{vehicle}?", "Buenos días{name}. ¿En qué puedo ayudarle con su cita de taller{vehicle}?",
            "Guten Tag{name}. Wie kann ich Ihnen bei Ihrem Werkstatttermin{vehicle} helfen?", note="Spoken after the disclosure. {name} becomes ', <name>' and {vehicle} the vehiclePhrase when known."),
    _prompt("vehiclePhrase", " for your {vehicle}", " dla Państwa {vehicle}", " para su {vehicle}", " für Ihren {vehicle}", note="Inserted into the greeting when the vehicle is known."),
    _prompt("notBooked", "I have not booked the appointment yet. Shall I book it now?", "Jeszcze nie zarezerwowałem terminu. Czy mam go teraz zarezerwować?",
            "Todavía no he reservado la cita. ¿Quiere que la reserve ahora?", "Ich habe den Termin noch nicht gebucht. Soll ich ihn jetzt buchen?", note="Replaces a booking or move claim made without a tool result (guard false_confirmation)."),
    _prompt("notCancelled", "I have not cancelled the appointment yet. Shall I cancel it now?", "Jeszcze nie odwołałem wizyty. Czy mam ją teraz odwołać?",
            "Todavía no he cancelado la cita. ¿Quiere que la cancele ahora?", "Ich habe den Termin noch nicht storniert. Soll ich ihn jetzt stornieren?", note="Replaces a cancellation claim made before CancelAppointment returned ok (guard false_cancellation)."),
    _prompt("selfService", "You can book the appointment yourself in the the manufacturer me app under Service, or on the the manufacturer website under Book a service appointment: choose your vehicle, your dealer, the service and a time, and you receive the confirmation by email or SMS.",
            "Wizytę mogą Państwo umówić samodzielnie w aplikacji the manufacturer me w sekcji Serwis lub na stronie the manufacturer w zakładce Umów wizytę serwisową: wystarczy wybrać pojazd, dealera, rodzaj usługi i termin, a potwierdzenie przyjdzie e-mailem lub SMS-em.",
            "Puede reservar la cita usted mismo en la aplicación the manufacturer me, en Servicio, o en la web de the manufacturer en Reservar cita de taller: elija su vehículo, su concesionario, el servicio y una hora, y recibirá la confirmación por correo o SMS.",
            "Sie können den Termin selbst in der the manufacturer me App unter Service oder auf der the manufacturer Website unter Werkstatttermin buchen: Fahrzeug, Händler, Leistung und Zeit wählen, die Bestätigung kommt per E-Mail oder SMS.",
            note="Assisted self-service instructions, read out verbatim when the state becomes SELF_SERVICE (guard self_service_line). Placeholder until the CAC work instruction text arrives (question 27)."),
    _prompt("repeat", "Sorry, could you repeat that?", "Przepraszam, czy mogą Państwo powtórzyć?", "Disculpe, ¿puede repetirlo?", "Entschuldigung, können Sie das wiederholen?", note="Spoken when the model returned an empty reply."),
    _prompt("techError", "Sorry, I have a technical problem. I will connect you to a colleague.", "Przepraszam, mam problem techniczny. Połączę Państwa z konsultantem.",
            "Disculpe, tengo un problema técnico. Le paso con un agente.", "Entschuldigung, ich habe ein technisches Problem. Ich verbinde Sie mit einem Mitarbeiter.", note="Spoken when the dialogue agent fails (PLATFORM_ERROR handoff)."),
    _prompt("confirmation", "Workshop: service appointment confirmed. Code {code}, {dealer}, slot {slot}.", "Serwis: wizyta serwisowa potwierdzona. Kod {code}, {dealer}, termin {slot}.",
            "Taller: cita de taller confirmada. Código {code}, {dealer}, cita {slot}.", "Werkstatt: Werkstatttermin bestätigt. Code {code}, {dealer}, Termin {slot}.", note="Retired 2026-10-22: DealerBook sends the confirmation via Cusmes. Kept for AR-16, which is no longer called."),
    _prompt("retryTransfer", "One moment, I am trying the transfer again.", "Chwileczkę, próbuję ponownie połączyć z konsultantem.", "Un momento, vuelvo a intentar pasarle con un agente.", "Einen Moment, ich versuche die Weiterleitung noch einmal.", note="First REFER failure."),
    _prompt("transferFailed", "I could not transfer you. A colleague will call you back. Goodbye.", "Nie udało mi się połączyć. Konsultant oddzwoni do Państwa. Do widzenia.",
            "No he podido transferirle. Un agente le devolverá la llamada. Adiós.", "Die Weiterleitung ist fehlgeschlagen. Ein Mitarbeiter ruft Sie zurück. Auf Wiederhören.", note="Second REFER failure: the call ends."),
]


def build_ar90():
    w = WF("ar-90-seed-config", "AR-90 Seed configuration tables", "POST /webhook/ar/v1/admin/seed. Upserts ar_markets (PL, ES, DE, GB), ar_codes (intent and handoff reason codes), ar_prompts (goals, rules and every spoken line, per language) and the lab fixtures (customers, dealers, the two existing appointments). The body may carry mockCustomers, mockDealers and mockBookings rows to upsert on top (the test identities of the engagement, kept out of the generator). Run once per environment; safe to re-run.")
    w.sticky("## AR-90 Seed config\nMarkets, codes and prompts are data, not code: a new market is one row, a wording change is one cell. Disclosure texts are placeholders until Legal supplies the market wording. The mock rows in the body (the engagement's test identities, gitignored, sent by ar-seed.py) are upserted after the synthetic fixtures; missing columns are filled from the fixture shape.", 0, -400, 760, 120)
    a = w.webhook("ReceiveSeed", "ar/v1/admin/seed", 0, 0)
    code_rows = [dict(zip(["kind","code","short","ccValue","description"], r)) for r in CODE_ROWS]
    seeds = [("Market", "ar_markets", MARKET_ROWS, [("market", "={{ $json.market }}")], None),
             ("Code", "ar_codes", code_rows, [("kind", "={{ $json.kind }}"), ("code", "={{ $json.code }}")], None),
             ("Prompt", "ar_prompts", PROMPT_ROWS, [("key", "={{ $json.key }}")], None),
             ("MockCustomer", "ar_mock_customers", MOCK_CUSTOMERS, [("vin", "={{ $json.vin }}")], "mockCustomers"),
             ("MockDealer", "ar_mock_dealers", MOCK_DEALERS, [("dealerId", "={{ $json.dealerId }}")], "mockDealers"),
             ("MockBooking", "ar_mock_bookings", MOCK_BOOKINGS, [("bookingRef", "={{ $json.bookingRef }}")], "mockBookings")]
    body = "($('ReceiveSeed').first().json.body || {})"
    prev = a
    for i, (label, table, rows, match, overlay) in enumerate(seeds):
        # executeOnce: the previous upsert hands over one item per row; without it each table's rows would be emitted once per incoming item.
        if overlay:
            # Fixture rows plus the body's rows, each body row completed to the fixture's columns (booleans default false, the rest empty).
            expr = ("={{ (s => ({ rows: s.concat((" + body + "." + overlay + " || []).map(r => Object.fromEntries(Object.keys(s[0]).map(k => [k, r[k] !== undefined ? r[k] : (typeof s[0][k] === 'boolean' ? false : '')])))) }))("
                    + json.dumps(rows, ensure_ascii=False) + ") }}")
        else:
            expr = json.dumps({"rows": rows}, ensure_ascii=False, indent=2)
        r = w.set_json(f"{label}Rows", expr, 260 + i * 780, 0, executeOnce=True)
        s = w.split_out(f"Split{label}s", "rows", 520 + i * 780, 0)
        u = w.dt(f"Upsert{label}", table, "upsert", 780 + i * 780, 0, conditions=match, values={c: f"={{{{ $json.{c} }}}}" for c in TABLE_COLS[table]})
        w.chain(prev, r, s, u)
        prev = u
    rs = w.respond("RespondSeeded", "={{ JSON.stringify({ ok: true, markets: " + str(len(MARKET_ROWS)) + ", codes: " + str(len(CODE_ROWS)) + ", prompts: " + str(len(PROMPT_ROWS))
                   + ", mockCustomers: " + str(len(MOCK_CUSTOMERS)) + " + (" + body + ".mockCustomers || []).length, mockDealers: " + str(len(MOCK_DEALERS)) + " + (" + body + ".mockDealers || []).length"
                   + ", mockBookings: " + str(len(MOCK_BOOKINGS)) + " + (" + body + ".mockBookings || []).length }) }}", 4940, 0)
    w.link(prev, rs)
    return w


# =============================================================================
# AR-50 to AR-55: Twilio transfer proof (decision doc 13). Lab only; not part of the demo.
# Config (non-secret) in ar_proof_config: publicBase, openaiProjectId, openaiSipHost, sipSecure, model, voice, humanNumber,
# referDomain, mcpToken. Secrets: the OpenAI key and the MCP bearer are n8n credentials created by setup.py.
# =============================================================================
XML = '<?xml version="1.0" encoding="UTF-8"?>'
HEX_DECODE = "(h => (String(h || '').match(/.{2}/g) || []).map(x => String.fromCharCode(parseInt(x, 16))).join(''))"
HEX_ENCODE = "(t => String(t || '').split('').map(ch => ch.charCodeAt(0).toString(16).padStart(2, '0')).join('').toUpperCase())"
OPENAI_CALLS = "https://api.openai.com/v1/realtime/calls/"


def log_inputs(workflow, event, **kw):
    base = {"correlationId": "", "sessionId": "", "caseId": "", "workflow": workflow, "event": event, "state": "", "tool": "", "latencyMs": 0, "outcome": "", "errorCode": "", "detail": ""}
    base.update(kw)
    return base


def build_ar50():
    w = WF("ar-50-twilio-voice", "AR-50 PoC: Twilio voice entry", "POST /webhook/ar/twilio/voice, called by Twilio Programmable Voice on an inbound call. Answers TwiML: Dial Sip to the OpenAI Realtime SIP ingress with a synthetic in-car UUI, referUrl on AR-53, action on AR-54. Without openaiProjectId in ar_proof_config the caller hears a lab line (Twilio-only smoke test). POST /webhook/ar/proof/config (gateway auth) upserts the config rows.")
    w.sticky("## AR-50 Twilio voice entry\nProof of decision doc 13: Twilio stands in for the contact center trunk. No Twilio signature check on the lab (PoC exception, test plan). The UUI is the first GB fixture customer, hex-encoded as contact center would send it.", 0, -400, 760, 110)
    a = w.webhook("ReceiveTwilioCall", "ar/twilio/voice", 0, 0, auth=False)
    g, c = w.load_config(260, 0)
    cu = w.dt("PickCustomer", "ar_mock_customers", "get", 900, 0, conditions=[("market", "GB")])
    C, T, K = "$('ProofConfig').first().json", "$('ReceiveTwilioCall').first().json.body", "$('PickCustomer').first().json"
    corr = f"('TW-' + String({T}.CallSid || Date.now()).slice(-10))"
    raw = f"['1', {corr}, {K}.vin || '', {K}.msisdn || '', {K}.language || 'en'].join(';')"
    su = w.set_fields("ShapeUui", [
        ("callSid", f"={{{{ String({T}.CallSid || '') }}}}", "string"), ("from", f"={{{{ String({T}.From || '') }}}}", "string"), ("to", f"={{{{ String({T}.To || '') }}}}", "string"),
        ("correlationId", f"={{{{ {corr} }}}}", "string"), ("uuiRaw", f"={{{{ {raw} }}}}", "string"),
        ("uuiHex", f"={{{{ {HEX_ENCODE}({raw}) }}}}", "string"),
        ("sipUri", f"={{{{ 'sip:' + ({C}.openaiProjectId || '') + '@' + ({C}.openaiSipHost || 'sip-eu.api.openai.com') + ';transport=tls' + ({C}.sipSecure === 'true' ? ';secure=true' : '') }}}}", "string"),
        ("publicBase", f"={{{{ String({C}.publicBase || '').replace(/\\/$/, '') }}}}", "string"),
        ("hasOpenAi", f"={{{{ !!({C}.openaiProjectId) }}}}", "boolean"),
    ], 1100, 0)
    ho = w.if_node("HasOpenAi", "={{ $json.hasOpenAi }}", 1300, 0)
    dial = w.set_fields("TwimlDial", [("twiml", "={{ '" + XML + "<Response><Dial referUrl=\"' + $json.publicBase + '/webhook/ar/twilio/refer\" referMethod=\"POST\" action=\"' + $json.publicBase + '/webhook/ar/twilio/done\" method=\"POST\" answerOnBridge=\"true\"><Sip>' + $json.sipUri + '?User-to-User=' + $json.uuiHex + '%3Bencoding%3Dhex</Sip></Dial></Response>' }}", "string")], 1500, -100, keep=True)
    line = w.set_fields("TwimlLabLine", [("twiml", XML + '<Response><Say voice="Polly.Amy">This is the n8n lab for the agentic race. Your call reached n8n through the tunnel. The AI leg is not configured yet. Goodbye.</Say><Hangup/></Response>', "string")], 1500, 100, keep=True)
    r = w.respond_text("RespondTwiml", "={{ $json.twiml }}", 1700, 0)
    S = "$('ShapeUui').first().json"
    lg = w.exec_wf("LogTwilioCall", "ar-17-log-event", log_inputs("AR-50", "twilio_call", correlationId=f"={{{{ {S}.correlationId }}}}", outcome=f"={{{{ {S}.hasOpenAi ? 'dial_sip' : 'lab_line' }}}}",
                   detail=f"={{{{ 'callSid=' + {S}.callSid + ' from=' + {S}.from + ' uui=' + {S}.uuiRaw }}}}"), 1900, 0)
    w.chain(a, g); w.chain(c, cu, su, ho)
    w.link(ho, dial, 0); w.link(ho, line, 1); w.link(dial, r); w.link(line, r); w.link(r, lg)
    # Config admin: {rows: [{key, value}]} upserted on key.
    ca = w.webhook("ReceiveProofConfig", "ar/proof/config", 0, 400)
    so = w.split_out("SplitRows", "body.rows", 260, 400)
    up = w.dt("UpsertProofConfig", "ar_proof_config", "upsert", 520, 400, conditions=[("key", "={{ $json.key }}")], values={"key": "={{ $json.key }}", "value": "={{ String($json.value ?? '') }}"})
    ag = w.aggregate("ConfigRows", "rows", 780, 400)
    rc = w.respond("RespondConfig", "={{ JSON.stringify({ ok: true, keys: ($json.rows || []).map(r => r.key) }) }}", 1040, 400)
    w.chain(ca, so, up, ag, rc)
    return w


SIDEBAND_BASE = "http://host.docker.internal:8790"  # scripts/ar-sideband.mjs on the Mac, as the lab n8n reaches it

ACCEPT_INSTRUCTIONS = ("['You are the workshop appointment assistant of a car brand, on a live phone call. Speak ' + ({ pl: 'Polish', es: 'Spanish', de: 'German' }[$json.language] || 'English') + '.', "
                       "'One message per turn: one or two short sentences ending in one question, then stop and wait for the caller. Never say two messages in a row. Never narrate what you are doing or thinking.', "
                       "'Internal reference, never spoken: sessionId ' + $json.sessionId + '. Pass it as sessionId to every tool, unchanged. Never say a sessionId or a slotId aloud.', "
                       "'Flow, one step per turn: confirm the identity; ask for consent; ask what the caller needs; FindSlots and offer at most two slots by day and time; when the caller picks one, ConfirmSlot and read back exactly what it returns, then ask for a yes; after the yes, BookAppointment with the same slotId and read the confirmation code back slowly.', "
                       "'Only say what a tool returned. Never invent a dealer, a time or a booking. If a tool fails, say the booking system is unavailable right now and offer a colleague.', "
                       "'After the confirmation code, ask once whether the caller needs anything else. When they say no or say goodbye, call EndCall, say exactly the line it returns, and stop talking.', "
                       "'If the caller asks for a person, call RequestAgent with a one-line summary, then say: I am connecting you to a colleague now, and stop talking.'].join(' ')")


def build_ar51():
    w = WF("ar-51-openai-call-in", "AR-51 PoC: OpenAI call incoming", "POST /webhook/ar/openai/call-incoming, the OpenAI webhook for realtime.call.incoming. Reads the UUI from the SIP headers, starts the n8n session (AR-01), accepts the call with the greeting, the session id and the n8n MCP tool server, and stores the call id on the session for the REFER. Responds 200 before the accept; signature verification is a PoC exception.")
    w.sticky("## AR-51 OpenAI call incoming\nAccept within two seconds: the 200 goes first, then AR-01 and the accept. The prompt is a short proof prompt, not the AR-02 turn prompt. Webhook signature (Standard Webhooks HMAC) is not verified on the lab: a forged call id fails at the accept.", 0, -400, 760, 110)
    a = w.webhook("ReceiveCallIncoming", "ar/openai/call-incoming", 0, 0, auth=False)
    ii = w.if_rules("IsCallIncoming", [w.cond("={{ String($json.body.type || '') }}", "equals", "realtime.call.incoming")], 260, 0)
    ack = w.respond("RespondAck", "={{ JSON.stringify({ ok: true }) }}", 520, -100)
    ign = w.respond("RespondIgnored", "={{ JSON.stringify({ ok: true, ignored: $json.body.type || 'unknown' }) }}", 520, 100)
    D = "$('ReceiveCallIncoming').first().json.body.data"
    hdr = f"(name => ((({D}.sip_headers || []).find(h => String(h.name || '').toLowerCase() === name) || {{}}).value || ''))"
    uui = f"{HEX_DECODE}(String({hdr}('user-to-user')).split(';')[0])"
    sh = w.set_fields("ShapeCall", [
        ("callId", f"={{{{ String({D}.call_id || '') }}}}", "string"), ("startedAt", "={{ Date.now() }}", "number"),
        ("uuiRaw", f"={{{{ {uui} }}}}", "string"),
        ("correlationId", f"={{{{ ({uui}).split(';')[1] || ('OA-' + String({D}.call_id || '').slice(-10)) }}}}", "string"),
        ("vin", f"={{{{ ({uui}).split(';')[2] || '' }}}}", "string"), ("msisdn", f"={{{{ ({uui}).split(';')[3] || '' }}}}", "string"),
        ("language", f"={{{{ (({uui}).split(';')[4] || 'en').toLowerCase().slice(0, 2) }}}}", "string"),
        ("sipFrom", f"={{{{ {hdr}('from') }}}}", "string"),
    ], 780, -100)
    g, c = w.load_config(1040, -100)
    S = "$('ShapeCall').first().json"
    st = w.http_json("StartSession", "POST", LAB + "/webhook/ar/v1/session/start",
                     f"={{{{ JSON.stringify({{ correlationId: {S}.correlationId, uui: {{ version: '1', raw: {S}.uuiRaw, vin: {S}.vin, msisdn: {S}.msisdn, language: {S}.language }}, sip: {{ callId: {S}.callId }}, gateway: {{ vendor: 'openai-realtime-sip' }} }}) }}}}", 1700, -100, cred=CRED_GW)
    C = "$('ProofConfig').first().json"
    ca = w.set_fields("ComposeAccept", [
        ("sessionId", "={{ $json.sessionId || '' }}", "string"), ("caseId", "={{ $json.caseId || '' }}", "string"), ("speak", "={{ $json.speak || '' }}", "string"),
        ("language", f"={{{{ $json.language || {S}.language }}}}", "string"), ("callId", f"={{{{ {S}.callId }}}}", "string"),
        ("instructions", f"={{{{ {ACCEPT_INSTRUCTIONS} }}}}", "string"),
    ], 1900, -100)
    acc = w.http_json("AcceptCall", "POST", "={{ '" + OPENAI_CALLS + "' + $json.callId + '/accept' }}",
                      f"={{{{ JSON.stringify({{ type: 'realtime', model: {C}.model || 'gpt-realtime-2.1-mini', instructions: $json.instructions, audio: {{ input: {{ transcription: {{ model: 'gpt-4o-mini-transcribe' }}, turn_detection: {{ type: 'server_vad', threshold: Number({C}.vadThreshold || 0.7), prefix_padding_ms: 300, silence_duration_ms: Number({C}.vadSilenceMs || 700), create_response: true, interrupt_response: true }}, noise_reduction: {{ type: 'far_field' }} }}, output: {{ voice: {C}.voice || 'marin' }} }}, tools: [{{ type: 'mcp', server_label: 'n8n', server_url: String({C}.publicBase || '').replace(/\\/$/, '') + '/mcp/ar-proof-tools', authorization: {C}.mcpToken || '', require_approval: 'never' }}], tool_choice: 'auto', max_output_tokens: 600 }}) }}}}",
                      2100, -100, cred=CRED_OPENAI)
    A = "$('ComposeAccept').first().json"
    ns = w.http_json("NotifySideband", "POST", f"={{{{ String({C}.sidebandBase || SIDEBAND_BASE).replace(/\\/$/, '') + '/attach' }}}}".replace("SIDEBAND_BASE", repr(SIDEBAND_BASE)),
                     f"={{{{ JSON.stringify({{ callId: {A}.callId, sessionId: {A}.sessionId, correlationId: {S}.correlationId, language: {A}.language, speak: {A}.speak, vadThreshold: {C}.vadThreshold || '', vadSilenceMs: {C}.vadSilenceMs || '' }}) }}}}", 2300, -200, timeout=5000)
    us = w.dt("StoreCallId", "ar_sessions", "update", 2500, -200, conditions=[("sessionId", f"={{{{ {A}.sessionId }}}}")], values={"voiceCallId": f"={{{{ {A}.callId }}}}"})
    lg = w.exec_wf("LogAccept", "ar-17-log-event", log_inputs("AR-51", "call_accepted", correlationId=f"={{{{ {S}.correlationId }}}}", sessionId=f"={{{{ {A}.sessionId }}}}", caseId=f"={{{{ {A}.caseId }}}}",
                   latencyMs=f"={{{{ Date.now() - {S}.startedAt }}}}", outcome="accepted", detail=f"={{{{ 'callId=' + {S}.callId + ' from=' + {S}.sipFrom }}}}"), 2500, -200)
    lf = w.exec_wf("LogAcceptFailed", "ar-17-log-event", log_inputs("AR-51", "call_accept_failed", correlationId=f"={{{{ {S}.correlationId }}}}", sessionId=f"={{{{ {A}.sessionId }}}}",
                   latencyMs=f"={{{{ Date.now() - {S}.startedAt }}}}", outcome="failed", errorCode="accept_failed", detail="={{ String(($json.error || {}).message || '').slice(0, 200) }}"), 2500, 0)
    rj = w.http_json("RejectCall", "POST", "={{ '" + OPENAI_CALLS + "' + " + S + ".callId + '/reject' }}", "={{ JSON.stringify({ status_code: 486 }) }}", 1900, 100, cred=CRED_OPENAI)
    ls = w.exec_wf("LogStartFailed", "ar-17-log-event", log_inputs("AR-51", "session_start_failed", correlationId=f"={{{{ {S}.correlationId }}}}", outcome="rejected", errorCode="start_failed",
                   detail="={{ String(($json.error || {}).message || '').slice(0, 200) }}"), 2100, 100)
    w.chain(a, ii)
    w.link(ii, ack, 0); w.link(ii, ign, 1)
    w.chain(ack, sh, g); w.chain(c, st)
    w.link(st, ca, 0); w.link(st, rj, 1); w.link(rj, ls); w.link(rj, ls, 1)
    w.chain(ca, acc)
    w.link(acc, ns, 0); w.link(ns, us, 0); w.link(ns, us, 1); w.link(us, lg); w.link(acc, lf, 1)
    return w


def build_ar56():
    w = WF("ar-56-confirm-slot", "AR-56 PoC: Confirm slot", "Agent tool for the voice path. Marks one offered slot as read back (state CONFIRM, slotId on the session) so AR-13 BookingGate can book it in the next turn. Inputs: sessionId, slotId. Returns {ok, when, dealerName, say} or {ok:false, error}.")
    w.sticky("## AR-56 ConfirmSlot\nThe CONFIRM step AR-02 performs in the demo, as a tool: the model calls it with the slot the caller picked, reads back what it returns, and books only after the yes. A slot not offered in this call is refused.", 0, -400, 760, 100)
    t = w.sub_trigger([("sessionId", "string"), ("slotId", "string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    wanted = "String($('ToolInput').first().json.slotId || '').trim()"
    offered = "JSON.parse($json.offeredSlots || '[]')"
    io = w.if_rules("IsOffered", [w.cond(f"={{{{ {offered}.some(o => o.slotId === {wanted}) }}}}", "true", kind="boolean")], 520, 0)
    pick = w.set_fields("PickSlot", [
        ("sessionId", "={{ $json.sessionId }}", "string"),
        ("slot", f"={{{{ {offered}.find(o => o.slotId === {wanted}) }}}}", "object")], 780, -100)
    u = w.dt("StoreConfirm", "ar_sessions", "update", 1040, -100, conditions=[("sessionId", "={{ $json.sessionId }}")],
             values={"state": "CONFIRM", "slotId": "={{ $json.slot.slotId }}"})
    rc = w.set_fields("ReturnConfirm", [
        ("ok", True, "boolean"), ("slotId", "={{ $('PickSlot').first().json.slot.slotId }}", "string"),
        ("when", "={{ $('PickSlot').first().json.slot.label }}", "string"),
        ("dealerName", "={{ $('PickSlot').first().json.slot.dealerName || '' }}", "string"),
        ("say", "={{ 'Read back exactly: ' + ($('PickSlot').first().json.slot.dealerName || 'the workshop') + ', ' + $('PickSlot').first().json.slot.label + '. Then ask: shall I book it? Call BookAppointment with this slotId only after the caller says yes.' }}", "string")], 1300, -100)
    rn = w.set_fields("ReturnNotOffered", [
        ("ok", False, "boolean"), ("error", "slot_not_offered", "string"),
        ("message", "slotId is not one of the slots offered in this call. Call FindSlots and use one of its slotIds.", "string"),
        ("offeredSlotIds", f"={{{{ {offered}.map(o => o.slotId) }}}}", "array")], 780, 100)
    w.chain(t, g, io)
    w.link(io, pick, 0); w.chain(pick, u, rc); w.link(io, rn, 1)
    return w


def build_ar57():
    w = WF("ar-57-end-call", "AR-57 PoC: End call", "Agent tool for the voice path. Marks the session ended and returns the market closing line for the model to say; the sideband sidecar hangs the call up once that line has been spoken. Inputs: sessionId, reason. Returns {ok, say}.")
    w.sticky("## AR-57 EndCall\nThe model calls it when the caller is done. The session gets state ENDED and the reason; the closing line comes from the market row, never from the model. The hangup itself is the sidecar's, after the audio of the goodbye has stopped.", 0, -400, 760, 100)
    t = w.sub_trigger([("sessionId", "string"), ("reason", "string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    m = w.dt("LoadMarket", "ar_markets", "get", 520, 0, conditions=[("market", "={{ $json.market }}")])
    u = w.dt("StoreEnded", "ar_sessions", "update", 780, 0, conditions=[("sessionId", "={{ $('ToolInput').first().json.sessionId }}")],
             values={"state": "ENDED", "endedAt": "={{ new Date().toISOString() }}", "endReason": "={{ String($('ToolInput').first().json.reason || 'caller_done').slice(0, 40) }}"})
    r = w.set_fields("ReturnEnd", [
        ("ok", True, "boolean"),
        ("say", "={{ $('LoadMarket').first().json.closingLine || 'Thank you for calling. Goodbye.' }}", "string"),
        ("note", "Say exactly the closing line, then stop talking. The call is hung up for you.", "string")], 1040, 0)
    w.chain(t, g, m, u, r)
    return w


def build_ar52():
    w = WF("ar-52-proof-tools", "AR-52 PoC: MCP tool server", "MCP Server Trigger at /mcp/ar-proof-tools (bearer auth) exposing FindDealers, FindSlots, BookAppointment and RequestAgent to the OpenAI Realtime session. The same sub-workflows the demo agent uses; RequestAgent is the REFER path (AR-55).")
    w.sticky("## AR-52 MCP tool server\nThe Realtime API executes MCP tools itself, so the tools are the sub-workflows behind a bearer-protected MCP trigger. The session id travels in the instructions and comes back as a tool argument.", 0, -400, 760, 110)
    p = {"path": "ar-proof-tools", "authentication": "bearerAuth",
         "instructions": "Appointment booking tools for one phone call. Always pass the sessionId from the instructions. Order: FindSlots, ConfirmSlot when the caller picks one, BookAppointment after the caller says yes, EndCall when the caller is done. Call RequestAgent when the caller asks for a person."}
    extra = {"credentials": {"httpBearerAuth": CRED_MCP}} if CRED_MCP else {}
    t = w.add("ProofTools", "@n8n/n8n-nodes-langchain.mcpTrigger", 2.1, p, 0, 0, **extra)
    sid = "={{ $fromAI('sessionId', 'The session id given in the instructions, unchanged', 'string') }}"
    tools = [
        w.tool_wf("FindDealers", "ar-11-find-dealers", "Dealers in the caller's market, nearest first. Call when the caller names a city or asks where to go.",
                  {"sessionId": sid, "city": "={{ $fromAI('city', 'City the caller named, as spoken, or empty for all dealers', 'string') }}"}, 0, 300),
        w.tool_wf("FindSlots", "ar-12-find-slots", "Free workshop slots at the agreed dealer. Returns slotIds with labels; read at most three back.",
                  {"sessionId": sid, "serviceIds": "={{ $fromAI('serviceIds', 'Service ids the caller chose, comma separated, for example MAINT; default MAINT', 'string') }}",
                   "fromDate": "={{ $fromAI('fromDate', 'Earliest acceptable date as YYYY-MM-DD, or empty for the earliest available', 'string') }}"}, 208, 300),
        w.tool_wf("ConfirmSlot", "ar-56-confirm-slot", "Marks the slot the caller picked as read back. Call it as soon as the caller chooses a day and time, then read back what it returns and ask for a yes.",
                  {"sessionId": sid, "slotId": "={{ $fromAI('slotId', 'The slotId of the slot the caller picked, from FindSlots', 'string') }}"}, 416, 300),
        w.tool_wf("BookAppointment", "ar-13-book-appointment", "Books the slot after ConfirmSlot and the caller's yes. Returns the confirmation code; only this code may be read out.",
                  {"sessionId": sid, "slotId": "={{ $fromAI('slotId', 'The slotId the caller confirmed, the same one given to ConfirmSlot', 'string') }}", "mobility": "={{ $fromAI('mobility', 'Courtesy car or taxi if asked for, else empty', 'string') }}",
                   "notes": "={{ $fromAI('notes', 'Anything the workshop should know, or empty', 'string') }}"}, 624, 300),
        w.tool_wf("RequestAgent", "ar-55-refer-call", "Transfers the call to a human agent with the context. Call only when the caller asks for a person or the request cannot be served; then say the handover line and stop.",
                  {"sessionId": sid, "summary": "={{ $fromAI('summary', 'One line on what the caller wanted and how far the call got', 'string') }}"}, 832, 300),
        w.tool_wf("EndCall", "ar-57-end-call", "Ends the call. Call it when the caller says they need nothing else, or says goodbye; then say exactly the line it returns and stop.",
                  {"sessionId": sid, "reason": "={{ $fromAI('reason', 'caller_done, booked, or declined', 'string') }}"}, 1040, 300),
    ]
    for tl in tools:
        w.link(tl, t, ctype="ai_tool")
    return w


def build_ar55():
    w = WF("ar-55-refer-call", "AR-55 PoC: Refer call to agent", "Inputs: sessionId, summary. Builds the handover UUI (AR-14), then POSTs refer to the OpenAI call with target_uri sip:agent@<referDomain>?User-to-User=<hex>;encoding=hex. Twilio receives the REFER and posts the Refer-To to AR-53. Returns {ok, message}.")
    w.sticky("## AR-55 Refer call\nRisk 1 and 2 of decision doc 13 in one call: does the REFER reach the carrier, and does the query parameter in Refer-To survive. AR-53 answers both from what Twilio posts.", 0, -400, 760, 110)
    t = w.sub_trigger([("sessionId", "string"), ("summary", "string")])
    ls = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    I, S = "$('ToolInput').first().json", "$('LoadSession').first().json"
    bh = w.exec_wf("BuildHandoff", "ar-14-build-handoff", {"sessionId": f"={{{{ {I}.sessionId }}}}", "intent": f"={{{{ {S}.intent || 'UNKNOWN' }}}}", "handoffReason": "CALLER_REQUEST", "summary": f"={{{{ {I}.summary || '' }}}}"}, 520, 0)
    g, c = w.load_config(780, 0)
    B, C = "$('BuildHandoff').first().json", "$('ProofConfig').first().json"
    rq = w.set_fields("ReferRequest", [
        ("callId", f"={{{{ {S}.voiceCallId || '' }}}}", "string"), ("sessionId", f"={{{{ {I}.sessionId }}}}", "string"), ("correlationId", f"={{{{ {S}.correlationId || '' }}}}", "string"),
        ("uuiHex", f"={{{{ {B}.uuiHex || '' }}}}", "string"),
        ("targetUri", f"={{{{ 'sip:agent@' + ({C}.referDomain || 'proof.n8n.invalid') + '?User-to-User=' + ({B}.uuiHex || '') + '%3Bencoding%3Dhex' }}}}", "string"),
        ("startedAt", "={{ Date.now() }}", "number"),
    ], 1420, 0)
    hc = w.if_node("HasCallId", "={{ $json.callId }}", 1620, 0, kind="string")
    cr = w.http_json("CallRefer", "POST", "={{ '" + OPENAI_CALLS + "' + $json.callId + '/refer' }}", "={{ JSON.stringify({ target_uri: $json.targetUri }) }}", 1820, -100, cred=CRED_OPENAI)
    R = "$('ReferRequest').first().json"
    lg = w.exec_wf("LogReferSent", "ar-17-log-event", log_inputs("AR-55", "refer_sent", correlationId=f"={{{{ {R}.correlationId }}}}", sessionId=f"={{{{ {R}.sessionId }}}}", latencyMs=f"={{{{ Date.now() - {R}.startedAt }}}}",
                   outcome="sent", detail=f"={{{{ 'target=' + {R}.targetUri }}}}"), 2020, -100)
    lf = w.exec_wf("LogReferFailed", "ar-17-log-event", log_inputs("AR-55", "refer_failed", correlationId=f"={{{{ {R}.correlationId }}}}", sessionId=f"={{{{ {R}.sessionId }}}}", latencyMs=f"={{{{ Date.now() - {R}.startedAt }}}}",
                   outcome="failed", errorCode="={{ $json.callId ? 'refer_failed' : 'no_call_id' }}", detail="={{ String(($json.error || {}).message || 'no voiceCallId on the session').slice(0, 200) }}"), 2020, 100)
    ok = w.set_json("ReturnReferred", "={{ { ok: true, message: 'Transfer requested. Say the handover line and stop talking.' } }}", 2220, -100)
    ko = w.set_json("ReturnReferFailed", "={{ { ok: false, error: 'refer_failed', message: 'The transfer could not be requested. Apologise once and ask the caller to call the dealer directly.' } }}", 2220, 100)
    w.chain(t, ls, bh, g); w.chain(c, rq, hc)
    w.link(hc, cr, 0); w.link(hc, lf, 1)
    w.link(cr, lg, 0); w.link(cr, lf, 1)
    w.link(lg, ok); w.link(lf, ko)
    return w


def build_ar53():
    w = WF("ar-53-twilio-refer", "AR-53 PoC: Twilio refer received", "POST /webhook/ar/twilio/refer, Twilio's referUrl: the Refer-To of the REFER arrives as ReferTransferTarget. Decodes the UUI, stores refer_received on the session, logs the target byte-exact, and answers TwiML that dials the human number from ar_proof_config (or says the context and hangs up).")
    w.sticky("## AR-53 Twilio refer received\nThe proof's pass condition lives here: ReferTransferTarget carries the UUI query parameter, n8n decodes it, the human phone rings. No Twilio signature check on the lab (PoC exception).", 0, -400, 760, 110)
    a = w.webhook("ReceiveRefer", "ar/twilio/refer", 0, 0, auth=False)
    g, c = w.load_config(260, 0)
    T, C = "$('ReceiveRefer').first().json.body", "$('ProofConfig').first().json"
    tgt = f"String({T}.ReferTransferTarget || '')"
    hexv = f"(({tgt}).match(/User-to-User=([0-9A-Fa-f]+)/i) || [])[1] || ''"
    raw = f"{HEX_DECODE}({hexv})"
    sh = w.set_fields("ShapeRefer", [
        ("callSid", f"={{{{ String({T}.CallSid || '') }}}}", "string"), ("target", f"={{{{ {tgt} }}}}", "string"),
        ("uuiHex", f"={{{{ {hexv} }}}}", "string"), ("uuiRaw", f"={{{{ {raw} }}}}", "string"),
        ("correlationId", f"={{{{ ({raw}).split(';')[1] || '' }}}}", "string"), ("sessionId", f"={{{{ ({raw}).split(';')[2] || '' }}}}", "string"),
        ("humanNumber", f"={{{{ String({C}.humanNumber || '') }}}}", "string"),
    ], 900, 0)
    us = w.dt("StoreReferReceived", "ar_sessions", "update", 1100, 0, conditions=[("sessionId", "={{ $json.sessionId }}")], values={"handoffResult": "={{ 'refer_received:' + $json.target.slice(0, 160) }}"})
    S = "$('ShapeRefer').first().json"
    lg = w.exec_wf("LogReferReceived", "ar-17-log-event", log_inputs("AR-53", "refer_received", correlationId=f"={{{{ {S}.correlationId }}}}", sessionId=f"={{{{ {S}.sessionId }}}}",
                   outcome=f"={{{{ {S}.uuiHex ? 'uui_present' : 'uui_missing' }}}}", detail=f"={{{{ 'target=' + {S}.target + ' uui=' + {S}.uuiRaw }}}}"), 1300, 0)
    hh = w.if_node("HasHuman", f"={{{{ {S}.humanNumber }}}}", 1500, 0, kind="string")
    dial = w.set_fields("TwimlDialHuman", [("twiml", "={{ '" + XML + "<Response><Say voice=\"Polly.Amy\">Transfer received with the context. Connecting you to a colleague.</Say><Dial answerOnBridge=\"true\"><Number>' + " + S + ".humanNumber + '</Number></Dial></Response>' }}", "string")], 1700, -100)
    say = w.set_fields("TwimlNoHuman", [("twiml", "={{ '" + XML + "<Response><Say voice=\"Polly.Amy\">Transfer received. The context was ' + (" + S + ".uuiRaw ? 'decoded' : 'missing') + '. No human number is configured. Goodbye.</Say><Hangup/></Response>' }}", "string")], 1700, 100)
    r = w.respond_text("RespondReferTwiml", "={{ $json.twiml }}", 1900, 0)
    w.chain(a, g); w.chain(c, sh, us, lg, hh)
    w.link(hh, dial, 0); w.link(hh, say, 1); w.link(dial, r); w.link(say, r)
    return w


def build_ar54():
    w = WF("ar-54-twilio-done", "AR-54 PoC: Twilio dial done", "POST /webhook/ar/twilio/done, the Dial action callback: logs DialCallStatus, DialSipResponseCode and the duration, then hangs up.")
    w.sticky("## AR-54 Twilio dial done\nThe SIP response code of the Dial Sip leg is the only place a failed INVITE to the vendor ingress shows up.", 0, -400, 700, 100)
    a = w.webhook("ReceiveDone", "ar/twilio/done", 0, 0, auth=False)
    B = "$json.body"
    sh = w.set_fields("ShapeDone", [
        ("callSid", f"={{{{ String({B}.CallSid || '') }}}}", "string"), ("dialStatus", f"={{{{ String({B}.DialCallStatus || '') }}}}", "string"),
        ("sipCode", f"={{{{ String({B}.DialSipResponseCode || '') }}}}", "string"), ("duration", f"={{{{ Number({B}.DialCallDuration || 0) }}}}", "number"),
        ("bridged", f"={{{{ String({B}.DialBridged || '') }}}}", "string"),
    ], 260, 0)
    lg = w.exec_wf("LogDone", "ar-17-log-event", log_inputs("AR-54", "twilio_dial_done", latencyMs="={{ $json.duration * 1000 }}", outcome="={{ $json.dialStatus || 'unknown' }}",
                   errorCode="={{ /^[45]/.test($json.sipCode) ? 'sip_' + $json.sipCode : '' }}", detail="={{ 'callSid=' + $json.callSid + ' sip=' + $json.sipCode + ' bridged=' + $json.bridged }}"), 520, 0)
    r = w.respond_text("RespondHangup", XML + "<Response><Hangup/></Response>", 780, 0)
    w.chain(a, sh, lg, r)
    return w

BUILDERS = [build_ar17, build_ar20, build_ar21, build_ar22, build_ar23, build_ar10, build_ar11, build_ar12, build_ar13, build_ar14, build_ar15, build_ar16,
            build_ar18, build_ar19, build_ar24, build_ar25, build_ar26, build_ar27,
            build_ar01, build_ar02, build_ar03, build_ar04, build_ar05, build_ar30, build_ar90,
            build_ar50, build_ar51, build_ar52, build_ar56, build_ar57, build_ar55, build_ar53, build_ar54]

# =============================================================================
# Canvas zones (n8n-canvas-docs): one entry per stage, rows of node names, None = empty column.
# Colours: 4 green human touchpoints, 3 red state and critical calls, 5 blue logic, 6 purple setup, 7 gray docs.
# =============================================================================
ZONES = {
    "ar-01-session-start": [
        {"title": "Receive", "color": 4, "body": "The gateway posts the SIP UUI once the INVITE is answered. Header auth. ParseStart splits the raw UUI; ResolveMarket: gateway value, phone prefix, language, PL. A missing correlationId or a UUI version other than 1 answers 400 and the call is released.",
         "rows": [["ReceiveStart", "ParseStart", "ResolveMarket", "IsValid"], [None, None, None, "RespondInvalid"]]},
        {"title": "Idempotency", "color": 5, "body": "Prompts are loaded once. A re-delivered correlationId returns the existing session without speaking again.",
         "rows": [["LoadPrompts", "Prompts", "CheckExisting", "AlreadyStarted"], [None, None, "ShapeExisting", "RespondExisting"]]},
        {"title": "Identify and open the case", "color": 3, "body": "Market row, CASE account lookup by VIN (in-car) or phone number (IVR), then the case with its mandatory fields (account name, origin, description, service type) and the CASE booking prerequisites of 2026-10-23 (country, account, asset, VIN or FIN, plate, mileage unit, assigned dealer, classification, primary contact). The model never sees the VIN or the number; an unknown caller is identified by spoken data in the dialogue.",
         "rows": [["LoadMarket", "IdentifyCustomer", "CreateCase"]]},
        {"title": "Session", "color": 3, "body": "Random session id, one row in ar_sessions per call, state starts at IDENTIFY. The greeting is the market's disclosure plus the ar_prompts greeting in the session language. Every start is logged to ar_events.",
         "rows": [["GenerateSessionId", "ComposeSession", "ComposeGreeting", "InsertSession", "LogStart"]]},
        {"title": "Greet or hand over", "color": 4, "body": "Backend down at start: the handover payload is built here and the greeting becomes the handover line.",
         "rows": [["BackendDown", "ShapeStart", "RespondStart"], [None, "BuildStartHandoff"]]},
    ],
    "ar-02-dialogue-turn": [
        {"title": "Receive", "color": 4, "body": "One turn per call: sessionId, transcript, ASR confidence.",
         "rows": [["ReceiveTurn", "ValidateTurn", "IsValid"], [None, None, "RespondInvalid"]]},
        {"title": "Load state", "color": 3, "body": "Prompts (goals, rules, spoken lines), the session row and the market row are the only state the model sees.",
         "rows": [["LoadPrompts", "Prompts", "LoadSession", "SessionFound", "LoadMarket"], [None, None, None, "RespondNotFound"]]},
        {"title": "Gates", "color": 5, "body": "YesNoGate: a deterministic yes or no in IDENTIFY (identified caller), CONSENT, DEALER, CONFIRM and CONFIRM_CANCEL, a spoken journey in NEED (cancel, reschedule, callback, self-service), a chosen service in SERVICES and a date in LOOKUP move the state before the model runs, so a small model cannot loop on a confirmed question or forget a tool call.",
         "rows": [["TurnFacts", "YesNoGate", "OnIdentityFailed", "OnNeedCancel", "OnRescheduleDate"], [None, None, "OnIdentityConfirmed", "OnNeedReschedule", "OnCancelInLookup"], [None, None, "OnConsentDeclined", "OnNeedCallback", "OnCancelConfirmed"], [None, None, "OnConsentGiven", "OnNeedSelfService", "OnSlotConfirmed"], [None, None, "OnDealerAgreed", "OnServicesChosen"]]},
        {"title": "Prefetch and prompt", "color": 5, "body": "The sub-workflow a gate implies runs here, deterministically: the appointment search on cancel or reschedule, the reservation on dealer agreed, the slot search on services chosen or a new date, the cancel, book or move on the caller's yes. Its result goes into the facts and the model reads it out instead of calling the tool. Then the system prompt is composed.",
         "rows": [["GateFacts", "Prefetch", "FetchAppointment", "WithAppointment", "AssembleFacts", "ComposeFacts", "PrepareTurn"], [None, None, "FetchReservation", "WithReservation"], [None, None, "FetchSlots", "WithSlots"], [None, None, "FetchCancel", "WithOutcome"], [None, None, "FetchBook"], [None, None, "FetchMove"]]},
        {"title": "Reason", "color": 5, "body": "The agent has nine tools (one per joint-scope step), Postgres memory and a structured parser with auto-fix.",
         "rows": [["DialogueAgent"], ["DialogueModel", "TurnMemory", "TurnOutputShape", "ParserFixerModel"], ["IdentifyCaller", "FindDealers", "CreateReservation", "FindSlots", "BookAppointment"], ["FindAppointment", "CancelAppointment", "MoveAppointment", "RequestCallback"]]},
        {"title": "Guards 1: gates", "color": 5, "body": "ClampOutput: only values the schema allows, only a state transition the table allows (illegal_transition), a journey intent sticks for the call. Then each rule is an If (the condition) and an Edit Fields (what it changes). Gates: a deterministic yes or no in IDENTIFY and CONSENT wins over the model.",
         "rows": [["ReloadSession", "ReadOutput", "ClampOutput", "GateDecline", "GateAdvance", "ScenarioDowngrade"], [None, None, None, "OnGateDecline", "OnGateAdvance", "OnScenarioDowngrade"]]},
        {"title": "Guards 2: caller, identification and outcomes", "color": 5, "body": "Caller asks for a person: handoff. Two failed identifications: handoff. OutcomeSync: a booking, move, cancellation or callback written by a tool forces its state. A booking, move or cancellation claim without a tool result is replaced by a question. Self-service instructions are read out verbatim.",
         "rows": [["CallerAsksForHuman", "IdentifyExhausted", "PendingSlot", "OutcomeSync", "FalseConfirmation", "FalseCancellation", "SelfServiceLine"],
                  ["OnCallerRequest", "OnIdentifyExhausted", None, "OnSyncBooked", "OnFalseConfirmation", "OnFalseCancellation", "OnSelfService"],
                  [None, None, None, "OnSyncMoved"], [None, None, None, "OnSyncCancelled"], [None, None, None, "OnSyncCallbackLogged"]]},
        {"title": "Guards 3: reply and end", "color": 5, "body": "Empty or over-long replies are fixed. A handoff always speaks the market's handover line. No end before a journey outcome (BOOKED, MOVED, CANCELLED, CALLBACK_LOGGED, SELF_SERVICE) or WRAPUP.",
         "rows": [["EmptyReply", "ReplyTooLong", "HandoffNormalise", "ForceHandoff", "EndBlocked", "GuardResult"], ["OnEmptyReply", "OnReplyTooLong", "OnHandoff", "OnForceHandoff", "OnEndBlocked"]]},
        {"title": "Handoff", "color": 3, "body": "A model failure answers with a fixed apology and PLATFORM_ERROR. Any handoff builds the Refer-To through AR-14.",
         "rows": [["NeedsHandoff", "BuildHandoff", "AttachHandoff"], ["HandleAgentError"]]},
        {"title": "Respond", "color": 4, "body": "Session update, event row, the reply the gateway speaks.",
         "rows": [["ComposeResponse", "UpdateSession", "LogTurn", "RespondTurn"]]},
    ],
    "ar-03-session-end": [
        {"title": "Receive", "color": 4, "body": "The gateway posts the end reason when the call drops or the transfer completes.",
         "rows": [["ReceiveEnd", "ValidateEnd", "IsValid"], [None, None, "RespondInvalid"]]},
        {"title": "Load state", "color": 3, "body": "Prompts for the spoken lines; the session row decides the outcome.",
         "rows": [["LoadPrompts", "Prompts", "LoadSession", "SessionFound"], [None, None, None, "RespondNotFound"]]},
        {"title": "Outcome", "color": 3, "body": "CaseOutcome in order: booked, moved or cancelled (Resolved, DealerBook sent the confirmation); callback logged (Delegated to XCCC); self-service (Resolved); ended in HANDOFF or a handoff end reason (Delegated); otherwise Closed.",
         "rows": [["CaseOutcome", "DecideOutcome", "CloseCase"], ["OnBooked"], ["OnMoved"], ["OnCancelled"], ["OnCallbackLogged"], ["OnSelfService"], ["OnHandedOff"], ["OnClosed"]]},
        {"title": "Close", "color": 4, "body": "Chat memory purged, session finalised, end logged. Nothing of the call is retained beyond ar_events.",
         "rows": [["PurgeMemory", "FinalizeSession", "LogEnd", "RespondEnd"]]},
    ],
    "ar-04-transfer-result": [
        {"title": "Receive", "color": 4, "body": "NOTIFY result of the REFER, posted by the gateway.",
         "rows": [["ReceiveTransferResult", "LoadPrompts", "Prompts", "LoadSession", "SessionFound"], [None, None, None, None, "RespondNotFound"]]},
        {"title": "Decide", "color": 5, "body": "TransferOutcome: NOTIFY 2xx releases; a first failure speaks and retries once; a second failure speaks and ends. One retry, never a loop.",
         "rows": [["TransferOutcome", "DecideTransferAction", "UpdateTransfer", "LogTransfer", "RespondTransfer"], ["OnReleased"], ["OnRetryOnce"], ["OnFinal"]]},
    ],
    "ar-05-health-and-handoff-lookup": [
        {"title": "Health", "color": 4, "body": "Probe for the gateway and the load balancer.",
         "rows": [["HealthCheck", "RespondHealth"]]},
        {"title": "Handoff lookup", "color": 4, "body": "Agent desktop context by correlationId: intent, reason, summary, case.",
         "rows": [["LookupHandoff", "LoadByCorrelation", "ShapeHandoffContext", "RespondHandoffContext"]]},
    ],
    "ar-10-identify-customer": [
        {"title": "Identify", "color": 3, "body": "CRM lookup by VIN and number. Returns the greeting name and the vehicle label, never the identifiers.",
         "rows": [["ToolInput", "CallCrm", "ShapeCustomer"], [None, None, "ReturnCrmError"]]},
    ],
    "ar-11-find-dealers": [
        {"title": "Find dealers", "color": 5, "body": "DealerBook dealer search by city; bookable tells the agent whether the dealer can be booked here.",
         "rows": [["ToolInput", "LoadSession", "CallDealerBookDealers", "ShapeDealers"], [None, None, None, "ReturnDealersError"]]},
    ],
    "ar-12-find-slots": [
        {"title": "Find slots", "color": 5, "body": "API V2 order: a missing reservation is created first from the session's dealer (CreateReservation); PATCH the selected services onto the reservation (new booking only), then GET timeslots for the reservation (or the existing booking's offer when rescheduling) in the market's window; at most maxSlotsOffered, labelled in the session language.",
         "rows": [["ToolInput", "LoadSession", "LoadMarket", "HasReservation", "SlotWindow", "NewBooking", "PatchServices", "CallDealerBookTimeslots"], [None, None, None, "CreateReservationFirst", None, None, None, "ReturnSlotsError"]]},
        {"title": "Remember what was offered", "color": 3, "body": "Selected services and offered slots are stored on the session so a booking or a move can only name one of them; the tool returns them by day and time, never by slotId.",
         "rows": [["ShapeSlots", "OfferSlots", "StoreOffered", "ReturnSlots"]]},
    ],
    "ar-13-book-appointment": [
        {"title": "Booking gate", "color": 5, "body": "Rules in order: a booking already exists; no reservation in hand; the slot was not offered in this call; the caller has not confirmed it (state CONFIRM with this slotId read back). Only Proceed books.",
         "rows": [["ToolInput", "LoadSession", "BookingGate", "BookingRequest"], [None, None, None, "ReplyAlreadyBooked"], [None, None, None, "ReplyNoReservation"], [None, None, None, "ReplySlotNotOffered"], [None, None, None, "ReplyNotConfirmed"]]},
        {"title": "Book", "color": 3, "body": "PATCH the timeslot and the fulfilment onto the reservation, then POST the booking with the idempotency key session:slot. The booking id and the six-letter reference code come from DealerBook, never from the model; a booking error becomes a retailer callback.",
         "rows": [["PatchReservation", "CallDealerBookBook", "StoreBooking", "ReturnBooking"], [None, "ReturnBookError"]]},
    ],
    "ar-14-build-handoff": [
        {"title": "Context", "color": 5, "body": "Session and market rows (return number, default language) and the short codes for the intent and the handoff reason from ar_codes.",
         "rows": [["ToolInput", "LoadSession", "LoadMarket", "LoadIntentCode", "LoadReasonCode"]]},
        {"title": "Return UUI", "color": 3, "body": "AssemblePayload builds version;correlationId;sessionId;intent;reason;language plus caseId. FitsBudget checks 64 bytes; DropCaseId removes the case id first and logs it. EncodeUui hex-encodes and builds the Refer-To, stored on the session for the lookup endpoint. UpdateCaseContext prepopulates the CASE case with the full context before the transfer (joint scope step 8).",
         "rows": [["AssemblePayload", "FitsBudget", "EncodeUui", "StoreHandoff", "UpdateCaseContext", "ReturnHandoff"], [None, "DropCaseId"]]},
    ],
    "ar-15-case-system": [
        {"title": "CASE case", "color": 3, "body": "Create at entry with the mandatory fields and the CASE booking prerequisites, update with the handover context, delegate to XCCC for a callback, close at the end. ScrubFreeText masks e-mail, phone, VIN and postcode in the free-text fields. Standard CRM case REST behind the mock.",
         "rows": [["ToolInput", "ScrubFreeText", "CallCaseSystem", "ShapeCase"], [None, None, None, "ReturnCaseError"]]},
    ],
    "ar-16-send-confirmation": [
        {"title": "Send confirmation", "color": 4, "body": "Retired 2026-10-22: DealerBook sends the confirmation via Cusmes in every journey. Kept for a market where the solution must send; not called by AR-03.",
         "rows": [["ToolInput", "CallComms", "ShapeComms"], [None, None, "ReturnCommsError"]]},
    ],
    "ar-17-log-event": [
        {"title": "Log event", "color": 5, "body": "One structured row per step in ar_events. No audio, no transcript.",
         "rows": [["ToolInput", "InsertEvent", "ReturnOk"]]},
    ],
    "ar-20-mock-crm": [
        {"title": "Account lookup", "color": 7, "body": "Synthetic customers in ar_mock_customers: by VIN (in-car), then by phone number (IVR). Lab only; the real build is the CASE account search.",
         "rows": [["ReceiveLookup", "LookupByVin", "FoundByVin", "ShapeFixture", "RespondCustomer"], [None, None, "LookupByPhone"]]},
        {"title": "Spoken identification", "color": 7, "body": "Registration plate (normalised) plus surname or postcode: the placeholder rule until the identification rule is agreed (question 20).",
         "rows": [["ReceiveIdentify", "LookupByPlate", "MatchesIdentity", "ShapeIdentified", "RespondIdentify"], [None, None, None, "ShapeNotFound"]]},
    ],
    "ar-21-mock-dealerbook": [
        {"title": "Dealers", "color": 7, "body": "Rows from ar_mock_dealers, narrowed by market, dealerId and city (accent-insensitive).",
         "rows": [["ReceiveDealers", "LoadDealers", "AllDealers", "FilterDealers", "RespondDealers"]]},
        {"title": "Reservation", "color": 7, "body": "POST reservations: vehicle plus dealer give a reservation id that carries the dealer; a dealer without online booking answers dealer_not_dealerbook_enabled.",
         "rows": [["ReceiveReservation", "LoadReservationDealer", "HashReservation", "ComputeReservation", "RespondReservation"]]},
        {"title": "Services and fulfilments", "color": 7, "body": "GET services and GET fulfilments of a reservation (POST on the lab): the catalogues, 404 for an unknown reservation id.",
         "rows": [["ReceiveServices", "LoadServicesDealer", "ComputeServices", "RespondServices"], ["ReceiveFulfilments", "LoadFulfilmentsDealer", "ComputeFulfilments", "RespondFulfilments"]]},
        {"title": "Patch", "color": 7, "body": "PATCH reservations: the selected services, fulfilment or timeslot. Acknowledged with what was patched, not stored; 404 for an unknown reservation id.",
         "rows": [["ReceivePatch", "LoadPatchDealer", "ComputePatch", "RespondPatch"]]},
        {"title": "Timeslots", "color": 7, "body": "GET timeslots in fromDateTime..toDateTime; dealer from the reservation id or the request; deterministic from dealerId and date so tests repeat (the one Code node kept in the mocks).",
         "rows": [["ReceiveTimeslots", "LoadDealer", "GenerateSlots", "RespondSlots"]]},
        {"title": "Book", "color": 7, "body": "POST bookings: required fields, then the FULL probe (409), then the DealerBook booking id and the six-letter reference code hashed from the idempotency key. Bookings persist in ar_mock_bookings.",
         "rows": [["ReceiveBook", "BookInvalid", "SlotFull", "HashKey", "ComputeBooking", "IsConflict", "UpsertBooking", "RespondBooked"], [None, "ReplyInvalid", "ReplyConflict", None, None, "RespondConflict"]]},
        {"title": "Bookings lookup", "color": 7, "body": "GET bookings by customerId, chassisNumber, offerId or status (POST on the lab); a referenceCode narrows to the spoken code.",
         "rows": [["ReceiveSearch", "LoadAllBookings", "AllBookings", "FilterBookings", "RespondBookings"]]},
        {"title": "Cancel", "color": 7, "body": "POST bookings/{bookingId}: marks the booking cancelled.",
         "rows": [["ReceiveCancel", "CancelBooking", "RespondCancelled"]]},
        {"title": "Reschedule", "color": 7, "body": "POST bookings/{bookingId}/reschedule: the booking keeps its id and reference code and gets the new timeslot; movedFrom records the old one.",
         "rows": [["ReceiveReschedule", "LoadBookingToMove", "BookingFound", "RescheduleBooking", "RespondRescheduled"], [None, None, None, "RespondNoBooking"]]},
    ],
    "ar-22-mock-case-system": [
        {"title": "Mock CASE case", "color": 7, "body": "Case number from a hash of the correlationId; the existing row is loaded so an update keeps what it does not name; rows in ar_mock_cases.",
         "rows": [["ReceiveCaseOp", "HashCorrelation", "LoadCase", "ComputeCase", "UpsertCase", "RespondCase"]]},
        {"title": "Appointment search", "color": 7, "body": "CASE searches account cases, VIN or booking number; the lab forwards to the booking store.",
         "rows": [["ReceiveAppointments", "ForwardToBookings", "ShapeAppointments", "RespondAppointments"]]},
    ],
    "ar-23-mock-comms": [
        {"title": "Mock comms", "color": 7, "body": "customerRef and text required; returns a message id, sends nothing.",
         "rows": [["ReceiveSend", "HasRequired", "AcceptMessage", "RespondSend"], [None, None, "RejectMessage"]]},
    ],
    "ar-30-error-workflow": [
        {"title": "Error trigger", "color": 3, "body": "Every AR-* failure lands in ar_events with workflow, node and message. Assign in Workflow Settings.",
         "rows": [["OnWorkflowError", "ShapeError", "InsertErrorEvent"]]},
    ],
    "ar-90-seed-config": [
        {"title": "Seed", "color": 6, "body": "Rows live in the Edit Fields nodes as JSON, plus the mock rows of the request body (test identities, gitignored); Split Out makes one item per row; upserts are keyed on the natural key. Run once per environment; safe to re-run (re-seeding restores the two fixture appointments).",
         "rows": [["ReceiveSeed", "MarketRows", "SplitMarkets", "UpsertMarket"], [None, "CodeRows", "SplitCodes", "UpsertCode"], [None, "PromptRows", "SplitPrompts", "UpsertPrompt"],
                  [None, "MockCustomerRows", "SplitMockCustomers", "UpsertMockCustomer"], [None, "MockDealerRows", "SplitMockDealers", "UpsertMockDealer"],
                  [None, "MockBookingRows", "SplitMockBookings", "UpsertMockBooking", "RespondSeeded"]]},
    ],
    "ar-18-identify-caller": [
        {"title": "Identify by spoken data", "color": 3, "body": "Plate plus surname or postcode against the CASE account (placeholder rule until question 20 is agreed). A match stores the customer on the session and account, asset, VIN or FIN, plate and dealer on the case; a miss counts an attempt.",
         "rows": [["ToolInput", "LoadSession", "CallIdentify", "Identified", "StoreIdentity", "UpdateCaseAccount", "ReturnIdentified"], [None, None, None, "ReturnIdentifyError", "CountAttempt", "ReturnNotIdentified"]]},
    ],
    "ar-19-create-reservation": [
        {"title": "Reservation, services, fulfilments", "color": 3, "body": "API V2 order: POST reservations for the session's vehicle at the agreed dealer, then GET services and GET fulfilments for it. A dealer without online booking answers with no reservation and the callback or self-service note.",
         "rows": [["ToolInput", "LoadSession", "CallDealerBookReservation", "ReservationOk", "CallDealerBookServices", "CallDealerBookFulfilments"], [None, None, "ReturnReservationError", "ShapeNoReservation"]]},
        {"title": "Store and return", "color": 3, "body": "Reservation, dealer, services and fulfilments are stored on the session; selected services and offered slots are reset.",
         "rows": [["ShapeReservation", "StoreReservation", "ReturnReservation"]]},
    ],
    "ar-24-find-appointment": [
        {"title": "Find the appointment", "color": 5, "body": "DealerBook bookings by the session's customer id and chassis number, status booked; a spoken reference code narrows. Exactly one match becomes the appointment in hand on the session, with its offer id for the slot search.",
         "rows": [["ToolInput", "LoadSession", "CallDealerBookBookings", "ShapeAppointments", "ExactlyOne", "StoreAppointment", "ReturnAppointments"], [None, None, None, "ReturnAppointmentsError"]]},
    ],
    "ar-25-cancel-appointment": [
        {"title": "Cancel gate", "color": 5, "body": "Rules in order: already cancelled; no appointment in hand; a different reference; not confirmed (state CONFIRM_CANCEL from the previous turn). Only Proceed cancels.",
         "rows": [["ToolInput", "LoadSession", "CancelGate"], [None, None, "ReplyAlreadyCancelled"], [None, None, "ReplyNotInHand"], [None, None, "ReplyWrongAppointment"], [None, None, "ReplyNotConfirmed"]]},
        {"title": "Cancel", "color": 3, "body": "POST cancel on the DealerBook booking id; cancelledAt and state CANCELLED on the session. DealerBook confirms to the customer.",
         "rows": [["CallDealerBookCancel", "StoreCancelled", "ReturnCancelled"], ["ReturnCancelError"]]},
    ],
    "ar-26-move-appointment": [
        {"title": "Move gate", "color": 5, "body": "Rules in order: already moved; no appointment in hand; the slot was not offered in this call; not confirmed (state CONFIRM with this slotId read back). Only Proceed moves.",
         "rows": [["ToolInput", "LoadSession", "MoveGate", "MoveRequest"], [None, None, None, "ReplyAlreadyMoved"], [None, None, None, "ReplyNotInHand"], [None, None, None, "ReplySlotNotOffered"], [None, None, None, "ReplyNotConfirmed"]]},
        {"title": "Move", "color": 3, "body": "POST reschedule on the DealerBook booking id keeps the id and the reference code; movedAt, the new slot and state MOVED on the session.",
         "rows": [["CallDealerBookReschedule", "StoreMoved", "ReturnMoved"], ["ReturnMoveError"]]},
    ],
    "ar-27-request-callback": [
        {"title": "Callback gate", "color": 5, "body": "Already logged, or not yet confirmed (state CALLBACK from the previous turn). Only Proceed delegates.",
         "rows": [["ToolInput", "LoadSession", "CallbackGate"], [None, None, "ReplyAlreadyLogged"], [None, None, "ReplyNotConfirmed"]]},
        {"title": "Delegate", "color": 3, "body": "The CASE case is delegated to XCCC with the callback note; callbackAt and state CALLBACK_LOGGED on the session. No DealerBook.",
         "rows": [["DelegateCase", "DelegateOk", "StoreCallback", "ReturnLogged"], [None, None, "ReturnDelegateError"]]},
    ],
    "ar-50-twilio-voice": [
        {"title": "Twilio voice entry", "color": 4, "body": "Twilio posts the inbound call. The config rows and the first GB fixture customer shape the synthetic UUI; with an OpenAI project the TwiML dials the SIP ingress (referUrl AR-53, action AR-54), without it the caller hears the lab line.",
         "rows": [["ReceiveTwilioCall", "LoadProofConfig", "ProofRows", "ProofConfig", "PickCustomer", "ShapeUui", "HasOpenAi", "TwimlDial"], [None, None, None, None, None, None, None, "TwimlLabLine"]]},
        {"title": "Respond and log", "color": 3, "body": "TwiML as text/xml, then the call is logged with the UUI it was given.",
         "rows": [["RespondTwiml", "LogTwilioCall"]]},
        {"title": "Config admin", "color": 6, "body": "POST ar/proof/config {rows: [{key, value}]} with the gateway bearer: upsert on key. Values come from the seed script, never from a node field.",
         "rows": [["ReceiveProofConfig", "SplitRows", "UpsertProofConfig", "ConfigRows", "RespondConfig"]]},
    ],
    "ar-51-openai-call-in": [
        {"title": "Receive and acknowledge", "color": 4, "body": "Only realtime.call.incoming is handled; everything else is acknowledged and ignored. The 200 goes out before any work.",
         "rows": [["ReceiveCallIncoming", "IsCallIncoming", "RespondAck"], [None, None, "RespondIgnored"]]},
        {"title": "Session start", "color": 5, "body": "The UUI comes out of the INVITE headers (User-to-User, hex). AR-01 starts the n8n session as the gateway would; a failed start rejects the call with 486.",
         "rows": [["ShapeCall", "LoadProofConfig", "ProofRows", "ProofConfig", "StartSession", "RejectCall", "LogStartFailed"]]},
        {"title": "Accept", "color": 3, "body": "The accept body carries the greeting, the session id, the proof prompt and the MCP tool server on n8n. The sideband sidecar (scripts/ar-sideband.mjs) is told the call id and sends the greeting response.create; a sidecar that is down does not block the store. The call id is stored on the session for the REFER.",
         "rows": [["ComposeAccept", "AcceptCall", "NotifySideband", "StoreCallId", "LogAccept"], [None, None, "LogAcceptFailed"]]},
    ],
    "ar-52-proof-tools": [
        {"title": "MCP server", "color": 3, "body": "Bearer-protected MCP trigger at /mcp/ar-proof-tools. The tools are the demo sub-workflows plus ConfirmSlot (the CONFIRM step as a tool); RequestAgent is the REFER path.",
         "rows": [["ProofTools"], ["FindDealers", "FindSlots", "ConfirmSlot", "BookAppointment", "RequestAgent", "EndCall"]]},
    ],
    "ar-57-end-call": [
        {"title": "End", "color": 5, "body": "State ENDED and the reason on the session; the closing line from the market row. The sidecar hangs up after the goodbye audio stops.",
         "rows": [["ToolInput", "LoadSession", "LoadMarket", "StoreEnded", "ReturnEnd"]]},
    ],
    "ar-56-confirm-slot": [
        {"title": "Confirm", "color": 5, "body": "The picked slot must be one FindSlots offered in this call. State CONFIRM and the slotId go on the session; BookingGate books the same slotId in the next turn.",
         "rows": [["ToolInput", "LoadSession", "IsOffered", "PickSlot", "StoreConfirm", "ReturnConfirm"], [None, None, None, "ReturnNotOffered"]]},
    ],
    "ar-55-refer-call": [
        {"title": "Refer", "color": 3, "body": "AR-14 builds the UUI; the refer goes to the OpenAI call with the UUI as a Refer-To query parameter. No call id on the session means the call was never accepted through AR-51.",
         "rows": [["ToolInput", "LoadSession", "BuildHandoff", "LoadProofConfig", "ProofRows", "ProofConfig", "ReferRequest", "HasCallId"], [None, None, None, None, None, None, None, "CallRefer"]]},
        {"title": "Outcome", "color": 5, "body": "Sent or failed is logged with the target; the tool answer tells the model what to say.",
         "rows": [["LogReferSent", "ReturnReferred"], ["LogReferFailed", "ReturnReferFailed"]]},
    ],
    "ar-53-twilio-refer": [
        {"title": "Refer received", "color": 4, "body": "ReferTransferTarget is the Refer-To Twilio received. The UUI hex is decoded, the session gets refer_received, the event carries the target byte-exact.",
         "rows": [["ReceiveRefer", "LoadProofConfig", "ProofRows", "ProofConfig", "ShapeRefer", "StoreReferReceived", "LogReferReceived", "HasHuman"]]},
        {"title": "Dial the human", "color": 3, "body": "With humanNumber in the config the caller is bridged to the phone; without it the line says whether the context was decoded and ends.",
         "rows": [["TwimlDialHuman", "RespondReferTwiml"], ["TwimlNoHuman"]]},
    ],
    "ar-54-twilio-done": [
        {"title": "Dial done", "color": 5, "body": "The Dial action callback: status, SIP response code and duration into ar_events, then hang up.",
         "rows": [["ReceiveDone", "ShapeDone", "LogDone", "RespondHangup"]]},
    ],
}


if __name__ == "__main__":
    learn_existing_ids()
    # names must be known before references are rendered: declare all, then dump all
    built = [b() for b in BUILDERS]
    # second pass: re-render references now that every name is registered
    built = [b() for b in BUILDERS]
    for w in built:
        print(os.path.basename(w.dump()), "nodes:", len(w.nodes), "id:", IDS.get("wf_" + w.key, "PENDING"))
    json.dump(IDS, open(IDS_PATH, "w"), indent=2)
