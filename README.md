# Codebase Concierge

Email-driven agent that answers natural-language questions about a codebase. Email it → Nia retrieves code context → Claude composes a cited answer → AgentMail sends a threaded reply. Auto-CCs the engineer who last touched the cited code (via `git blame`). Caches answers so duplicate questions return instantly. Routes by sender or subject tag into eng / sales / marketing / support voices.

Built for the OpenClaw Hackathon (Eragon × Nozomio × AgentMail).

## Deploy your own

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Squidgy-AI/codebase-concierge)

Click the button, point Render at your fork (or the upstream repo), and provide these secrets when prompted:

| Env var | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/settings/keys |
| `NIA_API_KEY` | https://app.trynia.ai → API Keys |
| `AGENTMAIL_API_KEY` | https://app.agentmail.to → API Keys |
| `AGENTMAIL_INBOX_ID` | Your AgentMail inbox email, e.g. `myteam@agentmail.to` |
| `NIA_REPOS` | Comma-separated repos you've indexed in Nia, e.g. `acme/api,acme/web` |
| `NIA_DATA_SOURCES` *(optional)* | Display names of indexed doc sites, e.g. `Acme Docs` |
| `SENDER_MODES` *(optional)* | Routing map, e.g. `cs@acme.com:support,@partner.com:sales` |

After deploy:
1. Copy the Render URL (e.g. `https://codebase-concierge-xxx.onrender.com`)
2. Register an AgentMail webhook pointing at `<your-url>/webhook` for `message.received` events
3. Email your AgentMail inbox a question — reply lands in seconds

## Channels

The reasoning core (`core.answer_codebase_question`) is channel-agnostic. Two channels ship today, both backed by the same brain:

- **`POST /webhook`** — AgentMail inbound mail → threaded reply
- **`POST /skill/ask`** — JSON request, returns `{answer_html, answer_md, sources, engineers, cache_hit, mode}`

```bash
curl -X POST https://codebase-concierge.onrender.com/skill/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"How does Hono handle middleware composition?"}'
```

## Modes

| Mode | Trigger | Voice |
|---|---|---|
| `eng` | default | Code-clarifying answers for non-engineers |
| `sales` | `[sales]` in subject *or* sender match | Yes/no/partial-yes capability answers, no code in prose |
| `marketing` | `[marketing]` in subject *or* sender match | Hook + bullets + suggested headline |
| `support` | `[support]` in subject *or* sender match | Labeled triage: BUG / EXPECTED / NEEDS-MORE-INFO |

Subject tag wins over sender map; sender map wins over default.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in keys

mkdir -p repos
git clone --depth=300 https://github.com/honojs/hono.git repos/hono   # or your own repos
git clone --depth=300 https://github.com/honojs/node-server.git repos/node-server

uvicorn main:app --reload --port 8000
ngrok http 8000  # then register the public URL with AgentMail
```

## Files

- `core.py` — reasoning core (Nia + Claude + cache + git blame + mode routing)
- `cache.py` — SQLite Q&A memory ("previously answered for X on Y")
- `main.py` — FastAPI channels (webhook + /skill/ask)
- `skills/codebase-concierge/SKILL.md` — OpenClaw skill manifest
- `render.yaml` — one-click Render blueprint
- `CLAUDE.md` — full hackathon project context
