"""
Admin panel — user CRUD, flagged-sender review, lockdown toggle.

Auth: HTTP Basic against ADMIN_PASSWORD env var. If unset (local dev),
auth is bypassed. /api/users stays open so the dashboard autocomplete
works without prompting visitors.
"""
import html
import os
import re
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi import Body
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

import cache


ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()
ALLOW_INSECURE_ADMIN = os.environ.get("ALLOW_INSECURE_ADMIN", "").strip() == "1"
_security = HTTPBasic(auto_error=False)


def _require_admin(creds: HTTPBasicCredentials | None = Depends(_security)) -> None:
    if not ADMIN_PASSWORD:
        if ALLOW_INSECURE_ADMIN:
            return  # explicit local-dev opt-in
        raise HTTPException(
            status_code=503,
            detail="Admin disabled: ADMIN_PASSWORD not configured. "
                   "Set ADMIN_PASSWORD, or ALLOW_INSECURE_ADMIN=1 for local dev only.",
        )
    if creds is None or not secrets.compare_digest(creds.password or "", ADMIN_PASSWORD):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": 'Basic realm="Concierge Admin"'},
        )


router = APIRouter()

def _badge(mode: str) -> str:
    """Pull color from core.all_modes() so custom modes render with their own palette."""
    import core
    info = core.all_modes().get(mode)
    fg, bg = info["color"] if info else ("#444", "#eee")
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


def _gap_row(g: dict) -> str:
    askers = ", ".join(html.escape(a) for a in g["askers"][:5])
    if len(g["askers"]) > 5:
        askers += f' <span style="color:#bbb">+{len(g["askers"])-5} more</span>'
    other_q = ""
    if len(g["all_questions"]) > 1:
        other_q = (
            '<div class="all-q">also asked: '
            + " · ".join(html.escape(q) for q in g["all_questions"][1:4])
            + '</div>'
        )
    return (
        f'<tr>'
        f'<td><span class="ask-count">{g["ask_count"]}</span><div class="askers">{g["asker_count"]} {"asker" if g["asker_count"]==1 else "askers"}</div></td>'
        f'<td><strong>{html.escape(g["exemplar_question"])}</strong>{other_q}'
        f'  <div class="askers" style="margin-top:6px">{askers}</div></td>'
        f'<td><div class="snippet">{html.escape(g["snippet"])}</div></td>'
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


def render(nia_sources: list[dict] | None = None, nonce: str = "") -> str:
    nonce_attr = f' nonce="{html.escape(nonce)}"' if nonce else ""
    import core
    users = cache.list_users()
    flagged = cache.list_flagged(limit=50, only_unresolved=True)
    lockdown = cache.get_setting("lockdown", "0") == "1"
    cc_enabled = cache.get_setting("auto_cc_enabled", "0") == "1"
    s = cache.stats()
    modes = core.all_modes()
    prompts = {mid: core.get_prompt(mid) for mid in modes.keys()}
    custom_modes = [m for mid, m in modes.items() if not m["builtin"]]
    active_repos = core.get_active_repos()
    active_docs = core.get_active_data_sources()
    cache_ttl_hours = cache.get_setting("cache_ttl_hours", "168") or "168"

    # Map active doc display_names → Nia source_id so we can offer a "view" link.
    # Caller passes the pre-fetched sources list; admin_page() handler does the await.
    doc_id_by_name: dict[str, str] = {}
    for src in (nia_sources or []):
        if not isinstance(src, dict) or not src.get("id"):
            continue
        for key in ("display_name", "identifier"):
            val = src.get(key)
            if isinstance(val, str) and val:
                doc_id_by_name.setdefault(val, src["id"])

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

    def _repo_row(entry: str) -> str:
        repo, _, branch = entry.partition("@")
        repo = repo.strip()
        branch = branch.strip()
        gh_url = f"https://github.com/{html.escape(repo)}"
        if branch:
            gh_url += f"/tree/{html.escape(branch)}"
            label = f'{html.escape(repo)} <span style="color:#888;font-size:11px">@ {html.escape(branch)}</span>'
        else:
            label = html.escape(repo)
        return (
            f'<tr><td><a href="{gh_url}" target="_blank">{label}</a></td>'
            f'<td><form method="post" action="/admin/sources/repo/remove" style="margin:0">'
            f'  <input type="hidden" name="repo" value="{html.escape(entry)}">'
            f'  <button class="link-btn" type="submit" onclick="return confirm(\'Remove {html.escape(entry)} from active list?\')">remove</button>'
            f'</form></td></tr>'
        )
    repos_rows = "".join(_repo_row(r) for r in active_repos) or (
        '<tr><td colspan="2" style="color:#999;text-align:center;padding:18px">No repos active.</td></tr>'
    )

    def _doc_row(d: str) -> str:
        sid = doc_id_by_name.get(d)
        view = (
            f'<a href="/admin/source/{html.escape(sid)}/view" style="margin-right:10px;font-size:12px">view</a>'
            if sid else
            '<span style="color:#bbb;font-size:12px;margin-right:10px" title="Nia source ID not resolved">view</span>'
        )
        return (
            f'<tr><td><span style="color:#a8479a">{html.escape(d)}</span></td>'
            f'<td>{view}'
            f'<form method="post" action="/admin/sources/doc/remove" style="margin:0;display:inline">'
            f'  <input type="hidden" name="display_name" value="{html.escape(d)}">'
            f'  <button class="link-btn" type="submit" onclick="return confirm(\'Remove {html.escape(d)} from active list?\')">remove</button>'
            f'</form></td></tr>'
        )
    docs_rows = "".join(_doc_row(d) for d in active_docs) or (
        '<tr><td colspan="2" style="color:#999;text-align:center;padding:18px">No doc sources active.</td></tr>'
    )

    def _builtin_prompt_form(mid: str) -> str:
        return (
            f'<form method="post" action="/admin/prompt" style="margin-bottom:16px">'
            f'  <input type="hidden" name="mode" value="{mid}">'
            f'  <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">{_badge(mid)}'
            f'    <span style="color:#888;font-size:12px">system prompt · built-in</span></div>'
            f'  <textarea name="prompt" rows="6" style="width:100%;font-family:ui-monospace,Menlo,monospace;'
            f'    font-size:12px;padding:10px;border:1px solid #ddd;border-radius:6px;line-height:1.45">'
            f'{html.escape(prompts[mid])}</textarea>'
            f'  <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:6px">'
            f'    <button type="submit">Save</button>'
            f'    <button type="submit" name="reset" value="1" formnovalidate '
            f'      onclick="return confirm(\'Reset {mid} prompt to default?\')" '
            f'      style="background:#fff;color:#c33;border:1px solid #c33">Reset to default</button>'
            f'  </div>'
            f'</form>'
        )

    def _custom_mode_form(m: dict) -> str:
        fg, bg = m["color"]
        return (
            f'<form method="post" action="/admin/custom_mode/upsert" style="margin-bottom:18px;border:1px solid #eee;border-radius:6px;padding:12px">'
            f'  <input type="hidden" name="id" value="{html.escape(m["id"])}">'
            f'  <div style="display:flex;gap:10px;align-items:center;margin-bottom:8px">{_badge(m["id"])}'
            f'    <input type="text" name="label" value="{html.escape(m["label"])}" placeholder="label" style="flex:1;font-size:13px;padding:5px 8px;border:1px solid #ddd;border-radius:4px">'
            f'    <input type="color" name="color_fg" value="{html.escape(fg)}" title="text">'
            f'    <input type="color" name="color_bg" value="{html.escape(bg)}" title="background">'
            f'  </div>'
            f'  <textarea name="prompt" rows="6" style="width:100%;font-family:ui-monospace,Menlo,monospace;'
            f'    font-size:12px;padding:10px;border:1px solid #ddd;border-radius:6px;line-height:1.45">'
            f'{html.escape(prompts[m["id"]])}</textarea>'
            f'  <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:6px">'
            f'    <button type="submit">Save</button>'
            f'    <button type="submit" formaction="/admin/custom_mode/delete" formnovalidate '
            f'      onclick="return confirm(\'Delete the {html.escape(m["id"])} mode?\')" '
            f'      style="background:#fff;color:#c33;border:1px solid #c33">Delete</button>'
            f'  </div>'
            f'</form>'
        )

    builtin_prompts_html = "".join(_builtin_prompt_form(mid) for mid, info in modes.items() if info["builtin"])
    user_mode_options = "".join(
        f'<option value="{html.escape(mid)}">{html.escape(mid)}</option>'
        for mid in modes.keys()
    )
    custom_modes_html = "".join(_custom_mode_form(m) for m in custom_modes) or (
        '<p class="sub" style="margin:0;color:#999">No custom modes yet. Add one below.</p>'
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
    <div class="nav"><a href="/">← live log</a> · <a href="/admin/insights">📊 insights</a> · <a href="/setup">🔧 setup</a></div>

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
      <h2 style="margin-top:0">Cache freshness</h2>
      <p class="sub" style="margin:-4px 0 12px">Cache hits older than the TTL fall through to a fresh Nia query, so updates to the indexed code/docs do reach users — they just absorb one slow answer per change. Set TTL to 0 to never expire.</p>
      <form method="post" action="/admin/cache_ttl" class="form-row" style="margin-bottom:10px">
        <input type="number" name="hours" min="0" value="{html.escape(cache_ttl_hours)}" style="flex:0 0 100px">
        <span class="state" style="font-size:13px;color:#555;align-self:center">hours before a cached answer expires</span>
        <button type="submit">Save</button>
      </form>
      <form method="post" action="/admin/cache_purge" style="display:flex;gap:8px;align-items:center">
        <button type="submit" onclick="return confirm('Wipe the entire Q&amp;A cache?')" style="background:#fff;color:#c33;border:1px solid #c33">Purge cache now</button>
        <span class="state" style="font-size:13px;color:#555">currently {s["questions"]} entries / {s["cache_hits"]} hits</span>
      </form>
    </div>

    <div class="panel">
      <h2 style="margin-top:0">Pre-warm for demo</h2>
      <form method="post" action="/admin/prewarm" class="toggle">
        <button type="submit">▶ Pre-warm cached beats</button>
        <span class="state">Seeds only the demo beats marked <code>prewarm</code> in /demo. Live beats stay fresh on stage. Runs in background — refresh / to see entries appear.</span>
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
        <input type="text" name="branch" placeholder="branch (optional, default: main)" style="max-width:220px">
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

      <h3 style="font-size:13px;margin:18px 0 6px;color:#555">Upload a file</h3>
      <p class="sub" style="margin:-4px 0 8px">Accepts <code>.pdf</code>, <code>.docx</code>, <code>.txt</code>, <code>.md</code>. Non-PDFs get converted to PDF in-memory before indexing.</p>
      <form method="post" action="/admin/sources/upload" enctype="multipart/form-data" class="form-row">
        <input type="file" name="file" required accept=".pdf,.docx,.txt,.md,application/pdf,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document">
        <input type="text" name="display_name" placeholder="display name (optional)">
        <button type="submit">Upload &amp; index</button>
      </form>
    </div>

    <div class="panel">
      <h2 style="margin-top:0">Mode prompts (built-in)</h2>
      <p class="sub" style="margin:-4px 0 12px">Claude system prompts for the baked-in modes. Edit and save to override; reset clears the override and falls back to default.</p>
      {builtin_prompts_html}
    </div>

    <div class="panel">
      <h2 style="margin-top:0">Custom modes</h2>
      <p class="sub" style="margin:-4px 0 12px">Define your own modes (subject tag <code>[id]</code> activates them). Edit colors and prompts inline, or use the generator to draft a prompt from a one-line description.</p>

      {custom_modes_html}

      <h3 style="font-size:13px;margin:18px 0 6px;color:#555">Add a new mode</h3>
      <details style="background:#fafafa;border:1px solid #eee;border-radius:6px;padding:10px 14px;margin:0 0 12px">
        <summary style="cursor:pointer;font-size:12px;font-weight:600;color:#555;text-transform:uppercase;letter-spacing:0.5px">✨ Need a prompt? Generate one from a description</summary>
        <div style="margin-top:10px">
          <textarea id="gen-desc" rows="3" placeholder="e.g. Onboarding mode for new engineers — points them at entry-point files with one-line explanations" style="width:100%;font-size:13px;padding:8px;border:1px solid #ddd;border-radius:6px"></textarea>
          <div style="display:flex;gap:8px;margin-top:6px">
            <button type="button" id="gen-btn">Draft prompt</button>
            <span id="gen-status" style="font-size:12px;color:#888;align-self:center"></span>
          </div>
        </div>
      </details>
      <form method="post" action="/admin/custom_mode/upsert" style="border:1px solid #eee;border-radius:6px;padding:12px">
        <div style="display:flex;gap:8px;margin-bottom:8px">
          <input type="text" name="id" pattern="[a-z][a-z0-9_]{{1,30}}" required placeholder="id (lowercase, e.g. product)" style="flex:0 0 200px;font-size:13px;padding:6px 8px;border:1px solid #ddd;border-radius:4px">
          <input type="text" name="label" placeholder="label (e.g. Product)" style="flex:1;font-size:13px;padding:6px 8px;border:1px solid #ddd;border-radius:4px">
          <input type="color" name="color_fg" value="#444" title="text color">
          <input type="color" name="color_bg" value="#eee" title="background color">
        </div>
        <textarea id="new-mode-prompt" name="prompt" rows="8" placeholder="System prompt for this mode (or use the generator above)" style="width:100%;font-family:ui-monospace,Menlo,monospace;font-size:12px;padding:10px;border:1px solid #ddd;border-radius:6px;line-height:1.45"></textarea>
        <div style="display:flex;justify-content:flex-end;margin-top:6px">
          <button type="submit">Add mode</button>
        </div>
      </form>
    </div>

    <script{nonce_attr}>
      document.getElementById('gen-btn')?.addEventListener('click', async () => {{
        const desc = (document.getElementById('gen-desc').value || '').trim();
        const status = document.getElementById('gen-status');
        const promptOut = document.getElementById('new-mode-prompt');
        // The new-mode form's id/label inputs are the only un-named ones in the wrap;
        // grab them via the form's name attributes for stability.
        const newForm = promptOut?.closest('form');
        const idInput = newForm?.querySelector('input[name="id"]');
        const labelInput = newForm?.querySelector('input[name="label"]');
        if (!desc) {{ status.textContent = 'enter a description first'; return; }}
        status.textContent = 'drafting…';
        try {{
          const r = await fetch('/admin/custom_mode/generate', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ description: desc }}),
          }});
          if (!r.ok) throw new Error('HTTP ' + r.status);
          const data = await r.json();
          promptOut.value = data.prompt || '';
          // Only fill id/label if the operator hasn't started typing them.
          if (idInput && !idInput.value.trim() && data.id) idInput.value = data.id;
          if (labelInput && !labelInput.value.trim() && data.label) labelInput.value = data.label;
          status.textContent = `drafted — id: ${{data.id || '?'}} · label: ${{data.label || '?'}} — review and edit before saving`;
        }} catch (e) {{ status.textContent = 'failed: ' + e.message; }}
      }});
    </script>

    <div class="panel">
      <h2 style="margin-top:0">Users ({len(users)})</h2>
      <table>
        <thead><tr><th>Email</th><th>Name</th><th>Mode</th><th></th></tr></thead>
        <tbody>{user_rows}</tbody>
      </table>
      <form method="post" action="/admin/users/upsert" class="form-row">
        <input type="email" name="email" required placeholder="email@team.com">
        <input type="text" name="display_name" placeholder="display name (optional)">
        <select name="default_mode" required>{user_mode_options}</select>
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
async def admin_page(request: Request):
    import core
    try:
        sources = await core.nia_list_sources()
    except Exception as e:
        print(f"[admin] nia_list_sources failed: {e}")
        sources = []
    return render(nia_sources=sources, nonce=getattr(request.state, "csp_nonce", ""))


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


@router.post("/admin/cache_ttl", dependencies=[Depends(_require_admin)])
async def set_cache_ttl(hours: int = Form(...)):
    cache.set_setting("cache_ttl_hours", str(max(0, int(hours))))
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/cache_purge", dependencies=[Depends(_require_admin)])
async def purge_cache():
    n = cache.purge_cache(older_than_hours=None)
    print(f"[admin] purged {n} cache rows")
    return RedirectResponse("/admin", status_code=303)


@router.get("/admin/source/{source_id}/view", response_class=HTMLResponse, dependencies=[Depends(_require_admin)])
async def view_source(source_id: str, path: str | None = None):
    import core
    try:
        meta = await core.nia_get_source(source_id)
    except Exception as e:
        meta = {"error": str(e)}

    # Documentation sources require a `path`. Repo sources don't take one.
    paths: list[str] = []
    src_type = (meta.get("type") if isinstance(meta, dict) else "") or ""
    if src_type == "documentation":
        try:
            tree = await core.nia_get_source_tree(source_id)
            paths = sorted(set(core._flatten_tree_paths(tree)))
        except Exception as e:
            paths = []
            tree_err = str(e)  # noqa: F841

    chosen_path = path or (paths[0] if paths else None)

    body = ""
    try:
        content = await core.nia_get_source_content(source_id, path=chosen_path)
        for key in ("content", "text", "body", "raw"):
            v = content.get(key) if isinstance(content, dict) else None
            if isinstance(v, str) and v.strip():
                body = v
                break
        if not body and isinstance(content, dict):
            import json as _json
            body = _json.dumps(content, indent=2)[:200_000]
    except Exception as e:
        body = f"(failed to fetch content: {e})"

    title = (meta.get("display_name") or meta.get("identifier") or source_id) if isinstance(meta, dict) else source_id

    paths_html = ""
    if paths:
        items = "".join(
            f'<li><a href="/admin/source/{html.escape(source_id)}/view?path={html.escape(p)}"'
            f'{" class=\"active\"" if p == chosen_path else ""}>{html.escape(p)}</a></li>'
            for p in paths
        )
        paths_html = f'<aside class="tree"><strong>pages ({len(paths)})</strong><ul>{items}</ul></aside>'

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif;
          background: #fafafa; padding: 24px; max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 18px; margin: 0 0 4px; }}
  .meta {{ font-size: 12px; color: #888; margin-bottom: 16px; }}
  .nav a {{ color: #0a3a99; font-size: 13px; }}
  .layout {{ display: grid; grid-template-columns: {('260px 1fr' if paths else '1fr')}; gap: 16px; align-items: start; }}
  aside.tree {{ background: #fff; border: 1px solid #eee; border-radius: 8px; padding: 12px;
                font-size: 12px; max-height: 80vh; overflow: auto; }}
  aside.tree ul {{ list-style: none; padding: 0; margin: 8px 0 0; }}
  aside.tree li {{ margin: 2px 0; }}
  aside.tree a {{ color: #0a3a99; text-decoration: none; word-break: break-all; }}
  aside.tree a.active {{ font-weight: 600; color: #c33; }}
  pre {{ background: #fff; border: 1px solid #eee; border-radius: 8px;
         padding: 16px; white-space: pre-wrap; word-wrap: break-word;
         font-family: ui-monospace, Menlo, monospace; font-size: 12px; line-height: 1.5; margin: 0; }}
</style></head><body>
  <div class="nav"><a href="/admin">← admin</a></div>
  <h1>{html.escape(title)}</h1>
  <div class="meta">
    id: <code>{html.escape(source_id)}</code> ·
    type: {html.escape(str(src_type or '?'))} ·
    status: {html.escape(str(meta.get('status','?')) if isinstance(meta, dict) else '?')}
    {f' · viewing: <code>{html.escape(chosen_path)}</code>' if chosen_path else ''}
  </div>
  <div class="layout">
    {paths_html}
    <pre>{html.escape(body or '(empty)')}</pre>
  </div>
</body></html>"""


def _set_active_repos(repos: list[str]) -> None:
    cache.set_setting("nia_repos", ",".join(r.strip() for r in repos if r.strip()))


def _set_active_docs(docs: list[str]) -> None:
    cache.set_setting("nia_data_sources", ",".join(d.strip() for d in docs if d.strip()))


def _invalidate_cache_after_source_change(reason: str) -> None:
    """Any change to the active source list potentially makes existing cached
    answers stale (citations may now point at the wrong repo set, or new
    sources may produce a different answer). Wipe the cache so the next ask
    re-queries Nia."""
    n = cache.purge_cache(older_than_hours=None)
    print(f"[admin] purged {n} cache rows after source change: {reason}")


_REPO_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+$")


def _normalize_repo_input(raw: str) -> tuple[str, str]:
    """Coerce common copy-paste shapes into ('org/repo', 'branch_or_empty').

    Accepts:
      - 'org/repo'                                (canonical)
      - 'org/repo@branch'                         (inline branch)
      - 'https://github.com/org/repo'             (full URL)
      - 'https://github.com/org/repo/tree/branch' (URL with branch)
      - 'https://github.com/org/repo.git'         (clone URL)
      - 'git@github.com:org/repo.git'             (SSH URL)

    Returns ('', '') if the input can't be parsed; caller should 400.
    """
    s = raw.strip()
    branch = ""
    # SSH form: git@github.com:org/repo.git
    if s.startswith("git@github.com:"):
        s = s[len("git@github.com:"):]
    # HTTP(S) form: strip protocol + host
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if s.lower().startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    # Pull branch out of '/tree/<branch>' segment if present.
    if "/tree/" in s:
        s, _, branch = s.partition("/tree/")
        branch = branch.split("/", 1)[0].strip()
    # Inline @branch wins over a tree-segment branch (more explicit).
    if "@" in s:
        repo_part, _, inline = s.partition("@")
        if inline.strip():
            branch = inline.strip()
        s = repo_part.strip()
    s = s.strip("/")
    return s, branch


@router.post("/admin/sources/repo/add", dependencies=[Depends(_require_admin)])
async def add_repo(repo: str = Form(...), branch: str = Form("")):
    import core
    raw_repo, parsed_branch = _normalize_repo_input(repo or "")
    branch = (branch or "").strip() or parsed_branch
    if not _REPO_PATTERN.match(raw_repo):
        raise HTTPException(
            status_code=400,
            detail=(
                "Couldn't parse that as a repo. Use 'org/repo' "
                "(optionally 'org/repo@branch') or paste a github.com URL."
            ),
        )
    raw = raw_repo
    # Normalize: 'main' is Nia's default — treat as no branch so the active-list
    # entry stays in the simpler 'org/repo' form.
    if branch == "main":
        branch = ""
    entry = f"{raw}@{branch}" if branch else raw
    try:
        await core.nia_index_repo(raw, branch=branch or None)
    except Exception as e:
        # Common case: Nia rejects private/auth-required repos, or the branch
        # doesn't exist yet. Surface but still accept the addition so the
        # operator can fix the index out-of-band.
        print(f"[admin] nia_index_repo failed for {entry}: {e}")
    # Best-effort local clone so git blame works for cited files. Doesn't block
    # the response if it fails (e.g. private repo without GITHUB_TOKEN).
    try:
        org, name = raw.split("/", 1)
        core.ensure_repo_cloned(org, name)
    except Exception as e:
        print(f"[admin] local clone failed for {raw}: {e}")
    active = core.get_active_repos()
    if entry not in active:
        active.append(entry)
    _set_active_repos(active)
    _invalidate_cache_after_source_change(f"add repo {entry}")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/sources/repo/remove", dependencies=[Depends(_require_admin)])
async def remove_repo(repo: str = Form(...)):
    import core
    active = [r for r in core.get_active_repos() if r != repo.strip()]
    _set_active_repos(active)
    _invalidate_cache_after_source_change(f"remove repo {repo}")
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
    _invalidate_cache_after_source_change(f"add doc {label}")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/sources/doc/remove", dependencies=[Depends(_require_admin)])
async def remove_doc(display_name: str = Form(...)):
    import core
    active = [d for d in core.get_active_data_sources() if d != display_name.strip()]
    _set_active_docs(active)
    _invalidate_cache_after_source_change(f"remove doc {display_name}")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/sources/upload", dependencies=[Depends(_require_admin)])
async def upload_source(
    file: UploadFile = File(...),
    display_name: str = Form(""),
):
    import core
    import uploads
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="file > 25 MB")
    try:
        src = await uploads.upload_file(file.filename or "upload", content, display_name or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"[admin] upload failed: {e}")
        raise HTTPException(status_code=502, detail=f"Nia upload failed: {e}")
    label = (display_name.strip() or src.get("display_name")
             or os.path.splitext(file.filename or "Upload")[0])
    active = core.get_active_data_sources()
    if label and label not in active:
        active.append(label)
    _set_active_docs(active)
    _invalidate_cache_after_source_change(f"upload {label}")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/custom_mode/upsert", dependencies=[Depends(_require_admin)])
async def upsert_custom_mode(
    id: str = Form(...),
    label: str = Form(""),
    color_fg: str = Form("#444"),
    color_bg: str = Form("#eee"),
    prompt: str = Form(""),
):
    import core
    mid = (id or "").strip().lower()
    if not core.is_valid_mode_id(mid):
        raise HTTPException(status_code=400, detail="invalid id (lowercase letters, digits, underscores; 2–31 chars; must start with a letter)")
    if mid in core.BUILTIN_MODES:
        raise HTTPException(status_code=400, detail=f"'{mid}' is a built-in mode; edit it via the built-in prompts panel instead")
    cache.upsert_custom_mode(
        mid=mid,
        label=(label or mid).strip(),
        color_fg=(color_fg or "#444").strip(),
        color_bg=(color_bg or "#eee").strip(),
        prompt=(prompt or "").strip(),
    )
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/custom_mode/delete", dependencies=[Depends(_require_admin)])
async def delete_custom_mode(id: str = Form(...)):
    cache.delete_custom_mode((id or "").strip().lower())
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/custom_mode/generate", dependencies=[Depends(_require_admin)])
async def generate_custom_mode(payload: dict = Body(...)):
    import core
    desc = (payload.get("description") if isinstance(payload, dict) else "") or ""
    try:
        meta = core.generate_mode_metadata(desc)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"[admin] generate_mode_metadata failed: {e}")
        raise HTTPException(status_code=502, detail=f"generation failed: {e}")
    return JSONResponse(meta)


@router.get("/admin/insights", response_class=HTMLResponse, dependencies=[Depends(_require_admin)])
async def insights_page():
    import insights
    gaps = insights.find_capability_gaps()
    rows = "".join(_gap_row(g) for g in gaps) or (
        '<tr><td colspan="3" style="color:#999;text-align:center;padding:24px">No capability gaps detected yet. Send a few sales-mode questions through; whenever the answer says "not yet" / "limited" / "doesn\'t support", it shows up here.</td></tr>'
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Concierge — Insights</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif;
          background: #fafafa; color: #222; padding: 24px; max-width: 880px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: #888; font-size: 13px; margin-bottom: 18px; }}
  .nav a {{ color: #0a3a99; font-size: 13px; margin-right: 16px; }}
  .panel {{ background: #fff; border-radius: 8px; padding: 16px 20px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th, td {{ padding: 10px; border-bottom: 1px solid #f0f0f0; text-align: left; vertical-align: top; }}
  th {{ font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }}
  .ask-count {{ font-size: 18px; font-weight: 600; color: #0a3a99; }}
  .askers {{ font-size: 11px; color: #888; }}
  .snippet {{ font-size: 12px; color: #555; margin-top: 6px; line-height: 1.5; }}
  .all-q {{ font-size: 11px; color: #888; margin-top: 4px; }}
</style></head><body>
  <h1>Capability gaps — product opportunities</h1>
  <div class="sub">Sales-mode answers that contained negative-capability markers (not yet, limited, doesn't support, etc.) — clustered and ranked by how many distinct people asked.</div>
  <div class="nav"><a href="/admin">← admin</a> · <a href="/">live log</a></div>
  <div class="panel">
    <table>
      <thead><tr><th>Asks</th><th>Gap (exemplar question)</th><th>Evidence</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</body></html>"""


@router.post("/admin/prompt", dependencies=[Depends(_require_admin)])
async def set_prompt(
    mode: str = Form(...),
    prompt: str = Form(""),
    reset: str = Form(""),
):
    if mode not in ("eng", "sales", "marketing", "support", "security"):
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


@router.get("/api/users", dependencies=[Depends(_require_admin)])
async def api_users():
    return cache.list_users()
