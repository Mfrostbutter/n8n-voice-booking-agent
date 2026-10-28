#!/usr/bin/env python3
"""Seed the AR configuration tables (ar_markets, ar_codes, ar_prompts) and the mock fixtures through AR-90's admin webhook,
plus the gitignored overlay data/voice-booking/fixtures/mock-overlay.json (test identities) when present.
Reads .env.lab like the golden runner: AR_GATEWAY_TOKEN or N8N_LAB_AR_GATEWAY_TOKEN, N8N_LAB_URL. Safe to re-run."""
import json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(os.path.dirname(HERE), ".env.lab")
if os.path.exists(ENV):
    for line in open(ENV):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        os.environ.setdefault(k.strip(), v)
BASE = (os.environ.get("N8N_LAB_URL") or os.environ.get("N8N_URL") or "http://localhost:5678").rstrip("/")
TOK = os.environ.get("AR_GATEWAY_TOKEN") or os.environ.get("N8N_LAB_AR_GATEWAY_TOKEN")
if not TOK:
    sys.exit("gateway token missing (AR_GATEWAY_TOKEN)")
# Overlay rows (the engagement's test identities, gitignored): {"mockCustomers": [...], "mockDealers": [...], "mockBookings": [...]}.
OVERLAY = os.path.join(os.path.dirname(HERE), "data", "voice-booking", "fixtures", "mock-overlay.json")
body = {}
if os.path.exists(OVERLAY):
    body = {k: v for k, v in json.load(open(OVERLAY)).items() if k in ("mockCustomers", "mockDealers", "mockBookings")}
    print("overlay rows:", {k: len(v) for k, v in body.items()})
req = urllib.request.Request(BASE + "/webhook/ar/v1/admin/seed", data=json.dumps(body).encode(), method="POST")
req.add_header("Authorization", "Bearer " + TOK)
req.add_header("Content-Type", "application/json")
with urllib.request.urlopen(req, timeout=120) as r:
    print(r.status, json.loads(r.read() or b"{}"))
