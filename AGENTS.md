# Working in this repository

- Edit `workflows/voice-booking/build/gen.py`, never the workflow JSON. The
  generator is the readable form; regenerate, then deploy with
  `build/deploy.py`.
- Native nodes first. Read `.agents/skills/n8n-native-nodes-first/SKILL.md`
  before adding or replacing a node. A rule is an If or a Switch, a mapping
  is Edit Fields, a service call is the dedicated node or HTTP Request on a
  credential. A Code node needs a reason; a community node needs a recorded
  exception. `scripts/node-policy.sh` must print `RESULT: ok` before a deploy.
- Every workflow carries `tags`, `project` and `folder`; the deploy refuses
  anything else and stops when the project cannot reach a credential or a
  Data Table.
- Secrets come from the environment (`.env.example`), never from a text
  field, a Set node or a commit.
- A change is done when `scripts/ar-golden-run.py` scores 10/10 twice in a row
  (SETUP.md, section 4).
