"""
Admin panel — user CRUD, flagged-sender review, lockdown toggle.

Auth: HTTP Basic against ADMIN_PASSWORD env var. If unset (local dev),
auth is bypassed. /api/users stays open so the dashboard autocomplete
works without prompting visitors.
"""
import html
import os
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

import cache


ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()
_security = HTTPBasic(auto_error=False)


def _require_admin(creds: HTTPBasicCredentials | None = Depends(_security)) -> None:
    if not ADMIN_PASSWORD:
        return  # no password set → bypass (dev convenience)
    if creds is None or not secrets.compare_digest(creds.password or "", ADMIN_PASSWORD):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": 'Basic realm="Concierge Admin"'},
        )


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
    import core
    users = cache.list_users()
    flagged = cache.list_flagged(limit=50, only_unresolved=True)
    lockdown = cache.get_setting("lockdown", "0") == "1"
    cc_enabled = cache.get_setting("auto_cc_enabled", "0") == "1"
    s = cache.stats()
    prompts = {m: core.get_prompt(m) for m in ("eng", "sales", "marketing", "support")}
    active_repos = core.get_active_repos()
    active_docs = core.get_active_data_sources()

    user_rows = "\n".join(_user_row(u) for u in users) or (
        '<tr><td colspan="4" style="color:#999;text-align:center;padding:18px">'
        'No users yet. Add one below.</td></tr>'
    )
    flag_rows = "\n".join(_flag_row(f) for f in flagged) or (
        '<tr><td colspan="4" style="color:#999;text-align:center;padding:18px">'
        'Nothing flagged.</td></tr>'
    )
    lock_state = "ON — unknown senders are logged and ignored" if lockdown else "OFF — unknown senders get answered (and still flagged)"
    cc_state = "ON — engineers in allowed domains will be CC'd on replies" if cc_enabled else "OFF — engineers shown in email body only, never CC'd"

    repos_rows = "".join(
        f'<tr><td><a href="https://github.com/{html.escape(r)}" target="_blank">{html.escape(r)}</a></td>'
        f'<td><form method="post" action="/admin/sources/repo/remove" style="margin:0">'
        f'  <input type="hidden" name="repo" value="{html.escape(r)}">'
        f'  <button class="link-btn" type="submit" onclick="return confirm(\'Remove {html.escape(r)} from active list?\')">remove</button>'
        f'</form></td></tr>'
        for r in active_repos
    ) or '<tr><td colspan="2" style="color:#999;text-align:center;padding:18px">No repos active.</td></tr>'

    docs_rows = "".join(
        f'<tr><td><span style="color:#a8479a">{html.escape(d)}</span></td>'
        f'<td><form method="post" action="/admin/sources/doc/remove" style="margin:0">'
        f'  <input type="hidden" name="display_name" value="{html.escape(d)}">'
        f'  <button class="link-btn" type="submit" onclick="return confirm(\'Remove {html.escape(d)} from active list?\')">remove</button>'
        f'</form></td></tr>'
        for d in active_docs
    ) or '<tr><td colspan="2" style="color:#999;text-align:center;padding:18px">No doc sources active.</td></tr>'

    prompts_html = "".join(
        f'<form method="post" action="/admin/prompt" style="margin-bottom:16px">'
        f'  <input type="hidden" name="mode" value="{m}">'
        f'  <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">{_badge(m)}'
        f'    <span style="color:#888;font-size:12px">system prompt</span></div>'
        f'  <textarea name="prompt" rows="6" style="width:100%;font-family:ui-monospace,Menlo,monospace;'
        f'    font-size:12px;padding:10px;border:1px solid #ddd;border-radius:6px;line-height:1.45">'
        f'{html.escape(prompts[m])}</textarea>'
        f'  <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:6px">'
        f'    <button type="submit">Save</button>'
        f'    <button type="submit" name="reset" value="1" formnovalidate '
        f'      onclick="return confirm(\'Reset {m} prompt to default?\')" '
        f'      style="background:#fff;color:#c33;border:1px solid #c33">Reset to default</button>'
        f'  </div>'
        f'</form>'
        for m in ("eng", "sales", "marketing", "support")
    )

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
      <h2 style="margin-top:0">Auto-CC engineers</h2>
      <form method="post" action="/admin/auto_cc" class="toggle">
        <input type="hidden" name="enabled" value="{0 if cc_enabled else 1}">
        <button type="submit">{"Disable" if cc_enabled else "Enable"} auto-CC</button>
        <span class="state">{html.escape(cc_state)}</span>
      </form>
    </div>

    <div class="panel">
      <h2 style="margin-top:0">Pre-warm cache</h2>
      <form method="post" action="/admin/prewarm" class="toggle">
        <button type="submit">▶ Run all demo scenarios</button>
        <span class="state">Runs the 7 questions on /demo through the brain, populating the cache. Misses fire Nia (~25s each); hits are instant. Runs in background — refresh /admin or / to see entries appear.</span>
      </form>
    </div>

    <div class="panel">
      <h2 style="margin-top:0">Indexed sources</h2>
      <p class="sub" style="margin:-4px 0 12px">These are the repos and doc sites the brain searches against. Adding indexes them in Nia and adds them to the active list.</p>

      <h3 style="font-size:13px;margin:12px 0 6px;color:#555">Repositories ({len(active_repos)})</h3>
      <table>
        <thead><tr><th>Repo</th><th></th></tr></thead>
        <tbody>{repos_rows}</tbody>
      </table>
      <form method="post" action="/admin/sources/repo/add" class="form-row">
        <input type="text" name="repo" required placeholder="org/repo (e.g. honojs/middleware)">
        <button type="submit">Add &amp; index</button>
      </form>

      <h3 style="font-size:13px;margin:18px 0 6px;color:#555">Documentation sources ({len(active_docs)})</h3>
      <table>
        <thead><tr><th>Display name</th><th></th></tr></thead>
        <tbody>{docs_rows}</tbody>
      </table>
      <form method="post" action="/admin/sources/doc/add" class="form-row">
        <input type="text" name="url" required placeholder="https://docs.example.com">
        <input type="text" name="display_name" placeholder="display name (e.g. Acme Docs)">
        <button type="submit">Add &amp; index</button>
      </form>
    </div>

    <div class="panel">
      <h2 style="margin-top:0">Mode prompts</h2>
      <p class="sub" style="margin:-4px 0 12px">These are the Claude system prompts for each mode. Edit and save to override; reset clears the override and falls back to the baked-in default.</p>
      {prompts_html}
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

@router.get("/admin", response_class=HTMLResponse, dependencies=[Depends(_require_admin)])
async def admin_page():
    return render()


@router.post("/admin/users/upsert", dependencies=[Depends(_require_admin)])
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


@router.post("/admin/users/delete", dependencies=[Depends(_require_admin)])
async def delete_user(email: str = Form(...)):
    cache.delete_user(email)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/flagged/resolve", dependencies=[Depends(_require_admin)])
async def resolve_flagged(flag_id: int = Form(...)):
    cache.resolve_flagged(flag_id)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/lockdown", dependencies=[Depends(_require_admin)])
async def set_lockdown(enabled: int = Form(...)):
    cache.set_setting("lockdown", "1" if int(enabled) else "0")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/auto_cc", dependencies=[Depends(_require_admin)])
async def set_auto_cc(enabled: int = Form(...)):
    cache.set_setting("auto_cc_enabled", "1" if int(enabled) else "0")
    return RedirectResponse("/admin", status_code=303)


def _set_active_repos(repos: list[str]) -> None:
    cache.set_setting("nia_repos", ",".join(r.strip() for r in repos if r.strip()))


def _set_active_docs(docs: list[str]) -> None:
    cache.set_setting("nia_data_sources", ",".join(d.strip() for d in docs if d.strip()))


@router.post("/admin/sources/repo/add", dependencies=[Depends(_require_admin)])
async def add_repo(repo: str = Form(...)):
    import core
    repo = (repo or "").strip()
    if "/" not in repo:
        raise HTTPException(status_code=400, detail="expected 'org/repo'")
    try:
        await core.nia_index_repo(repo)
    except Exception as e:
        # Common case: Nia rejects private/auth-required repos. Surface but still accept
        # the addition so the operator can fix the index out-of-band.
        print(f"[admin] nia_index_repo failed for {repo}: {e}")
    active = core.get_active_repos()
    if repo not in active:
        active.append(repo)
    _set_active_repos(active)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/sources/repo/remove", dependencies=[Depends(_require_admin)])
async def remove_repo(repo: str = Form(...)):
    import core
    active = [r for r in core.get_active_repos() if r != repo.strip()]
    _set_active_repos(active)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/sources/doc/add", dependencies=[Depends(_require_admin)])
async def add_doc(url: str = Form(...), display_name: str = Form("")):
    import core
    url = (url or "").strip()
    name = (display_name or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="expected an http(s) URL")
    try:
        src = await core.nia_index_doc(url, display_name=name or None)
    except Exception as e:
        print(f"[admin] nia_index_doc failed for {url}: {e}")
        src = {}
    label = name or src.get("display_name") or url
    active = core.get_active_data_sources()
    if label not in active:
        active.append(label)
    _set_active_docs(active)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/sources/doc/remove", dependencies=[Depends(_require_admin)])
async def remove_doc(display_name: str = Form(...)):
    import core
    active = [d for d in core.get_active_data_sources() if d != display_name.strip()]
    _set_active_docs(active)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/prompt", dependencies=[Depends(_require_admin)])
async def set_prompt(
    mode: str = Form(...),
    prompt: str = Form(""),
    reset: str = Form(""),
):
    if mode not in ("eng", "sales", "marketing", "support"):
        raise HTTPException(status_code=400, detail="invalid mode")
    if reset:
        cache.set_setting(f"prompt_{mode}", "")
    else:
        cache.set_setting(f"prompt_{mode}", prompt)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/prewarm", dependencies=[Depends(_require_admin)])
async def prewarm_cache(background_tasks: BackgroundTasks):
    """Run all canned demo scenarios in the background. Cache hits are instant;
    misses fire Nia (~25s each). Returns to /admin immediately."""
    import demo
    background_tasks.add_task(_run_prewarm, demo._SCENARIOS)
    return RedirectResponse("/admin", status_code=303)


async def _run_prewarm(scenarios) -> None:
    import core
    for sid, _title, _why, question, mode, sender in scenarios:
        try:
            await core.answer_codebase_question(question, sender=sender, mode=mode)
            print(f"[prewarm] {sid} done")
        except Exception as e:
            print(f"[prewarm] {sid} failed: {e}")


@router.get("/api/users")
async def api_users():
    return cache.list_users()
