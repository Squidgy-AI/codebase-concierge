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

After deploy, hit `https://<your-render-url>/setup` — it walks you through every remaining step:

1. Verifies all three API keys are valid
2. Lets you index repos in Nia from the form
3. Auto-registers the AgentMail webhook (one click)
4. Reminds you to set `ADMIN_PASSWORD` if you haven't
5. Adds your first user

Once every check is green, click **Finish setup** and `/setup` from then on redirects to `/admin`.

You'll also want to add these env vars on Render:

| Env var | Why |
|---|---|
| `ADMIN_PASSWORD` | Locks `/admin` and `/skill/ask` behind HTTP Basic. **Required.** Without it the admin panel returns 503. |
| `WEBHOOK_SIGNING_SECRET` | The `secret` AgentMail returned when the webhook was created. Required for production webhook verification. |
| `SKILL_API_KEY` *(optional)* | Bearer token for programmatic `/skill/ask` callers (Roam agent, OpenClaw, CLI). |
| `CACHE_DB_PATH` | Set to `/disk/cache.db` if you attached a Render persistent disk so Q&A memory survives deploys. |
| `RENDER_EXTERNAL_URL` | Set automatically by Render. Used by `/setup` to know where the webhook should point. |

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
- `main.py` — FastAPI channels (webhook + /skill/ask + setup wizard)
- `setup_page.py` — first-run onboarding wizard (`/setup`)
- `admin.py` — admin panel (users, modes, sources, prompts, insights)
- `dashboard.py` — live Q&A log at `/`
- `demo.py` — beat-by-beat demo runbook at `/demo`
- `insights.py` — capability-gap analysis (`/admin/insights`)
- `skills/codebase-concierge/SKILL.md` — OpenClaw skill manifest
- `render.yaml` — one-click Render blueprint

## Troubleshooting

**`/admin` returns 503 "Admin disabled"**
You haven't set `ADMIN_PASSWORD`. Required by default. Add it to your Render service env and redeploy. (For local dev only, you can set `ALLOW_INSECURE_ADMIN=1` to bypass.)

**Anthropic API key returns 401 "invalid x-api-key"**
Almost always a copy-paste issue. The key may have leading invisible characters (zero-width space, BOM). The app strips ASCII non-printables on startup, but if your key is *truncated*, it'll still fail. Real Anthropic keys are ~108 chars and end in `AA`. Re-copy from the console.

**Cache empty after every deploy**
Render's free disk is ephemeral. Attach a $0.25/mo persistent disk at `/disk` and set `CACHE_DB_PATH=/disk/cache.db`.

**Render build fails: `ModuleNotFoundError`**
Verify your `requirements.txt` is the unchanged upstream copy. If you've added imports, also add their packages.

**Webhook fires but no reply lands**
Check Render logs for the latest webhook hit. Common causes: missing `ADMIN_PASSWORD` blocks `/skill/ask` (which the chat panel uses but webhook doesn't — webhook should still answer), Anthropic 401, Nia quota exhausted, or `WEBHOOK_SIGNING_SECRET` mismatch.

**Cold-start latency on first email after idle**
Render free tier sleeps after 15 min idle, takes ~30s to wake. AgentMail will retry the webhook automatically, but the first ask after wakeup will be slow. Either upgrade to a paid plan or ping `/healthz` periodically.

**My docs upload says "view" but the link doesn't work**
The Nia source ID didn't resolve from the display_name. Re-add the doc with a unique display_name, or look up the ID directly in `/admin/insights` logs.

## License

MIT — see [LICENSE](LICENSE).
