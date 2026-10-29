---
name: n8n-native-nodes-first
description: >-
  Choose the node before building any n8n workflow step: the native node n8n
  ships first, every time. Use before adding or replacing any node, when a
  request names a community node, a custom node, an npm package or "a node
  for X", when reaching for HTTP Request, a Code node or an AI-agent tool to
  talk to a service, when converting a workflow built by a coding agent, when
  a customer's security review asks what is in a workflow, when deploy.sh
  stops on the node policy gate or the native-node-gate hook asks for a
  decision, and whenever the user says native node, community node, custom
  node, verified node, "is there a node for", "just use HTTP Request", "I'll
  write it in Code", or node policy. Owns the ladder (dedicated node, HTTP
  Request on a predefined credential, native transform, Code node, community
  node with a recorded exception), what counts as native, the exception
  record, deploy/node-policy.json, and the checker the deploy gate runs.
---

# Native n8n nodes first

A workflow built for a customer is built from the nodes n8n ships. A dedicated
integration node is the first choice, HTTP Request on a predefined credential
the second, a native transform node before any Code node, and a community or
custom node only when nothing native covers the need and the exception is
written down where a reviewer will read it. `deploy.sh` refuses the rest.

Why this is a hard rule and not a preference:

- **Security clearance.** A customer's security team clears n8n once. Every
  community package is a separate review: its author, its dependencies, its
  update cadence, and whether it runs on every worker. One community node can
  stall a go-live by weeks; a native node is already inside the approved
  surface.
- **Documentation.** Native nodes have official docs, a stable `typeVersion`
  history, a breaking-changes record and a support path. A community node has
  a README and an issue tracker. The customer's operators maintain this after
  the engagement ends.
- **Credentials.** Native nodes bind to the credential system. A Code node or a
  community node that takes a token as a parameter cannot, and the token ends
  up in a Set node or a hardcoded string (see `craft/secrets-and-security.md`).
- **Upgrades.** `n8n-upgrade-plan` can plan around native `typeVersion` drift
  from the docs server. It cannot plan around a package that stopped
  publishing.
- **Coding agents reach for code.** Left alone, an agent builds a 40-Code-node
  workflow because code is what it knows. The converted version uses If, Switch,
  Edit Fields and the dedicated nodes, runs the same golden set, and is the one
  a customer can read on the canvas.

Scope boundaries:

| Task | Skill |
|---|---|
| Finding a node and its operations (`search_nodes`, `get_node`, `explore_node_resources`) | `n8n-mcp-tools-expert` |
| Configuring the node once chosen | `n8n-node-configuration` |
| The Code node when it is the right answer | `n8n-code-javascript`, `n8n-code-tool` |
| Packaging and distributing a custom node the engagement decided to build | `n8n-node-dev` |
| Answering the customer's security objection in writing | `n8n-enterprise-delivery` |
| The deploy and the other three gates | `n8n-project-organization`, `n8n-canvas-docs`, `n8n-instance-ops` |

## What counts as native

A node type n8n ships in the product:

| Prefix | What |
|---|---|
| `n8n-nodes-base.*` | Every core node: triggers, integrations, transforms, flow control, HTTP Request, Code |
| `@n8n/n8n-nodes-langchain.*` | AI Agent, chat models, memory, tools, vector stores, Information Extractor, Text Classifier, MCP Client Tool |

Anything else is not native:

| Prefix | What | Rule |
|---|---|---|
| `n8n-nodes-<name>.*`, `@<scope>/n8n-nodes-<name>.*` | Community package from npm | Exception required |
| `CUSTOM.*`, a package the customer built | Custom node | Engagement decision, `n8n-node-dev` |

Verified community nodes (the ones n8n Cloud lists) are still community nodes
for this rule. They clear n8n's review, not the customer's.

## The ladder

Work down, stop at the first rung that does the job. Each step down needs a
reason you could say to the customer's security reviewer.

1. **Dedicated integration node.** Slack, Google Sheets, Postgres, Notion,
   HubSpot, Jira, S3, OpenAI. Run `search_nodes` with the service name before
   anything else; then `get_node` with `mode: "docs"` or
   `explore_node_resources` to confirm the operation exists. Most "the node
   can't do X" claims die here.
2. **HTTP Request with a predefined credential type.** `authentication:
   predefinedCredentialType`, `nodeCredentialType: slackApi` (or
   `googleSheetsOAuth2Api`, `githubApi`, whichever the service has). Native,
   credential-bound, and the service's own credential is reused. The reason to
   be here is one sentence: the operation the dedicated node lacks. Write it in
   the exception record (rule `http-native-available`) when the host is one a
   native node covers.
3. **HTTP Request with a generic credential.** Header Auth, Basic Auth, OAuth2,
   Query Auth, for a service with no node and no predefined credential type.
   Still native. No exception needed.
4. **Native transform before Code.** Edit Fields, Filter, If, Switch, Merge,
   Aggregate, Summarize, Sort, Limit, Remove Duplicates, Split Out, Compare
   Datasets, DateTime, Crypto, HTML, XML, Markdown, Extract from File, Convert
   to File, Compression. Expression first, then an arrow function inside Edit
   Fields, then one of these, then Code (the pack rule in
   `using-n8n-mcp-skills`). A rule written as an If or Switch is one a customer
   reads on the canvas; the same rule inside a Code node is one they ask you
   about.
5. **Code node.** Logic no native node expresses: a non-trivial algorithm, a
   structure walk, a transformation over many fields at once. Never an HTTP
   call: a Code node cannot use a credential, so the key is hardcoded or passed
   as data. The checker reports that as `code-http`.
6. **Community node, with a record.** Only when rungs 1 to 5 fail. Before adding
   it: name the native alternative you rejected and why, name the package and
   its maintainer, and get the customer's security contact to approve it in
   writing. Then record the exception (below). On queue mode the package has to
   be installed on every worker; on n8n Cloud only verified nodes install.
7. **Custom node.** An engagement decision with its own cost line, not a build
   step. `n8n-node-dev`.

## The exception record

Exceptions live in the workflow JSON next to `project` and `folder`, so they
travel with the file into the repo and the review:

```json
"nodePolicy": {
  "exceptions": [
    {
      "node": "PostToLegacyErp",
      "rule": "community-node",
      "reason": "ERP speaks a proprietary binary protocol; no native node, no HTTP API. Package n8n-nodes-acme-erp reviewed by customer security.",
      "approvedBy": "J. Doe (Acme security)",
      "date": "2026-10-14"
    },
    {
      "node": "FetchChannelHistory",
      "rule": "http-native-available",
      "reason": "Slack node has no conversations.history with cursor paging; HTTP Request on the slackApi credential.",
      "approvedBy": "the repository owner",
      "date": "2026-10-21"
    }
  ]
}
```

- `node`: the node name, PascalCase, as on the canvas.
- `rule`: `community-node`, `http-native-available` or `code-http`. Omit to
  cover any rule on that node.
- `reason`: one sentence a reviewer can check. Fewer than ten characters is
  not a reason and fails the gate.
- `approvedBy`: a person. For an engagement, the customer's security or
  platform contact. For a lab build, you.
- `date`: absolute.

A package the customer cleared once for the whole engagement goes in
`deploy/node-policy.json` (committed, no secrets in it):

```json
{
  "allowedNodeTypes": {
    "n8n-nodes-acme-erp.acmeErp": {
      "reason": "Customer-built connector to the ERP; maintained by their platform team.",
      "approvedBy": "J. Doe (Acme security)",
      "date": "2026-10-14"
    }
  }
}
```

An exception that names a node the workflow no longer has is reported as
stale: remove it. An exception without a reason or an approver is an error.

## The check and the gate

`scripts/node_policy_check.py` ships with this skill and is vendored into every
engagement and care kit as `scripts/policy/node_policy_check.py` with the
`./scripts/node-policy.sh` wrapper (`tools/install-tools.sh`). Standard library,
offline, Python 3.9+.

```bash
./scripts/node-policy.sh workflows/dev/order-intake.json
./scripts/node-policy.sh workflows/dev/            # a whole environment
./scripts/node-policy.sh wf.json --policy deploy/node-policy.json --json
```

Output: one summary line per workflow (native, community, code, http,
excepted counts), the findings, then `RESULT: ok` or `RESULT: n finding(s)`.
Exit 1 on an error-level finding.

| Rule | Level | Fires when |
|---|---|---|
| `community-node` | error | A node type outside the two native prefixes, no exception, not in `deploy/node-policy.json` |
| `http-native-available` | error | An HTTP Request or HTTP Request Tool node calls a host a native node covers (Slack, Google APIs, GitHub, Notion, OpenAI, Anthropic, HubSpot, Salesforce, Jira, Microsoft Graph, AWS, and more; the list is `NATIVE_HOSTS` in the script) |
| `code-http` | error | A Code node or Custom Code Tool calls `this.helpers.httpRequest`, `fetch`, `axios`, `urllib` or `requests` |
| `exception-invalid` | error | An exception without `node`, a reason or `approvedBy`, or with an unknown rule |
| `exception-stale` | warn | An exception for a node the workflow does not have |

`tools/deploy_workflow.py` runs it as the fourth hard gate, after tags,
placement and canvas, before any request reaches the instance. No `RESULT:
ok`, no deploy. `N8N_NODE_POLICY_CHECK` points the deploy at another copy of
the checker; `N8N_NODE_POLICY` at another policy file.

The PreToolUse hook `native-node-gate.py` watches every MCP tool that writes
workflow JSON or SDK code to an instance (`n8n_create_workflow`,
`n8n_update_full_workflow`, `n8n_update_partial_workflow`,
`n8n_deploy_template`, `update_workflow`, `create_workflow_from_code`). A
community or custom node type in the payload turns the call into a permission
prompt that names the type and this skill. It does not see the file's
exception record, so the prompt is the moment to say which exception covers
it, or to go back to rung 1.

## Procedure when a coding agent builds the workflow

1. Before the first node: `search_nodes` for every external service in the
   brief. Write the native node per service into the build plan. A service with
   no node gets "HTTP Request, predefined credential `<type>`" or "HTTP
   Request, Header Auth"; never "community node" at this stage.
2. Every rule, routing decision or field mapping is an If, Switch, Filter or
   Edit Fields before it is a Code node. Keep the Code nodes countable on one
   hand and name what each one does that nothing native could.
3. Run `./scripts/node-policy.sh` on the file before `validate.sh`. Fix the
   findings or write the exception. Do not add a blanket exception to make the
   gate pass; that is the thing the gate exists to catch.
4. Hand over the exception list with the workflow. A reviewer reads
   `nodePolicy.exceptions` and `deploy/node-policy.json` and knows every
   non-native surface in the delivery.

## Converting a workflow that was built the other way

A workflow that arrives with dozens of Code nodes (a coding agent's first
draft, an inherited build) is converted, not patched:

1. `./scripts/node-policy.sh` and `node-inventory` for the inventory: how many
   Code nodes, which community types, which HTTP hosts.
2. For each Code node, classify: a rule (If, Switch), a mapping (Edit Fields),
   a shape change (Split Out, Aggregate, Merge), a service call (the dedicated
   node), or real logic (stays). Prompts and long strings move to a Set node or
   a Data Table, not a Code string.
3. Rebuild on dev, keep the golden test set from the original, run both until
   the outputs match. The conversion of this repository's workflow set on 2026-10-21 went
   from 40 Code nodes to 2 this way with the golden set at 5/5 twice.
4. Only then replace the original, by the promotion path.

## Review checklist

- [ ] Every external service in the brief maps to a native node or an HTTP
      Request with a named credential type
- [ ] `node-policy.sh` prints `RESULT: ok` on every file in `workflows/<env>/`
- [ ] Every exception names a node on the canvas, one checkable reason, an
      approver and a date
- [ ] `deploy/node-policy.json` lists only packages the customer approved in
      writing, and the approval is referenced
- [ ] No Code node makes an HTTP call; no token sits in a parameter or a Set
      node
- [ ] Code nodes are few, named for what they do, and each one does something
      no native node expresses
- [ ] On queue mode, every allowed community package is on every worker
      (`n8n-node-dev`); on n8n Cloud, every one is a verified node
