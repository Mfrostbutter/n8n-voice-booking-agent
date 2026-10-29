#!/usr/bin/env python3
"""Deploy the AR workflow set to an n8n instance in dependency order, two passes.

Usage: N8N_URL=https://n8n.example N8N_API_KEY=... python3 build/deploy.py [--only key,key]
Reads the ids file build/setup.py wrote (lab-ids.json, or AR_IDS_FILE).
Pass 1 creates by name and learns ids; gen.py is re-run so sub-workflow references
resolve; pass 2 updates in place. Every workflow, sub-workflows included, is
published through POST /workflows/{id}/publish (n8n 2.35+). Every workflow is
placed where its JSON says (project, folder, tags) through
scripts/organize_workflows.py, and the deploy stops when the project cannot
reach a credential the nodes use.
"""
import json, os, subprocess, sys, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("AR_OUT_DIR") or os.path.dirname(HERE)  # where gen.py wrote the set
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import organize_workflows as org  # noqa: E402

BASE = (os.environ.get("N8N_URL") or os.environ.get("N8N_LAB_URL") or "http://localhost:5678").rstrip("/")
KEY = os.environ.get("N8N_API_KEY") or os.environ.get("N8N_LOCAL_WORKFLOW_LAB_API_KEY") or sys.exit("N8N_API_KEY not set")
IDS_PATH = os.environ.get("AR_IDS_FILE") or os.path.join(OUT, "lab-ids.json")
os.path.exists(IDS_PATH) or sys.exit(f"{IDS_PATH} missing: run build/setup.py first")
UNPUBLISHED = []
TARGET = {"env": "lab", "url": BASE, "base": BASE + "/api/v1", "key": KEY}


def credentials_or_empty():
    """A member key may not list credentials (403). In a personal project the scope checks are no-ops, so continue without the index."""
    code, _ = org.api(TARGET, "GET", "/credentials?limit=1")
    if code == 403:
        print("credential listing not permitted for this key: scope checks skipped")
        return {}
    return org.credentials_index(TARGET)


CREDS = credentials_or_empty()
TABLES = org.data_tables_index(TARGET)
ORDER = ["ar-17-log-event","ar-20-mock-crm","ar-21-mock-dealerbook","ar-22-mock-case-system","ar-23-mock-comms",
         "ar-10-identify-customer","ar-11-find-dealers","ar-12-find-slots","ar-13-book-appointment","ar-14-build-handoff",
         "ar-15-case-system","ar-16-send-confirmation","ar-18-identify-caller","ar-19-create-reservation","ar-24-find-appointment",
         "ar-25-cancel-appointment","ar-26-move-appointment","ar-27-request-callback","ar-01-session-start","ar-02-dialogue-turn","ar-03-session-end",
         "ar-04-transfer-result","ar-05-health-and-handoff-lookup","ar-30-error-workflow","ar-90-seed-config",
         "ar-56-confirm-slot","ar-57-end-call","ar-55-refer-call","ar-50-twilio-voice","ar-51-openai-call-in","ar-52-proof-tools","ar-53-twilio-refer","ar-54-twilio-done"]
only = None
if "--only" in sys.argv:
    only = sys.argv[sys.argv.index("--only") + 1].split(",")


def call(method, path, body=None):
    req = urllib.request.Request(f"{BASE}/api/v1{path}", data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("X-N8N-API-KEY", KEY); req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def existing_by_name():
    """name -> workflow, limited to the target project so a same-named set in
    another project on a shared instance is never updated by mistake."""
    first = json.load(open(os.path.join(OUT, ORDER[0] + ".json"))) if os.path.exists(os.path.join(OUT, ORDER[0] + ".json")) else None
    project = org.resolve_project(TARGET, (first or {}).get("project") or "personal")
    scope = "" if project["id"] == org.PERSONAL else f"&projectId={project['id']}"
    out = {}
    cursor = None
    while True:
        s, r = call("GET", "/workflows?limit=250" + scope + (f"&cursor={cursor}" if cursor else ""))
        for w in r.get("data", []):
            out[w["name"]] = w
        cursor = r.get("nextCursor")
        if not cursor:
            break
    return out


def deploy_one(key, have):
    path = os.path.join(OUT, key + ".json")
    wf = json.load(open(path))
    payload = {"name": wf["name"], "nodes": wf["nodes"], "connections": wf["connections"], "settings": wf.get("settings", {"executionOrder": "v1"})}
    # The recorded id first, then the name: a renamed workflow updates in place instead of leaving an orphan.
    by_id = {w["id"]: w for w in have.values()}
    cur = by_id.get(wf.get("id") or "") or have.get(wf["name"])
    if cur:
        wid = cur["id"]
        if cur.get("active"):
            call("POST", f"/workflows/{wid}/deactivate")
        s, r = call("PUT", f"/workflows/{wid}", payload)
        action = f"updated({s})"
    else:
        s, r = call("POST", "/workflows", payload)
        if s not in (200, 201):
            print(f"  {key}: create failed {s} {r}"); return None
        wid = r["id"]; action = "created"
    # Project, folder and tags from the JSON; the folder PUT writes a version, so it
    # runs before the activate or publish below.
    placed = org.place_workflow(TARGET, wid, wf, creds=CREDS, body=payload, tables=TABLES)
    place = f"-> {placed['project']} / {placed['folder']}{' (transferred)' if placed['transfer'] else ''}"
    s, r = call("POST", f"/workflows/{wid}/publish", {})
    if s == 200:
        act = "published"
        if key in UNPUBLISHED:
            UNPUBLISHED.remove(key)
    else:
        act = f"publish-failed({s}) {str(r)[:160]}"
        key in UNPUBLISHED or UNPUBLISHED.append(key)
    if wf.get("id") != wid:
        wf = {"id": wid, **{k: v for k, v in wf.items() if k != "id"}}
        json.dump(wf, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False); open(path, "a").write("\n")
    print(f"  {key}: {action} {wid} {act} {place}")
    return wid


def run_pass(n):
    print(f"pass {n}")
    subprocess.run([sys.executable, os.path.join(HERE, "gen.py")], check=True, stdout=subprocess.DEVNULL)
    have = existing_by_name()
    for key in ORDER:
        if only and key not in only:
            continue
        deploy_one(key, have)


def move_credentials():
    """Credentials only the AR set uses move into its project ahead of the workflows."""
    subprocess.run([sys.executable, os.path.join(HERE, "gen.py")], check=True, stdout=subprocess.DEVNULL)
    batch = [(wf.get("id"), wf) for _, wf in org.load_files(OUT)]
    project = org.resolve_project(TARGET, batch[0][1]["project"])
    moved, bad = org.move_batch_credentials(TARGET, batch, project, CREDS)
    for m in moved:
        print(f"  credential '{m}' moved into '{project['name']}'")
    if bad:
        sys.exit(f"credential scope: '{project['name']}' cannot take " + "; ".join(bad))


move_credentials()
run_pass(1)
if not only:
    run_pass(2)
subprocess.run([sys.executable, os.path.join(HERE, "gen.py")], check=True, stdout=subprocess.DEVNULL)
print("ids:", json.dumps({k: v for k, v in json.load(open(IDS_PATH)).items() if k.startswith("wf_")}, indent=1))
if UNPUBLISHED:
    sys.exit("not published after the last pass: " + ", ".join(UNPUBLISHED))
print("all deployed workflows published")
