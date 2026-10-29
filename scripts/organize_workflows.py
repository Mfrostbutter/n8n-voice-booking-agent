#!/usr/bin/env python3
"""Place workflows and credentials on an n8n instance: project, folder, tags.
Standard library only. Importable (deploy_workflow.py uses it) and a CLI.

  organize.sh audit [env]                                 what sits where, and every violation
  organize.sh place <workflow.json|dir> [env] [--dry-run]  project, folder and tags from the JSON
  organize.sh credentials <name>... --to <project> [--env <env>] [--force]
                                                          transfer credentials into a project
  organize.sh tables <name>... --to <project> [--env <env>] [--no-rows]
                                                          copy data tables into a project (no transfer exists)
  organize.sh project <name> [env]                        create a team project if it is missing

A workflow JSON carries its own placement, next to "tags":

  "tags": ["acme", "order-intake", "api"],
  "project": "Acme Corp",          # a team project by name, or "personal"
  "folder": "Order intake/API"     # a path; every segment is created on demand

Defaults to dev. Every write names the environment it hit. "personal" is the
calling key's own project: n8n's public API cannot name it, so a personal
workflow is never transferred and its credential scope is not checked.
"""
import fnmatch
import glob
import json
import os
import sys
import urllib.error
import urllib.request

import secrets_env
from secrets_env import fail

PERSONAL = "personal"


# ----------------------------------------------------------------- transport

def api(target, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(target["base"] + path, data=data, method=method)
    req.add_header("X-N8N-API-KEY", target["key"])
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "n8n-care-kit")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read().decode()
            return r.status, (json.loads(t) if t else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:500]}
    except OSError as e:
        return 0, {"error": str(e)}


def api_all(target, path):
    """Every page of a cursor-paginated list endpoint."""
    out, cursor = [], None
    while True:
        sep = "&" if "?" in path else "?"
        code, resp = api(target, "GET", path + (f"{sep}cursor={cursor}" if cursor else ""))
        if code != 200:
            fail(f"GET {path} -> HTTP {code}: {resp.get('error')}", 1)
        out.extend(resp.get("data", []))
        cursor = resp.get("nextCursor")
        if not cursor:
            return out


# ------------------------------------------------------------------ projects

def projects(target):
    return api_all(target, "/projects?limit=100")


def resolve_project(target, name):
    """Project record for a name. "personal" is a sentinel the folder API accepts
    as a path segment; it has no id and no transfer."""
    if not name:
        fail('workflow has no "project". Add "project": "<team project>" or "personal".', 1)
    if name.lower() == PERSONAL:
        return {"id": PERSONAL, "name": PERSONAL, "type": PERSONAL}
    hits = [p for p in projects(target) if p.get("type") == "team" and p.get("name") == name]
    if not hits:
        fail(f'project "{name}" does not exist on {target["url"]}. '
             f'Create it: organize.sh project "{name}" {target.get("env") or "dev"} '
             "(team projects need a licensed instance), or set \"project\": \"personal\".", 1)
    return hits[0]


def ensure_project(target, name):
    hits = [p for p in projects(target) if p.get("type") == "team" and p.get("name") == name]
    if hits:
        return hits[0], False
    code, resp = api(target, "POST", "/projects", {"name": name})
    if code not in (200, 201):
        fail(f"project create FAILED HTTP {code}: {resp.get('error')} "
             "(team projects need a licensed instance and a key with project:create)", 1)
    # 2.3x answers 201 with an empty body; read it back by name.
    hits = [p for p in projects(target) if p.get("type") == "team" and p.get("name") == name]
    return (hits[0] if hits else {"id": resp.get("id"), "name": name, "type": "team"}), True


def workflow_project_id(wf):
    """Owning project of a live workflow record (GET /workflows/{id})."""
    for s in wf.get("shared") or []:
        if s.get("role") == "workflow:owner":
            return s.get("projectId")
    return None


# ------------------------------------------------------------------- folders

_FOLDER_PROJECT = {}


def folder_project(target, project_id, hint=None):
    """Project id for the folder endpoints. Self-hosted 2.35 accepts "personal" as
    the path segment; cloud answers 404, so the key's own project id is taken
    from the workflow being placed (`hint`), the project list, or a data table."""
    if project_id != PERSONAL:
        return project_id
    base = target["base"]
    if base not in _FOLDER_PROJECT:
        code, _ = api(target, "GET", f"/projects/{PERSONAL}/folders?take=1&skip=0")
        pid = PERSONAL if code == 200 else hint
        if not pid:
            code, resp = api(target, "GET", "/projects?limit=100")
            pid = next((p["id"] for p in (resp.get("data") or []) if p.get("type") == PERSONAL), None) if code == 200 else None
        if not pid:
            code, resp = api(target, "GET", "/data-tables?limit=1")
            pid = next((t.get("projectId") for t in (resp.get("data") or [])), None) if code == 200 else None
        _FOLDER_PROJECT[base] = pid or PERSONAL
    return _FOLDER_PROJECT[base]


def folders(target, project_id):
    out, skip = [], 0
    while True:
        code, resp = api(target, "GET", f"/projects/{project_id}/folders?take=100&skip={skip}")
        if code != 200:
            fail(f"folder list FAILED HTTP {code}: {resp.get('error')} "
                 "(folders need n8n 2.19+ on a registered or licensed instance)", 1)
        page = resp.get("data", [])
        out.extend(page)
        skip += len(page)
        if not page or skip >= int(resp.get("count") or 0):
            return out


def ensure_folder(target, project_id, path, dry=False):
    """Folder id for a path like "Demos/Order intake", creating what is missing."""
    segments = [s.strip() for s in (path or "").split("/") if s.strip()]
    if not segments:
        fail('workflow has no "folder". Add "folder": "<name or path>"; the project root is not organized.', 1)
    have = folders(target, project_id)
    parent, created = None, []
    for seg in segments:
        hit = next((f for f in have if f.get("name") == seg and (f.get("parentFolderId") or None) == parent), None)
        if not hit:
            if dry:
                created.append(seg)
                parent = f"<new:{seg}>"
                continue
            body = {"name": seg}
            if parent:
                body["parentFolderId"] = parent
            code, resp = api(target, "POST", f"/projects/{project_id}/folders", body)
            if code not in (200, 201):
                fail(f"folder create FAILED for '{seg}' HTTP {code}: {resp.get('error')}", 1)
            hit = resp
            have.append(hit)
            created.append(seg)
        parent = hit["id"]
    return parent, created


def folder_count(target, project_id, folder_id):
    code, resp = api(target, "GET", f"/projects/{project_id}/folders/{folder_id}")
    return resp.get("totalWorkflows") if code == 200 else None


# ---------------------------------------------------------------------- tags

def ensure_tags(target, names):
    """Resolve tag names to ids on the target, creating any that are missing."""
    existing = {t["name"]: t["id"] for t in api_all(target, "/tags?limit=250")}
    ids = []
    for name in names:
        tid = existing.get(name)
        if not tid:
            c, r = api(target, "POST", "/tags", {"name": name})
            if c == 409:  # exists but was not listed (a shared instance with many tags): re-read
                existing = {t["name"]: t["id"] for t in api_all(target, "/tags?limit=250")}
                r = {"id": existing.get(name)}
                c = 200 if r["id"] else 409
            if c not in (200, 201):
                fail(f"tag create FAILED for '{name}' HTTP {c}: {r.get('error')}", 1)
            tid = r["id"]
            existing[name] = tid
        ids.append(tid)
    return ids


def set_tags(target, wid, names):
    ids = ensure_tags(target, names)
    code, resp = api(target, "PUT", f"/workflows/{wid}/tags", [{"id": i} for i in ids])
    if code not in (200, 201):
        fail(f"tag assign FAILED HTTP {code}: {resp.get('error')}", 1)
    return names


# --------------------------------------------------------------- credentials

def credentials_index(target):
    """id -> credential record with its `shared` projects. Needs an owner or admin key."""
    return {c["id"]: c for c in api_all(target, "/credentials?limit=250")}


def credential_projects(cred):
    return {s.get("id") for s in cred.get("shared") or []}


def used_credentials(wf):
    """(id, name) of every credential the nodes reference."""
    out = set()
    for node in wf.get("nodes", []):
        for ref in (node.get("credentials") or {}).values():
            if ref.get("id") or ref.get("name"):
                out.add((ref.get("id"), ref.get("name")))
    return out


def credential_scope_violations(wf, project, creds):
    """Credentials the workflow uses that its project cannot reach. A workflow in a
    team project executes only with credentials that project owns or is shared
    with; anything else fails at runtime with "node does not have access"."""
    if project["id"] == PERSONAL:
        return []
    bad = []
    for cid, cname in used_credentials(wf):
        cred = creds.get(cid) or next((c for c in creds.values() if c.get("name") == cname), None)
        if not cred:
            bad.append(f"'{cname}' ({cid}): not on this instance")
        elif project["id"] not in credential_projects(cred):
            owners = ", ".join(sorted(s.get("name", "?") for s in cred.get("shared") or [])) or "nobody"
            bad.append(f"'{cred['name']}' ({cred['id']}): owned by {owners}")
    return bad


def credential_users(wfs, cred_id):
    """Live workflows whose nodes bind this credential id."""
    return [w for w in wfs if any(r.get("id") == cred_id for n in w.get("nodes", [])
                                  for r in (n.get("credentials") or {}).values())]


def move_batch_credentials(target, batch, project, creds, wfs=None, dry=False):
    """Before a batch of workflows moves into a project, move the credentials that
    only this batch uses. A credential some other workflow still uses stays where
    it is and is reported, because the move would take it away from that workflow;
    the caller then creates a project-scoped copy. `batch` is [(wid, wf_json)].
    Returns (moved_names, violations)."""
    if project["id"] == PERSONAL:
        return [], []
    wfs = wfs if wfs is not None else api_all(target, "/workflows?limit=250")
    batch_ids = {wid for wid, _ in batch if wid}
    moved, bad, seen = [], [], set()
    for _, wf in batch:
        for cid, cname in used_credentials(wf):
            cred = creds.get(cid) or next((c for c in creds.values() if c.get("name") == cname), None)
            if not cred or cred["id"] in seen:
                continue
            seen.add(cred["id"])
            if project["id"] in credential_projects(cred):
                continue
            outside = sorted(w["name"] for w in credential_users(wfs, cred["id"]) if w["id"] not in batch_ids)
            if outside:
                bad.append(f"'{cred['name']}' ({cred['id']}) is also used by " + "; ".join(outside[:5])
                           + (" ..." if len(outside) > 5 else ""))
                continue
            if not dry:
                code, resp = api(target, "PUT", f"/credentials/{cred['id']}/transfer", {"destinationProjectId": project["id"]})
                if code not in (200, 204):
                    fail(f"credential transfer FAILED for '{cred['name']}' HTTP {code}: {resp.get('error')}", 1)
            # The index reflects the move (on a dry run, the plan), so the scope
            # check that follows sees the credential where it will be.
            cred["shared"] = [{"id": project["id"], "name": project["name"], "role": "credential:owner"}]
            moved.append(cred["name"])
    return moved, bad


def transfer_credential(target, cred, project, creds, force=False):
    """Move one credential into a project. Refuses when workflows outside the
    destination still use it, because the move takes the credential away from them."""
    wfs = api_all(target, "/workflows?limit=250")
    outside = sorted(w["name"] for w in credential_users(wfs, cred["id"]) if workflow_project_id(w) != project["id"])
    if outside and not force:
        return False, (f"used by {len(outside)} workflow(s) outside '{project['name']}': "
                       + "; ".join(outside[:6]) + (" ..." if len(outside) > 6 else "")
                       + ". Create a project-scoped copy for them, or --force to move it anyway.")
    code, resp = api(target, "PUT", f"/credentials/{cred['id']}/transfer", {"destinationProjectId": project["id"]})
    if code not in (200, 204):
        return False, f"transfer FAILED HTTP {code}: {resp.get('error')}"
    return True, f"moved{' (forced; ' + str(len(outside)) + ' outside users lost access)' if outside else ''}"


# --------------------------------------------------------------- data tables

def data_tables_index(target):
    """id -> data table record (name, projectId, columns). Tables are project-scoped
    like credentials, and unlike credentials they cannot be transferred."""
    return {t["id"]: t for t in api_all(target, "/data-tables?limit=100")}


def used_data_tables(wf):
    """ids of every Data Table the nodes bind (n8n-nodes-base.dataTable, mode id)."""
    out = set()
    for node in wf.get("nodes", []):
        if node.get("type") != "n8n-nodes-base.dataTable":
            continue
        ref = (node.get("parameters") or {}).get("dataTableId")
        tid = ref.get("value") if isinstance(ref, dict) else ref
        if isinstance(tid, str) and tid and not tid.startswith("="):
            out.add(tid)
    return out


def data_table_scope_violations(wf, project, tables):
    """Data tables the workflow binds that live in another project. A Data Table
    node resolves its table inside the workflow's own project and fails with
    "could not be found in this project" otherwise."""
    if project["id"] == PERSONAL:
        return []
    bad = []
    for tid in sorted(used_data_tables(wf)):
        t = tables.get(tid)
        if not t:
            bad.append(f"data table {tid}: not on this instance")
        elif t.get("projectId") != project["id"]:
            bad.append(f"data table '{t['name']}' ({tid}) lives in project {t.get('projectId')}")
    return bad


def copy_data_table(target, table, project, copy_rows=True, archive=True):
    """Recreate a data table inside a project, copy its rows, rename the original
    with an archive prefix. There is no transfer endpoint for tables, and the node
    binds the table by id, so the caller rebinds the workflow JSON to the new id.
    Returns (new_id, rows_copied)."""
    code, full = api(target, "GET", f"/data-tables/{table['id']}")
    if code != 200:
        fail(f"data table read FAILED HTTP {code}: {full.get('error')}", 1)
    cols = [{"name": c["name"], "type": c["type"]} for c in sorted(full.get("columns", []), key=lambda c: c.get("index", 0))]
    code, made = api(target, "POST", "/data-tables", {"name": table["name"], "columns": cols, "projectId": project["id"]})
    if code not in (200, 201):
        fail(f"data table create FAILED for '{table['name']}' HTTP {code}: {made.get('error')}", 1)
    new_id, copied = made["id"], 0
    if copy_rows:
        names = {c["name"] for c in cols}
        cursor = None
        while True:
            code, page = api(target, "GET", f"/data-tables/{table['id']}/rows?limit=100" + (f"&cursor={cursor}" if cursor else ""))
            if code != 200:
                fail(f"row read FAILED HTTP {code}: {page.get('error')}", 1)
            rows = [{k: v for k, v in r.items() if k in names} for r in page.get("data", [])]
            if rows:
                code, resp = api(target, "POST", f"/data-tables/{new_id}/rows", {"data": rows})
                if code not in (200, 201):
                    fail(f"row insert FAILED HTTP {code}: {resp.get('error')}", 1)
                copied += len(rows)
            cursor = page.get("nextCursor")
            if not cursor:
                break
    if archive:
        import datetime
        stamp = datetime.date.today().isoformat()
        api(target, "PATCH", f"/data-tables/{table['id']}", {"name": f"zz-archived-{stamp}-{table['name']}"[:128]})
    return new_id, copied


# -------------------------------------------------------------------- placing

def placement(wf):
    """(tags, project, folder) declared in a workflow file."""
    tags = [t if isinstance(t, str) else t.get("name") for t in (wf.get("tags") or [])]
    return [t for t in tags if t], (wf.get("project") or "").strip(), (wf.get("folder") or "").strip()


def place_workflow(target, wid, wf, creds=None, dry=False, body=None, tables=None):
    """Put a live workflow where its JSON says: project (transfer), folder (PUT with
    parentFolderId), tags. `body` is the update body a deploy already built; without
    it the live record is read back and re-sent unchanged. Returns a report dict."""
    tags, project_name, folder_path = placement(wf)
    report = {"id": wid, "name": wf.get("name")}
    if not tags:
        fail('tag gate: workflow has no tags. Add "tags": [...] (every workflow is tagged).', 1)
    project = resolve_project(target, project_name)
    creds = creds if creds is not None else credentials_index(target)
    bad = credential_scope_violations(wf, project, creds)
    if bad:
        fail(f'credential scope: "{project["name"]}" cannot reach ' + "; ".join(bad)
             + f'. Fix: organize.sh credentials "<name>" --to "{project["name"]}" '
             f'--env {target.get("env") or "dev"}, or create the credential inside the project.', 1)
    if tables is None and used_data_tables(wf):
        tables = data_tables_index(target)
    bad = data_table_scope_violations(wf, project, tables or {})
    if bad:
        fail(f'data table scope: "{project["name"]}" cannot reach ' + "; ".join(bad)
             + f'. Tables do not transfer: organize.sh tables "<name>" --to "{project["name"]}" '
             f'--env {target.get("env") or "dev"} copies one into the project, then rebind the node to the new id.', 1)

    code, live = api(target, "GET", f"/workflows/{wid}")
    if code != 200:
        fail(f"read-back FAILED HTTP {code}: {live.get('error')}", 1)
    current = workflow_project_id(live)
    needs_transfer = project["id"] != PERSONAL and current != project["id"]
    report["project"] = project["name"]
    report["transfer"] = needs_transfer

    folder_project_id = folder_project(target, project["id"], hint=current)
    folder_id, created = ensure_folder(target, folder_project_id, folder_path, dry=dry)
    report["folder"] = folder_path
    report["folder_created"] = created
    report["tags"] = tags
    if dry:
        return report

    if needs_transfer:
        code, resp = api(target, "PUT", f"/workflows/{wid}/transfer", {"destinationProjectId": project["id"]})
        if code not in (200, 204):
            fail(f"transfer FAILED HTTP {code}: {resp.get('error')}", 1)

    body = body or {
        "name": live["name"], "nodes": live["nodes"], "connections": live["connections"],
        "settings": {k: v for k, v in (live.get("settings") or {}).items()
                     if k in ("executionOrder", "timezone", "saveDataErrorExecution", "saveDataSuccessExecution",
                              "saveManualExecutions", "saveExecutionProgress", "errorWorkflow", "executionTimeout",
                              "callerPolicy", "callerIds")},
    }
    body = dict(body, parentFolderId=folder_id)
    code, resp = api(target, "PUT", f"/workflows/{wid}", body)
    if code not in (200, 201):
        fail(f"folder placement PUT FAILED HTTP {code}: {resp.get('error')}", 1)
    set_tags(target, wid, tags)

    # Verify: project from the read-back, folder from the folder's own count, publish state.
    code, live = api(target, "GET", f"/workflows/{wid}")
    if project["id"] != PERSONAL and workflow_project_id(live) != project["id"]:
        fail(f"VERIFY FAILED: workflow is not in project '{project['name']}' after transfer", 1)
    report["folder_total"] = folder_count(target, folder_project_id, folder_id)
    report["live_tags"] = sorted(t["name"] for t in live.get("tags") or [])
    report["draft"] = bool(live.get("active")) and live.get("versionId") != live.get("activeVersionId")
    return report


def print_report(r, env):
    line = (f"  {r['name']}\n    project {r['project']}{'  (transferred)' if r.get('transfer') else ''}"
            f"\n    folder  {r['folder']}" + (f"  (created {', '.join(r['folder_created'])})" if r.get("folder_created") else "")
            + f"\n    tags    {', '.join(r['tags'])}")
    if "folder_total" in r:
        line += f"\n    verify  folder holds {r['folder_total']} workflow(s); live tags {r['live_tags']}"
    if r.get("draft"):
        line += ("\n    WARNING an unpublished draft is live (versionId != activeVersionId). "
                 "Publish it: the editor's Publish button, or `n8n publish:workflow --id=...` in the container.")
    print(line)


# ----------------------------------------------------------------------- CLI

def load_files(path):
    files = sorted(glob.glob(os.path.join(path, "*.json"))) if os.path.isdir(path) else [path]
    out = []
    for f in files:
        if f.endswith(".zones.json") or os.path.basename(f) in ("lab-ids.json",):
            continue
        try:
            wf = json.load(open(f, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            fail(f"{f}: not valid JSON ({e})", 1)
        if isinstance(wf, dict) and "nodes" in wf:
            out.append((f, wf))
    if not out:
        fail(f"no workflow JSON under {path}", 1)
    return out


def cmd_audit(target):
    projs = projects(target)
    wfs = api_all(target, "/workflows?limit=250")
    creds = credentials_index(target)
    by_project = {}
    for w in wfs:
        by_project.setdefault(workflow_project_id(w), []).append(w)
    team = [p for p in projs if p.get("type") == "team"]
    tables = data_tables_index(target)
    print(f"{target['url']}  projects {len(projs)} ({len(team)} team)  workflows {len(wfs)}  "
          f"credentials {len(creds)}  data tables {len(tables)}\n")
    for p in projs:
        ws = by_project.get(p["id"], [])
        try:
            fcount = len(folders(target, p["id"]))
        except SystemExit:
            fcount = "?"
        cs = sum(1 for c in creds.values() if p["id"] in credential_projects(c))
        ts = sum(1 for t in tables.values() if t.get("projectId") == p["id"])
        print(f"  [{p.get('type'):8}] {p['name']}: {len(ws)} workflows, {fcount} folders, {cs} credentials, {ts} data tables")
    untagged = sorted(w["name"] for w in wfs if not w.get("tags"))
    personal_ids = {p["id"] for p in projs if p.get("type") == "personal"}
    in_personal = sorted(w["name"] for w in wfs if workflow_project_id(w) in personal_ids)
    scope = []
    for w in wfs:
        pid = workflow_project_id(w)
        if pid in personal_ids:
            continue
        pname = next((p["name"] for p in projs if p["id"] == pid), pid)
        proj = {"id": pid, "name": pname}
        bad = credential_scope_violations(w, proj, creds) + data_table_scope_violations(w, proj, tables)
        if bad:
            scope.append(f"{w['name']} in '{pname}': " + "; ".join(bad))
    print(f"\nuntagged workflows: {len(untagged)}")
    for n in untagged:
        print(f"  {n}")
    if team:
        print(f"\nworkflows left in a personal project: {len(in_personal)}")
        for n in in_personal:
            print(f"  {n}")
    print(f"\ncredential scope violations: {len(scope)}")
    for s in scope:
        print(f"  {s}")
    print("\nRESULT:", "ok" if not (untagged or scope or (team and in_personal)) else "findings")
    return 0 if not (untagged or scope) else 1


def cmd_place(target, path, dry):
    creds = credentials_index(target)
    files = load_files(path)
    print(f"place -> {target['url']} ({target.get('env')}){'  [dry run]' if dry else ''}")
    # Credentials first, per project: the ones only this batch uses move with it.
    by_project = {}
    for f, wf in files:
        if wf.get("id"):
            by_project.setdefault(placement(wf)[1], []).append((wf["id"], wf))
    wfs = api_all(target, "/workflows?limit=250")
    for pname, batch in by_project.items():
        moved, bad = move_batch_credentials(target, batch, resolve_project(target, pname), creds, wfs=wfs, dry=dry)
        for m in moved:
            print(f"  credential '{m}' {'would move' if dry else 'moved'} into '{pname}'")
        if bad:
            fail(f'credential scope: "{pname}" cannot take ' + "; ".join(bad)
                 + ". Create a project-scoped copy for this batch and rebind the nodes, or --force the move "
                 "with organize.sh credentials.", 1)
    for f, wf in files:
        wid = wf.get("id")
        if not wid:
            print(f"  {os.path.basename(f)}: no id in the JSON, deploy it first (deploy.sh places it)")
            continue
        print_report(place_workflow(target, wid, wf, creds=creds, dry=dry), target.get("env"))
    if dry:
        print("\nDry run. Nothing written.")


def cmd_tables(target, names, project_name, copy_rows=True):
    """Copy data tables into a project (schema and rows), archive the originals,
    print the id map the workflow JSON has to be rebound with."""
    project = resolve_project(target, project_name)
    if project["id"] == PERSONAL:
        fail("tables copy into a named team project; the public API cannot address a personal one", 1)
    tables = data_tables_index(target)
    idmap, rc = {}, 0
    for name in names:
        hits = [t for t in tables.values() if t.get("name") == name or t.get("id") == name]
        if not hits:
            print(f"  {name}: not found"); rc = 1; continue
        if len(hits) > 1:
            print(f"  {name}: {len(hits)} tables share this name, pass the id instead: " + ", ".join(t["id"] for t in hits)); rc = 1; continue
        t = hits[0]
        if t.get("projectId") == project["id"]:
            print(f"  {t['name']} ({t['id']}): already in '{project['name']}'"); continue
        new_id, copied = copy_data_table(target, t, project, copy_rows=copy_rows)
        idmap[t["id"]] = new_id
        print(f"  {t['name']}: {t['id']} -> {new_id} ({copied} rows copied; original renamed zz-archived-...)")
    if idmap:
        print("\nrebind the Data Table nodes (old id -> new id), then redeploy:")
        print(json.dumps(idmap, indent=2))
    return rc


def cmd_credentials(target, names, project_name, force):
    project = resolve_project(target, project_name)
    if project["id"] == PERSONAL:
        fail("credentials move into a named team project; the public API cannot address a personal one", 1)
    creds = credentials_index(target)
    rc = 0
    for name in names:
        hits = [c for c in creds.values() if c.get("name") == name or c.get("id") == name]
        if not hits:
            print(f"  {name}: not found"); rc = 1; continue
        if len(hits) > 1:
            print(f"  {name}: {len(hits)} credentials share this name, pass the id instead: "
                  + ", ".join(c["id"] for c in hits)); rc = 1; continue
        cred = hits[0]
        if project["id"] in credential_projects(cred):
            print(f"  {cred['name']} ({cred['id']}): already in '{project['name']}'"); continue
        ok, msg = transfer_credential(target, cred, project, creds, force=force)
        print(f"  {cred['name']} ({cred['id']}): {msg}")
        rc = rc if ok else 1
    return rc


def main():
    args = sys.argv[1:]
    to, env_flag = None, "dev"
    for opt in ("--to", "--env"):
        if opt in args:
            i = args.index(opt)
            if i + 1 >= len(args):
                fail(f"{opt} needs a value")
            if opt == "--to":
                to = args[i + 1]
            else:
                env_flag = args[i + 1]
            del args[i:i + 2]
    flags = {a for a in args if a.startswith("--")}
    pos = [a for a in args if not a.startswith("--")]
    if not pos:
        fail(__doc__.strip())
    cmd, rest = pos[0], pos[1:]
    if cmd == "audit":
        env = rest[0] if rest else "dev"
        sys.exit(cmd_audit(secrets_env.resolve_target(env)))
    if cmd == "place":
        if not rest:
            fail("usage: organize.sh place <workflow.json|dir> [env] [--dry-run]")
        env = rest[1] if len(rest) > 1 else "dev"
        cmd_place(secrets_env.resolve_target(env, strict=True), rest[0], "--dry-run" in flags)
        return
    if cmd == "credentials":
        if not rest or not to:
            fail("usage: organize.sh credentials <name>... --to <project> [--env <env>] [--force]")
        sys.exit(cmd_credentials(secrets_env.resolve_target(env_flag, strict=True), rest, to, "--force" in flags))
    if cmd == "tables":
        if not rest or not to:
            fail("usage: organize.sh tables <name>... --to <project> [--env <env>] [--no-rows]")
        sys.exit(cmd_tables(secrets_env.resolve_target(env_flag, strict=True), rest, to, copy_rows="--no-rows" not in flags))
    if cmd == "project":
        if not rest:
            fail("usage: organize.sh project <name> [env]")
        env = rest[1] if len(rest) > 1 else "dev"
        target = secrets_env.resolve_target(env, strict=True)
        p, created = ensure_project(target, rest[0])
        print(f"project '{p['name']}' {p.get('id')} {'created' if created else 'exists'} on {target['url']}")
        return
    fail(f"unknown command '{cmd}'\n" + __doc__.strip())


if __name__ == "__main__":
    main()
