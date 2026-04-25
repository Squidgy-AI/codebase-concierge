# Codebase Concierge

Email-driven agent that answers questions about a codebase. Email it → Nia retrieves code context → Claude composes a cited answer → AgentMail sends a threaded reply.

OpenClaw Hackathon (Eragon × Nozomio × AgentMail).

## Day-of setup checklist

### Before 11:00 AM
- [ ] Anthropic API key ready
- [ ] Sign up trynia.ai, grab API key
- [ ] Sign up agentmail.to, grab API key
- [ ] Render account ready
- [ ] ngrok installed (`brew install ngrok`)

### First 30 min (de-risk)
1. **Skip the Claude Code plugins** — Nia isn't in the marketplace yet. Go API-direct.
2. Pick demo repos (Hono + adapter + example, or single repo if pressed)
3. Index via Nia REST API (curl or httpx): capture each `repo_id` → put comma-separated in `NIA_REPO_IDS`
4. Create AgentMail inbox via API, get `inbox_id` → put in `.env`
5. Run locally: `uvicorn main:app --reload`
6. `ngrok http 8000` → register webhook URL on AgentMail inbox
7. Send test email → verify webhook fires (even if reply is junk)

### Build (next 5h)
Use Claude Code with both plugins installed. Hand it `CLAUDE.md` and let it iterate.

### Deploy to Render (~30 min before submission)
- Push to GitHub
- New Render web service from repo → it picks up `render.yaml`
- Set env vars in Render dashboard
- Update AgentMail webhook URL to Render URL
- Smoke test

## Local run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in keys
uvicorn main:app --reload --port 8000
```

## Files
- `main.py` — single-file FastAPI agent (~150 lines)
- `CLAUDE.md` — full project context for Claude Code
- `render.yaml` — one-click Render deploy
