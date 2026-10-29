#!/usr/bin/env python3
"""Prepare an n8n instance for the AR workflow set and write the ids file.

Usage: N8N_URL=https://n8n.example N8N_API_KEY=... AR_GATEWAY_TOKEN=... \\
       AR_PG_HOST=... AR_PG_DATABASE=... AR_PG_USER=... AR_PG_PASSWORD=... \\
       AR_MODEL_API_KEY=... AR_MODEL_BASE_URL=https://.../v1 AR_MODEL=<model id> \\
       python3 build/setup.py [--dry-run]

Creates, in the project AR_PROJECT (default "Voice Booking"; "personal" for an
instance without team projects): the three credentials the workflows use
(gateway bearer, chat-memory Postgres, OpenAI-compatible model), the six Data
Tables with the columns the generator declares, and writes the ids file
(lab-ids.json, or AR_IDS_FILE) that build/gen.py and build/deploy.py read.
Re-running is safe: anything that already exists by name in the project is
reused, never duplicated. Credential values are sent to n8n once and never
written to disk.

Environment, all optional unless marked:
  N8N_URL, N8N_API_KEY           required; the instance and an API key with
                                 workflow, credential, project and data-table scopes
  AR_GATEWAY_TOKEN               required; bearer the voice gateway presents
  AR_PG_HOST, AR_PG_DATABASE,    required; Postgres for chat memory
  AR_PG_USER, AR_PG_PASSWORD
  AR_PG_PORT (5432), AR_PG_SSL (disable | allow | require, default disable)
  AR_MODEL_API_KEY               required; key for the OpenAI-compatible endpoint
  AR_MODEL_BASE_URL              endpoint base URL ending in /v1; stored on the
                                 credential and set on the model nodes
  AR_MODEL                       model id the endpoint expects
  AR_PROJECT, AR_FOLDER          placement; defaults "Voice Booking", "Voice Booking Agent"
  AR_SELF_URL                    URL n8n uses to call its own mock webhooks;
                                 default N8N_URL
  AR_CRED_PREFIX                 credential name prefix, default "AR"
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(OUT))
MEMORY = os.environ.get("AR_MEMORY", "postgres")  # "simple" skips the Postgres credential (in-process memory on a test host)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import organize_workflows as org  # noqa: E402

DRY = "--dry-run" in sys.argv
TABLES_ONLY = "--tables-only" in sys.argv  # add new tables or columns to an existing instance; credentials and ids untouched
CREDS_ONLY = "--creds-only" in sys.argv  # create missing credentials only; tables and the other ids untouched
IDS_PATH = os.environ.get("AR_IDS_FILE") or os.path.join(OUT, "lab-ids.json")


def need(name):
    v = os.environ.get(name)
    if not v and not DRY:
        sys.exit(f"{name} not set (see the docstring of build/setup.py)")
    return v or ""


BASE = need("N8N_URL").rstrip("/")
KEY = need("N8N_API_KEY")
TARGET = {"env": "target", "url": BASE, "base": BASE + "/api/v1", "key": KEY}
PROJECT = os.environ.get("AR_PROJECT", "Voice Booking")
FOLDER = os.environ.get("AR_FOLDER", "Voice Booking Agent")
PREFIX = os.environ.get("AR_CRED_PREFIX", "AR")
MODEL = os.environ.get("AR_MODEL", "")
MODEL_URL = os.environ.get("AR_MODEL_BASE_URL", "")
SELF_URL = (os.environ.get("AR_SELF_URL") or BASE).rstrip("/")

# Column types come from the generator so the tables and the nodes agree.
sys.path.insert(0, HERE)
os.environ.setdefault("AR_IDS_FILE", IDS_PATH)
import gen  # noqa: E402

CREDENTIALS = [
    ("credGatewayHeader", f"{PREFIX} gateway bearer", "httpHeaderAuth",
     lambda: {"name": "Authorization", "value": "Bearer " + need("AR_GATEWAY_TOKEN")}),
    ("credPostgres", f"{PREFIX} chat memory Postgres", "postgres",
     lambda: {"host": need("AR_PG_HOST"), "database": need("AR_PG_DATABASE"), "user": need("AR_PG_USER"),
              "password": need("AR_PG_PASSWORD"), "port": int(os.environ.get("AR_PG_PORT", "5432")),
              "ssl": os.environ.get("AR_PG_SSL", "disable")}),
    ("credLmStudio", f"{PREFIX} model endpoint", "openAiApi",
     lambda: {"apiKey": need("AR_MODEL_API_KEY"), **({"url": MODEL_URL} if MODEL_URL else {})}),
    # Twilio transfer proof (AR-50 to AR-55). Created only when the value is in the environment.
    ("credOpenAi", f"{PREFIX} OpenAI Realtime", "httpHeaderAuth",
     lambda: {"name": "Authorization", "value": "Bearer " + need("AR_OPENAI_API_KEY")}),
    ("credMcpBearer", f"{PREFIX} MCP bearer", "httpBearerAuth",
     lambda: {"token": need("AR_PROOF_MCP_TOKEN")}),
    # Sideband sidecar off the Mac: AR-51 presents this bearer on /attach.
    ("credSidebandBearer", f"{PREFIX} sideband bearer", "httpHeaderAuth",
     lambda: {"name": "Authorization", "value": "Bearer " + need("AR_SIDEBAND_TOKEN")}),
]
OPTIONAL = {"credOpenAi": "AR_OPENAI_API_KEY", "credMcpBearer": "AR_PROOF_MCP_TOKEN", "credSidebandBearer": "AR_SIDEBAND_TOKEN"}


def project_record():
    if PROJECT.lower() == org.PERSONAL:
        return {"id": org.PERSONAL, "name": org.PERSONAL, "type": org.PERSONAL}
    if DRY:
        hits = [p for p in org.projects(TARGET) if p.get("type") == "team" and p.get("name") == PROJECT]
        print(f"project '{PROJECT}': {'exists' if hits else 'would be created'}")
        return hits[0] if hits else {"id": "DRY", "name": PROJECT, "type": "team"}
    rec, created = org.ensure_project(TARGET, PROJECT)
    print(f"project '{PROJECT}': {'created' if created else 'exists'} ({rec['id']})")
    return rec


def in_project(shared, project):
    # `shared` entries carry the project as `id`; a personal target accepts any.
    if project["id"] == org.PERSONAL:
        return True
    return any(s.get("id") == project["id"] for s in shared or [])


def ensure_credentials(project, ids):
    have = {}
    code, _ = org.api(TARGET, "GET", "/credentials?limit=1")
    if code == 403:
        print("credential listing not permitted for this key: existing ids come from the ids file")
    else:
        for c in org.credentials_index(TARGET).values():
            if in_project(c.get("shared"), project):
                have.setdefault(c["name"], c)
    for key, name, ctype, data in CREDENTIALS:
        if CREDS_ONLY and key not in OPTIONAL and ids.get(key):
            print(f"credential '{name}': kept ({ids[key]})"); continue
        if key == "credPostgres" and MEMORY == "simple":
            print(f"credential '{name}': skipped (AR_MEMORY=simple)"); ids.pop(key, None); continue
        cur = have.get(name)
        if not cur and code == 403 and ids.get(key):
            print(f"credential '{name}': kept from the ids file ({ids[key]})"); continue
        if cur:
            print(f"credential '{name}': exists ({cur['id']})")
            ids[key] = cur["id"]; ids[key + "Name"] = name
            continue
        if key in OPTIONAL and not os.environ.get(OPTIONAL[key]):
            print(f"credential '{name}': skipped ({OPTIONAL[key]} not in the environment)"); continue
        if DRY:
            print(f"credential '{name}' ({ctype}): would be created"); continue
        body = {"name": name, "type": ctype, "data": data()}
        if project["id"] != org.PERSONAL:
            body["projectId"] = project["id"]
        code, resp = org.api(TARGET, "POST", "/credentials", body)
        if code not in (200, 201):
            sys.exit(f"credential '{name}' create FAILED HTTP {code}: {resp.get('error')}")
        print(f"credential '{name}': created ({resp['id']})")
        ids[key] = resp["id"]; ids[key + "Name"] = name


def ensure_tables(project, ids):
    have = {}
    for t in org.data_tables_index(TARGET).values():
        pid = t.get("projectId") or (t.get("project") or {}).get("id")
        if project["id"] == org.PERSONAL or pid == project["id"]:
            have.setdefault(t["name"], t)
    for name, cols in gen.TABLE_COLS.items():
        cur = have.get(name)
        if cur:
            ids["dt_" + name] = cur["id"]
            code, detail = org.api(TARGET, "GET", f"/data-tables/{cur['id']}")
            existing = {c["name"] for c in (detail.get("columns") or [])} if code == 200 else set(cols)
            missing = [c for c in cols if c not in existing]
            for c in missing:
                if DRY:
                    print(f"data table '{name}': would add column {c}"); continue
                code, resp = org.api(TARGET, "POST", f"/data-tables/{cur['id']}/columns", {"name": c, "type": gen.coltype(c)})
                if code not in (200, 201):
                    sys.exit(f"data table '{name}' add column '{c}' FAILED HTTP {code}: {resp.get('error') or resp.get('message')}")
            print(f"data table '{name}': exists ({cur['id']})" + (f", added columns {missing}" if missing and not DRY else ""))
            continue
        if DRY:
            print(f"data table '{name}': would be created with {len(cols)} columns"); continue
        body = {"name": name, "columns": [{"name": c, "type": gen.coltype(c)} for c in cols]}
        if project["id"] != org.PERSONAL:
            body["projectId"] = project["id"]
        code, resp = org.api(TARGET, "POST", "/data-tables", body)
        if code not in (200, 201):
            sys.exit(f"data table '{name}' create FAILED HTTP {code}: {resp.get('error')}")
        print(f"data table '{name}': created ({resp['id']})")
        ids["dt_" + name] = resp["id"]


def main():
    ids = json.load(open(IDS_PATH)) if os.path.exists(IDS_PATH) else {}
    project = project_record()
    if not TABLES_ONLY:
        ensure_credentials(project, ids)
    if not CREDS_ONLY:
        ensure_tables(project, ids)
    if not TABLES_ONLY and not CREDS_ONLY:
        ids.update({"project": PROJECT, "folder": FOLDER, "selfUrl": SELF_URL})
        if MODEL:
            ids["model"] = MODEL
        ids["modelBaseUrl"] = MODEL_URL
        ids["memory"] = MEMORY
    if DRY:
        print("dry run: ids file not written"); return
    json.dump(ids, open(IDS_PATH, "w"), indent=2); open(IDS_PATH, "a").write("\n")
    print(f"ids file written: {IDS_PATH}")
    print("next: python3 build/deploy.py")


main()
