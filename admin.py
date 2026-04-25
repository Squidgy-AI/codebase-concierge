"""
Admin panel — user CRUD, flagged-sender review, lockdown toggle.

Single-tenant, no auth (hackathon scope). Stick behind a reverse proxy or
add an X-Admin-Token check before exposing publicly.
"""
import html

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

import cache


router = APIRouter()

_MODE_COLORS = {
    "eng": ("#0a7d3e", "#dcf5e6"),
    "sales": ("#0a3a99", "#e0eafc"),
    "marketing": ("#a8479a", "#fbe6f5"),
    "support": ("#b3530a", "#fcecdc"),
}


def _badge(mode: str) -> str:
    fg, bg = _MODE_COLORS.get(mode, _MODE_COLORS["eng"])
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:4px;'
        f'font-size:11px;font-weight:600;letter-spacing:0.5px;'
        f'color:{fg};background:{bg};text-transform:uppercase">{html.escape(mode)}</span>'
    )


def _user_row(u: dict) -> str:
    return (
        f'<tr>'
        f'<td>{html.escape(u["email"])}</td>'
        f'<td>{html.escape(u.get("display_name") or "")}</td>'
        f'<td>{_badge(u["default_mode"])}</td>'
        f'<td><form method="post" action="/admin/users/delete" style="margin:0">'
        f'  <input type="hidden" name="email" value="{html.escape(u["email"])}">'
        f'  <button class="link-btn" type="submit" onclick="return confirm(\'Delete {html.escape(u["email"])}?\')">delete</button>'
        f'</form></td>'
        f'</tr>'
    )


def _flag_row(f: dict) -> str:
    sender = html.escape(f["sender"] or "")
    subject = html.escape(f.get("subject") or "")
    return (
        f'<tr>'
        f'<td>{sender}</td>'
        f'<td>{subject}</td>'
        f'<td style="font-size:12px;color:#888">{f.get("created_at","")}</td>'
        f'<td><form method="post" action="/admin/flagged/resolve" style="margin:0">'
        f'  <input type="hidden" name="flag_id" value="{f["id"]}">'
        f'  <button class="link-btn" type="submit">dismiss</button>'
        f'</form></td>'
        f'</tr>'
    )


def render() -> str:
    users = cache.list_users()
    flagged = cache.list_flagged(limit=50, only_unresolved=True)
    lockdown = cache.get_setting("lockdown", "0") == "1"
    s = cache.stats()

    user_rows = "\n".join(_user_row(u) for u in users) or (
        '<tr><td colspan="4" style="color:#999;text-align:center;padding:18px">'
        'No users yet. Add one below.</td></tr>'
    )
    flag_rows = "\n".join(_flag_row(f) for f in flagged) or (
        '<tr><td colspan="4" style="color:#999;text-align:center;padding:18px">'
        'Nothing flagged.</td></tr>'
    )
    lock_state = "ON — unknown senders are logged and ignored" if lockdown else "OFF — unknown senders get answered (and still flagged)"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Concierge — Admin</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif;
            background: #fafafa; color: #222; margin: 0; padding: 24px; }}
    .wrap {{ max-width: 880px; margin: 0 auto; }}
    h1 {{ font-size: 22px; margin: 0 0 4px 0; }}
    h2 {{ font-size: 16px; margin: 24px 0 10px 0; }}
    .sub {{ color: #888; font-size: 13px; margin-bottom: 18px; }}
    .nav a {{ margin-right: 16px; color: #0a3a99; font-size: 13px; }}
    .panel {{ background: #fff; border-radius: 8px; padding: 16px 20px;
              box-shadow: 0 1px 2px rgba(0,0,0,0.04); margin-bottom: 16px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; }}
    th {{ font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }}
    .form-row {{ display: flex; gap: 8px; margin-top: 12px; }}
    input[type=text], input[type=email], select {{
      font-family: inherit; font-size: 14px; padding: 7px 10px;
      border: 1px solid #ddd; border-radius: 6px; background: #fff;
    }}
    input[type=email], input[type=text] {{ flex: 1; }}
    select {{ flex: 0 0 110px; }}
    button {{ background: #0a3a99; color: white; border: none; border-radius: 6px;
              cursor: pointer; padding: 8px 16px; font-size: 14px; font-weight: 500; }}
    button:hover {{ background: #0c46b8; }}
    .link-btn {{ background: none; color: #c33; padding: 0; font-size: 12px;
                 text-decoration: underline; cursor: pointer; }}
    .link-btn:hover {{ background: none; color: #900; }}
    .toggle {{ display: flex; align-items: center; gap: 12px; }}
    .toggle .state {{ font-size: 13px; color: #555; }}
    .stat-row {{ display: flex; gap: 24px; }}
    .stat-num {{ font-size: 22px; font-weight: 600; }}
    .stat-label {{ font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Concierge — Admin</h1>
    <div class="nav"><a href="/">← live log</a></div>

    <div class="panel">
      <div class="stat-row">
        <div><div class="stat-num">{len(users)}</div><div class="stat-label">Users</div></div>
        <div><div class="stat-num">{s["flagged_senders"]}</div><div class="stat-label">Flagged</div></div>
        <div><div class="stat-num">{s["questions"]}</div><div class="stat-label">Questions</div></div>
        <div><div class="stat-num">{s["cache_hits"]}</div><div class="stat-label">Cache hits</div></div>
      </div>
    </div>

    <div class="panel">
      <h2 style="margin-top:0">Lockdown</h2>
      <form method="post" action="/admin/lockdown" class="toggle">
        <input type="hidden" name="enabled" value="{0 if lockdown else 1}">
        <button type="submit">{"Disable" if lockdown else "Enable"} lockdown</button>
        <span class="state">{html.escape(lock_state)}</span>
      </form>
    </div>

    <div class="panel">
      <h2 style="margin-top:0">Users ({len(users)})</h2>
      <table>
        <thead><tr><th>Email</th><th>Name</th><th>Mode</th><th></th></tr></thead>
        <tbody>{user_rows}</tbody>
      </table>
      <form method="post" action="/admin/users/upsert" class="form-row">
        <input type="email" name="email" required placeholder="email@team.com">
        <input type="text" name="display_name" placeholder="display name (optional)">
        <select name="default_mode" required>
          <option value="eng">eng</option>
          <option value="sales">sales</option>
          <option value="marketing">marketing</option>
          <option value="support">support</option>
        </select>
        <button type="submit">Add / update</button>
      </form>
    </div>

    <div class="panel">
      <h2 style="margin-top:0">Flagged senders ({len(flagged)})</h2>
      <div class="sub">Unknown emails to <code>{html.escape(_inbox_label())}</code> get logged here. Add them above to assign a mode, or dismiss to ignore.</div>
      <table>
        <thead><tr><th>From</th><th>Subject</th><th>When</th><th></th></tr></thead>
        <tbody>{flag_rows}</tbody>
      </table>
    </div>

  </div>
</body>
</html>"""


def _inbox_label() -> str:
    import os
    return os.environ.get("AGENTMAIL_INBOX_ID", "the inbox")


# ---------- Routes ----------

@router.get("/admin", response_class=HTMLResponse)
async def admin_page():
    return render()


@router.post("/admin/users/upsert")
async def upsert_user(
    email: str = Form(...),
    display_name: str = Form(""),
    default_mode: str = Form(...),
):
    try:
        cache.upsert_user(email, display_name or None, default_mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/users/delete")
async def delete_user(email: str = Form(...)):
    cache.delete_user(email)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/flagged/resolve")
async def resolve_flagged(flag_id: int = Form(...)):
    cache.resolve_flagged(flag_id)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/lockdown")
async def set_lockdown(enabled: int = Form(...)):
    cache.set_setting("lockdown", "1" if int(enabled) else "0")
    return RedirectResponse("/admin", status_code=303)


@router.get("/api/users")
async def api_users():
    return cache.list_users()
