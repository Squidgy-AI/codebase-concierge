"""
First-run onboarding wizard. Single page, progressive checklist.

Anyone can hit /setup until setup_complete=1 is set in the settings table.
After that, /setup redirects to /admin.
"""
import html
import os

import httpx

import cache
import core


AGENTMAIL_BASE = "https://api.agentmail.to/v0"


# ---------- Status checks ----------

async def _check_anthropic() -> tuple[bool, str]:
    try:
        # A 1-token request is the cheapest verification.
        msg = core.claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2,
            messages=[{"role": "user", "content": "ping"}],
        )
        return True, f"OK ({msg.model})"
    except Exception as e:
        return False, str(e)[:140]


async def _check_nia() -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{core.NIA_BASE}/v2/repositories",
                headers={"Authorization": f"Bearer {core.NIA_API_KEY}"},
            )
            r.raise_for_status()
            n = len(r.json() or [])
            return True, f"OK — {n} repo(s) indexed"
    except Exception as e:
        return False, str(e)[:140]


async def _check_agentmail() -> tuple[bool, str]:
    inbox = os.environ.get("AGENTMAIL_INBOX_ID", "")
    api_key = os.environ.get("AGENTMAIL_API_KEY", "")
    if not inbox or not api_key:
        return False, "AGENTMAIL_INBOX_ID or AGENTMAIL_API_KEY not set"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{AGENTMAIL_BASE}/inboxes",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            r.raise_for_status()
            data = r.json()
            inboxes = data.get("inboxes") if isinstance(data, dict) else data
            for ib in inboxes or []:
                if (ib.get("inbox_id") or ib.get("email") or "").lower() == inbox.lower():
                    return True, f"OK — inbox {inbox}"
            return False, f"inbox {inbox} not found in account"
    except Exception as e:
        return False, str(e)[:140]


async def _check_webhook() -> tuple[bool, str]:
    api_key = os.environ.get("AGENTMAIL_API_KEY", "")
    if not api_key:
        return False, "AGENTMAIL_API_KEY missing"
    target = _public_webhook_url()
    if not target:
        return False, "could not determine public URL"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{AGENTMAIL_BASE}/webhooks",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            r.raise_for_status()
            for wh in (r.json() or {}).get("webhooks", []):
                if wh.get("url") == target and "message.received" in (wh.get("event_types") or []):
                    return True, f"OK — registered ({wh.get('webhook_id', '?')[:18]}…)"
            return False, f"no webhook found pointing at {target}"
    except Exception as e:
        return False, str(e)[:140]


def _public_webhook_url() -> str:
    """Render sets RENDER_EXTERNAL_URL automatically; otherwise the operator
    can set PUBLIC_URL manually."""
    base = (os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("PUBLIC_URL") or "").rstrip("/")
    if not base:
        return ""
    return base + "/webhook"


# ---------- Webhook auto-registration ----------

async def register_webhook() -> dict:
    api_key = os.environ.get("AGENTMAIL_API_KEY", "")
    target = _public_webhook_url()
    if not api_key:
        raise RuntimeError("AGENTMAIL_API_KEY not set")
    if not target:
        raise RuntimeError("Set RENDER_EXTERNAL_URL or PUBLIC_URL so we know where the webhook should point.")
    async with httpx.AsyncClient(timeout=15) as c:
        # If a webhook for our URL already exists, return it untouched.
        r = await c.get(
            f"{AGENTMAIL_BASE}/webhooks",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        r.raise_for_status()
        for wh in (r.json() or {}).get("webhooks", []):
            if wh.get("url") == target:
                return {"status": "already_registered", "webhook": wh}
        # Otherwise create.
        r = await c.post(
            f"{AGENTMAIL_BASE}/webhooks",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "url": target,
                "event_types": ["message.received"],
                "client_id": "codebase-concierge-auto",
            },
        )
        r.raise_for_status()
        return {"status": "created", "webhook": r.json()}


# ---------- Page rendering ----------

def is_complete() -> bool:
    return cache.get_setting("setup_complete", "0") == "1"


def mark_complete() -> None:
    cache.set_setting("setup_complete", "1")


def _step(num: int, title: str, ok: bool, detail: str, fix_html: str = "") -> str:
    icon = "✅" if ok else "⚠️"
    bg = "#dcf5e6" if ok else "#fff8d6"
    return f"""
    <section class="step" style="background:{bg}">
      <div class="step-head">
        <span class="step-num">{icon} {num}</span>
        <span class="step-title">{html.escape(title)}</span>
      </div>
      <div class="step-detail">{html.escape(detail)}</div>
      {f'<div class="step-fix">{fix_html}</div>' if fix_html and not ok else ""}
    </section>
    """


async def render() -> str:
    # Run live checks. Each one is best-effort.
    results: dict[str, tuple[bool, str]] = {}
    for name, fn in [
        ("anthropic", _check_anthropic),
        ("nia", _check_nia),
        ("agentmail", _check_agentmail),
        ("webhook", _check_webhook),
    ]:
        try:
            results[name] = await fn()
        except Exception as e:
            results[name] = (False, f"check failed: {e}")

    repos = core.get_active_repos()
    docs = core.get_active_data_sources()
    users = cache.list_users()
    has_admin_pw = bool(os.environ.get("ADMIN_PASSWORD", "").strip())

    public_url = _public_webhook_url() or "(set RENDER_EXTERNAL_URL or PUBLIC_URL)"

    a_ok, a_msg = results["anthropic"]
    n_ok, n_msg = results["nia"]
    am_ok, am_msg = results["agentmail"]
    wh_ok, wh_msg = results["webhook"]

    repos_ok = bool(repos)
    users_ok = bool(users)

    all_green = all([a_ok, n_ok, am_ok, wh_ok, repos_ok, has_admin_pw, users_ok])

    steps = [
        _step(1, "Anthropic API", a_ok, a_msg,
              '<a href="https://console.anthropic.com/settings/keys" target="_blank">Open Anthropic console</a> → create key → set <code>ANTHROPIC_API_KEY</code> in your service env.'),
        _step(2, "Nia API", n_ok, n_msg,
              '<a href="https://app.trynia.ai" target="_blank">Open Nia dashboard</a> → API keys → set <code>NIA_API_KEY</code>.'),
        _step(3, "AgentMail account + inbox", am_ok, am_msg,
              '<a href="https://app.agentmail.to" target="_blank">Open AgentMail</a> → create an inbox → set <code>AGENTMAIL_API_KEY</code> and <code>AGENTMAIL_INBOX_ID</code> (the inbox email).'),
        _step(4, "Indexed repos / docs", repos_ok,
              f'{len(repos)} repo(s), {len(docs)} doc source(s) active' if repos_ok else 'no repos active',
              '<a href="/admin">Open /admin</a> → Indexed sources → add the repo(s) you want the brain to query (e.g. <code>your-org/your-repo</code>).'),
        _step(5, "AgentMail webhook → this service", wh_ok, wh_msg,
              f'Public URL: <code>{html.escape(public_url)}</code><br>'
              f'<form method="post" action="/setup/register_webhook" style="margin-top:8px"><button type="submit">▶ Register webhook automatically</button></form>'),
        _step(6, "Admin password", has_admin_pw,
              "ADMIN_PASSWORD set" if has_admin_pw else "/admin is currently locked (503) — set ADMIN_PASSWORD on your service env to unlock.",
              "Render dashboard → service → Environment → add <code>ADMIN_PASSWORD</code> with a strong value, save, service auto-restarts."),
        _step(7, "First user", users_ok,
              f'{len(users)} user(s) configured' if users_ok else 'no users — anyone emailing the inbox will be flagged as unknown',
              '<a href="/admin">Open /admin</a> → Users → add yourself with a default mode.'),
    ]

    finish = ""
    if all_green and not is_complete():
        finish = (
            '<form method="post" action="/setup/complete" style="margin-top:24px">'
            '<button type="submit" style="background:#0a7d3e;color:#fff;border:none;padding:10px 20px;'
            'border-radius:6px;font-size:15px;font-weight:600;cursor:pointer">✓ All checks pass — finish setup</button>'
            '<span style="margin-left:10px;color:#555;font-size:13px">After this, /setup will redirect to /admin.</span>'
            '</form>'
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Codebase Concierge — Setup</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif;
            background: #fafafa; color: #222; margin: 0; padding: 24px; }}
    .wrap {{ max-width: 760px; margin: 0 auto; }}
    h1 {{ font-size: 24px; margin: 0 0 4px; }}
    .sub {{ color: #888; font-size: 13px; margin-bottom: 20px; }}
    .step {{ border-radius: 8px; padding: 14px 18px; margin-bottom: 12px;
             box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
    .step-head {{ display: flex; gap: 12px; align-items: baseline; margin-bottom: 6px; }}
    .step-num {{ font-size: 14px; font-weight: 700; }}
    .step-title {{ font-size: 15px; font-weight: 600; }}
    .step-detail {{ font-size: 13px; color: #444; }}
    .step-fix {{ font-size: 13px; color: #555; margin-top: 8px;
                 padding: 10px; background: rgba(255,255,255,0.6); border-radius: 6px; }}
    code {{ background: #f0f0f0; padding: 1px 5px; border-radius: 3px; font-size: 12px; }}
    a {{ color: #0a3a99; }}
    button {{ background: #0a3a99; color: white; border: none;
              border-radius: 6px; padding: 8px 14px; font-size: 13px;
              font-weight: 500; cursor: pointer; }}
    button:hover {{ background: #0c46b8; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Codebase Concierge — first-run setup</h1>
    <div class="sub">Once every step is green, click "finish setup" and this page redirects to /admin from then on.</div>
    {''.join(steps)}
    {finish}
    <p style="font-size:12px;color:#888;margin-top:24px">Already done? <a href="/admin">go to admin →</a></p>
  </div>
</body>
</html>"""
