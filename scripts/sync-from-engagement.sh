#!/usr/bin/env bash
# Build this public repository from the engagement repository.
#
# The engagement repository is the source. This script copies the build and
# test scripts and the workflow catalogue, rewrites every customer-specific
# term to a neutral one (system names, brand, dealer names, placement, paths),
# regenerates the workflow JSON from the rewritten generator so the JSON and
# the generator agree, and refuses to finish if a customer or private term is
# still present anywhere in the tree. README.md, SETUP.md and
# docs/architecture.md are written here, not copied.
#
# Usage: scripts/sync-from-engagement.sh [path-to-engagement-repo]
set -euo pipefail
SRC="${1:-$HOME/Developer/engagements/mercedes-benz}"
HARNESS="${HARNESS:-$HOME/Developer/n8n-fde-harness}"
DST="$(cd "$(dirname "$0")/.." && pwd)"
[ -d "$SRC/workflows/agentic-race" ] || { echo "no agentic-race workflows under $SRC" >&2; exit 1; }
[ -f "$HARNESS/skills/n8n-native-nodes-first/SKILL.md" ] || { echo "no n8n-native-nodes-first skill under $HARNESS" >&2; exit 1; }

WF="$DST/workflows/voice-booking"
rm -rf "$DST/workflows" "$DST/docs/agentic-race"
mkdir -p "$WF/build" "$DST/docs" "$DST/scripts/canvas"
cp "$SRC/workflows/agentic-race/build/gen.py" "$SRC/workflows/agentic-race/build/deploy.py" "$SRC/workflows/agentic-race/build/setup.py" "$WF/build/"
cp "$SRC/scripts/ar-golden-run.py" "$SRC/scripts/ar-seed.py" "$SRC/scripts/organize_workflows.py" "$SRC/scripts/secrets_env.py" "$DST/scripts/"
cp "$SRC/scripts/canvas/zone_layout.py" "$SRC/scripts/canvas/layout_check.py" "$DST/scripts/canvas/"
cp "$SRC/docs/agentic-race/04-workflow-catalogue.md" "$DST/docs/workflow-catalogue.md"

# The node policy skill and its checker, vendored from the harness so the
# rule the workflows were built under travels with them. .agents/skills is
# the portable location (Codex, Cursor, Copilot); Claude Code reads the link.
SKILL="$DST/.agents/skills/n8n-native-nodes-first"
rm -rf "$DST/.agents/skills"
mkdir -p "$SKILL/scripts" "$DST/.claude/skills"
cp "$HARNESS/skills/n8n-native-nodes-first/SKILL.md" "$SKILL/"
cp "$HARNESS/skills/n8n-native-nodes-first/scripts/node_policy_check.py" "$SKILL/scripts/"
ln -sfn ../../.agents/skills/n8n-native-nodes-first "$DST/.claude/skills/n8n-native-nodes-first"
cat > "$DST/scripts/node-policy.sh" <<'EOS'
#!/usr/bin/env bash
# Node policy check: native nodes first. RESULT: ok or the findings, exit 1 on an error.
# Usage: scripts/node-policy.sh [workflow.json | directory] [--policy deploy/node-policy.json] [--json]
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$HERE/.agents/skills/n8n-native-nodes-first/scripts/node_policy_check.py" "${@:-$HERE/workflows/voice-booking}"
EOS
chmod +x "$DST/scripts/node-policy.sh"

python3 - "$DST" <<'EOF'
import os, re, sys
dst = sys.argv[1]
# Ordered: specific phrases first, bare terms last. Applied to every text file.
TERMS = [
    # dealer names in the mock data
    ("Mercedes-Benz Niederlassung Stuttgart", "Dealer Stuttgart Mitte"),
    ("Mercedes-Benz of London West", "Dealer London West"),
    ("Mercedes-Benz Warszawa Okęcie", "Dealer Warszawa Okęcie"),
    ("Mercedes-Benz Warszawa Wola", "Dealer Warszawa Wola"),
    ("Mercedes-Benz Madrid Castellana", "Dealer Madrid Castellana"),
    ("Mercedes-Benz Barcelona", "Dealer Barcelona"),
    ("Mercedes-Benz Kraków", "Dealer Kraków"),
    ("Mercedes-Benz Gdańsk", "Dealer Gdańsk"),
    # spoken texts
    ("asystent cyfrowy Mercedes-Benz", "asystent cyfrowy serwisu"),
    ("the Mercedes-Benz digital assistant", "the workshop's digital assistant"),
    ("el asistente digital de Mercedes-Benz", "el asistente digital del taller"),
    ("der digitale Assistent von Mercedes-Benz", "der digitale Assistent der Werkstatt"),
    ("Mercedes-Benz: wizyta serwisowa", "Serwis: wizyta serwisowa"),
    ("Mercedes-Benz: cita de taller", "Taller: cita de taller"),
    ("Mercedes-Benz: Werkstatttermin", "Werkstatt: Werkstatttermin"),
    ("Mercedes-Benz: service appointment", "Workshop: service appointment"),
    ("You are the Mercedes-Benz service appointment assistant", "You are the workshop service appointment assistant"),
    ("Lists Mercedes-Benz dealers", "Lists dealers"),
    # placement and credential defaults
    ('"project", "Mercedes-Benz"', '"project", "Voice Booking"'),
    ('"AR_PROJECT", "Mercedes-Benz"', '"AR_PROJECT", "Voice Booking"'),
    ('"folder", "Agentic Race"', '"folder", "Voice Booking Agent"'),
    ('(default "Mercedes-Benz";', '(default "Voice Booking";'),
    ('defaults "Mercedes-Benz", "Agentic Race"', 'defaults "Voice Booking", "Voice Booking Agent"'),
    ('"AR_FOLDER", "Agentic Race"', '"AR_FOLDER", "Voice Booking Agent"'),
    ('["mercedes-benz", "agentic-race"]', '["voice-booking"]'),
    ("LM Studio (Mercedes-Benz)", "Model endpoint"),
    ("AR lab Postgres (ar_lab)", "Chat memory Postgres"),
    ("AR gateway bearer (lab)", "Gateway bearer"),
    # case system
    ("DSD Book through the Sirius API", "DealerBook through the dealer booking API"),
    ("Sirius DSD booking API", "dealer booking API"),
    ("Sirius DSD API", "dealer booking API"),
    ("Mock Sirius (Salesforce) case", "Mock case system"),
    ("Sirius (Salesforce) case API", "case system API"),
    ("Sirius (Salesforce) case", "case"),
    ("AR-MOCK-SIRIUS Salesforce case", "AR-MOCK-CASE Case system"),
    ("Mock Sirius", "Mock case system"),
    ("ar-22-mock-sirius-case", "ar-22-mock-case-system"),
    ("ar-15-sirius-case", "ar-15-case-system"),
    ("AR-15 Sirius case create", "AR-15 Case create"),
    ("AR-15 Sirius case", "AR-15 Case"),
    ("CallSirius", "CallCaseSystem"),
    ("SIRIUS_JS", "CASE_JS"),
    ("/sirius/case", "/casesys/case"),
    ("the Sirius case", "the case"),
    ("Sirius case", "case"),
    ("Sirius", "case system"),
    ("SIRIUS", "CASE"),
    ("ar-mock/sirius", "ar-mock/casesys"),
    ("sirius", "casesys"),
    ("Standard Salesforce REST", "A standard REST API"),
    ("Salesforce REST", "the case system's REST"),
    ("Salesforce", "CRM"),
    ("Customer 360", "customer master"),
    # dealer booking system
    ("GIS / DSD dealer search", "dealer search"),
    ("CallDsd", "CallDealerBook"),
    ("ar-21-mock-dsd-book", "ar-21-mock-dealerbook"),
    ("AR-MOCK-DSD Book", "AR-MOCK-DealerBook"),
    ("Mock DSD Book", "Mock DealerBook"),
    ("DSD Book", "DealerBook"),
    ("ar-mock/dsd", "ar-mock/dealerbook"),
    ("DSD_", "DEALERBOOK_"),
    ("'DSD-'", "'BK-'"),
    ("Non-DSD dealer", "Non-bookable dealer"),
    ("dsdEnabled", "bookable"),
    ("GIS / DSD", "the dealer directory"),
    ("or GIS", "or the dealer directory"),
    ("DSD", "DealerBook"),
    ("dsd", "dealerbook"),
    # contact center
    ("AR-14 Build Genesys handoff context", "AR-14 Build handoff context"),
    ("Genesys agent desktop", "agent desktop"),
    ("Genesys requirement", "contact-center requirement"),
    ("Genesys return context", "contact-center return context"),
    ("Genesys UUI", "contact-center UUI"),
    ("genesysValue", "ccValue"),
    ("@genesys.invalid", "@cc.invalid"),
    ("byoc.euc1.genesys.example", "cc.example"),
    ("byoc.euw2.genesys.example", "cc.example"),
    ("Genesys", "contact center"),
    # brand and customer
    ("The Mercedes voice-booking conversion on 2026-10-21", "The conversion of this repository's workflow set on 2026-10-21"),
    ("the MB instance", "the production instance"),
    ("the MB systems", "the real systems"),
    ("MB customer API", "customer API"),
    ("MB Legal", "Legal"),
    ("M. Frostbutter", "the repository owner"),
    ("Mercedes-Benz", "the manufacturer"),
    ("Mercedes", "the manufacturer"),
    ("mercedes-benz", "voice-booking"),
    # race
    ("Generate the Agentic Race workflow set", "Generate the voice booking workflow set"),
    ("workflows/agentic-race", "workflows/voice-booking"),
    ("data/agentic-race", "data/voice-booking"),
    ("docs/agentic-race", "docs"),
    ("service: 'agentic-race'", "service: 'voice-booking'"),
    ("Agentic Race", "Voice Booking Agent"),
    ("agentic-race", "voice-booking"),
    ("The proof's pass criterion lives here", "The proof's pass condition lives here"),
    # synthetic VINs on the brand's manufacturer codes
    ("W1K2970461A123456", "TST2970461A123456"),
    ("WDD2130421A654321", "TST2130421A654321"),
    ("W1K1770871N998877", "TST1770871N998877"),
    ("W1N2476011J112233", "TST2476011J112233"),
    ("WDB0000000X000000", "TST0000000X000000"),
    ("W1K2970461A223344", "TST2970461A223344"),
    ("W1N2476011J445566", "TST2476011J445566"),
    # personal tooling
    ("(engagement copy first, then the harness, then ~/.claude)", "(scripts/canvas next to the repository, or N8N_CANVAS_SCRIPTS)"),
    ("per the n8n-canvas-docs\nskill", "per the canvas layout\nscripts"),
    ("the n8n-canvas-docs skill", "the canvas layout scripts"),
    (",\n             os.path.expanduser(\"~/Developer/n8n-fde-harness/skills/n8n-canvas-docs/scripts\"),\n             os.path.expanduser(\"~/.claude/skills/n8n-canvas-docs/scripts\")", ""),
]
SKIP = {"sync-from-engagement.sh"}
# In the vendored skill, Salesforce is an n8n node name, not a customer term.
SKILL_DIR = os.path.join(dst, ".agents")
GENERIC = {"Standard Salesforce REST", "Salesforce REST", "Salesforce"}
changed = {}
for d, _, fs in os.walk(dst):
    if "/.git" in d:
        continue
    for f in fs:
        if f in SKIP:
            continue
        p = os.path.join(d, f)
        try:
            t = open(p, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        n = t
        terms = [x for x in TERMS if x[0] not in GENERIC] if p.startswith(SKILL_DIR) else TERMS
        for old, new in terms:
            n = n.replace(old, new)
        if n != t:
            open(p, "w", encoding="utf-8").write(n); changed[os.path.relpath(p, dst)] = True
print("rewritten:", ", ".join(sorted(changed)))

# The catalogue's evaluation column belongs to the customer's process.
cat = os.path.join(dst, "docs", "workflow-catalogue.md")
out, drop = [], False
for line in open(cat, encoding="utf-8").read().splitlines():
    if line.startswith("|") and "Criteria served" in line:
        drop = True
    if line.startswith("|") and drop:
        cells = line.split("|")
        line = "|".join(cells[:-2]) + "|"
    elif not line.startswith("|"):
        drop = False
    out.append(line)
open(cat, "w", encoding="utf-8").write("\n".join(out) + "\n")
EOF

# Regenerate the workflow JSON from the rewritten generator. A placeholder ids
# file stands in for the one build/setup.py writes on a real instance.
python3 - "$WF" <<'EOF'
import json, sys, os, re
wf = sys.argv[1]
ids = {k: "PENDING" for k in ("credPostgres", "credGatewayHeader", "credLmStudio")}
ids.update({"dt_" + t: "PENDING" for t in re.findall(r'^\s+"(ar_\w+)":\s*\[', open(os.path.join(wf, "build", "gen.py")).read(), re.M)})
ids["modelBaseUrl"] = ""
json.dump(ids, open(os.path.join(wf, "lab-ids.json"), "w"), indent=2)
EOF
(cd "$WF" && python3 build/gen.py >/dev/null) || { echo "generator failed on the rewritten tree" >&2; exit 1; }
rm -f "$WF/lab-ids.json"
echo "generated: $(ls "$WF"/ar-*.json | grep -vc zones) workflows"

# The env example is authored here so its defaults stay neutral.
cat > "$DST/.env.example" <<'EOF'
# Export these in the shell before build/setup.py, build/deploy.py and
# scripts/ar-golden-run.py. Keep the values in your secret manager; never
# commit a filled copy. See SETUP.md.

# The instance and an API key with workflow, credential, project, tag and
# data-table scopes.
N8N_URL=
N8N_API_KEY=

# Bearer the voice gateway (and the test runner) sends on every call.
AR_GATEWAY_TOKEN=

# Postgres for chat memory (one table, created on first use).
AR_PG_HOST=
AR_PG_PORT=5432
AR_PG_DATABASE=
AR_PG_USER=
AR_PG_SSL=disable
AR_PG_PASSWORD=

# OpenAI-compatible model endpoint with tool calling.
AR_MODEL_API_KEY=
AR_MODEL_BASE_URL=
AR_MODEL=

# Placement. "personal" on an instance without team projects.
AR_PROJECT=Voice Booking
AR_FOLDER=Voice Booking Agent

# Optional: the URL n8n uses to call its own mock webhooks (default N8N_URL).
# AR_SELF_URL=http://localhost:5678

# Optional: a command that prints the chat-memory row count for a session, so
# the test runner can verify the purge. {sessionId} is substituted.
# AR_MEMORY_COUNT_CMD=psql postgresql://user:pass@host/db -tAc "SELECT count(*) FROM ar_chat_memory WHERE session_id = '{sessionId}'"
EOF

# Nothing customer-specific, personal or lab-bound may remain.
PATTERN='[Mm]ercedes|MERCEDES|Benz|BENZ|\bMB\b|[Ss]irius|SIRIUS|[Dd]sd|DSD|[Gg]enesys|GENESYS|Nexus|Model Garden|Salesforce|\bGIS\b|Daimler|Agentic Race|agentic-race|\bRace\b|jury|Jury|criteri|W1K|WDD|W1N|WDB|Frosty|frosty|Frostbutter|Marta|Vivek|Dawid|Biniam|Eitler|Malaika|Daniele|Rajeshwari|Preeth|Zacharias|MASSP4AI|infisical|Infisical|podman|pg-lab|netbird|ag-ingest|10\.10\.|n8n-fde-harness|[a-z]+labs\.com|\.claude'
if grep -rnE "$PATTERN" "$DST" --exclude-dir=.git --exclude-dir=.agents --exclude=sync-from-engagement.sh --exclude=secrets_env.py; then
  echo "sync refused: customer or private terms above" >&2; exit 1
fi
if grep -rnE "${PATTERN/|Salesforce/}" "$DST/.agents"; then
  echo "sync refused: customer or private terms in the vendored skill" >&2; exit 1
fi
command -v gitleaks >/dev/null && gitleaks detect --source "$DST" --no-git --no-banner --redact
cd "$DST" && git status --short | head -40
