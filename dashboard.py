"""
Concierge dashboard — read-only HTML view of the SQLite Q&A log.

One-file, zero JS frameworks. Auto-refreshes every 5s via meta tag.
"""
import html
import re
from datetime import datetime, timezone

import cache
import core


_MODE_COLORS = {
    "eng": ("#0a7d3e", "#dcf5e6"),
    "sales": ("#0a3a99", "#e0eafc"),
    "marketing": ("#a8479a", "#fbe6f5"),
    "support": ("#b3530a", "#fcecdc"),
}


def _strip_mode_prefix(question: str) -> tuple[str, str]:
    """Cache stores '[mode] question' — split it back for display."""
    m = re.match(r"^\[([a-z]+)\]\s*(.*)$", question, flags=re.DOTALL)
    if m and m.group(1) in _MODE_COLORS:
        return m.group(1), m.group(2)
    return "eng", question


def _ago(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        # Cache stores ISO timestamps with optional Z; sqlite default is "YYYY-MM-DD HH:MM:SS".
        if "T" in ts:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            t = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    except ValueError:
        return ts
    now = datetime.now(timezone.utc)
    secs = int((now - t).total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _badge(mode: str) -> str:
    fg, bg = _MODE_COLORS.get(mode, _MODE_COLORS["eng"])
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:4px;'
        f'font-size:11px;font-weight:600;letter-spacing:0.5px;'
        f'color:{fg};background:{bg};text-transform:uppercase">{html.escape(mode)}</span>'
    )


def _row(entry: dict) -> str:
    mode, question = _strip_mode_prefix(entry["question"])
    asked_ago = _ago(entry["created_at"])
    sources = entry["sources"][:5]
    engineers = entry["engineers"][:3]

    def _link(s):
        href = s if s.startswith(("http://", "https://")) else core.github_blob_url(s)
        return f'<a href="{html.escape(href)}" target="_blank" rel="noopener">{html.escape(s)}</a>'

    sources_html = " · ".join(_link(s) for s in sources) if sources else '<span style="color:#999">no sources</span>'
    if len(entry["sources"]) > 5:
        sources_html += f' <span style="color:#999">+{len(entry["sources"]) - 5} more</span>'

    engineers_html = ""
    if engineers:
        names = ", ".join(html.escape(e["name"]) for e in engineers)
        engineers_html = f'<div style="color:#666;font-size:12px;margin-top:4px">cc: {names}</div>'

    hits_html = ""
    if entry["hit_count"]:
        hit_who = html.escape(entry["last_hit_sender"] or "another teammate")
        hit_when = _ago(entry["last_hit_at"])
        hits_html = (
            f'<div style="margin-top:6px;padding:6px 10px;background:#fff8d6;'
            f'border-left:3px solid #f5b800;border-radius:3px;font-size:12px">'
            f'⚡ <strong>Cache hit ×{entry["hit_count"]}</strong> — last asked by '
            f'{hit_who} {hit_when}</div>'
        )

    sender = html.escape(entry["original_sender"] or "unknown")
    return (
        f'<article style="padding:14px 18px;border-bottom:1px solid #eee">'
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">'
        f'  {_badge(mode)}'
        f'  <span style="color:#999;font-size:12px">{sender} · {asked_ago}</span>'
        f'</div>'
        f'<div style="font-size:15px;font-weight:500;margin-bottom:4px">{html.escape(question)}</div>'
        f'<div style="font-size:12px;color:#555">{sources_html}</div>'
        f'{engineers_html}'
        f'{hits_html}'
        f'</article>'
    )


def render_feed_html() -> str:
    """Just the feed + stats fragment, for JS-driven refresh without reloading the page."""
    rows = cache.recent(limit=30)
    s = cache.stats()
    body = "\n".join(_row(r) for r in rows) or (
        '<div style="padding:48px;text-align:center;color:#999">'
        'No questions yet. Send one below or email '
        '<code>codebaseconcierge@agentmail.to</code>.</div>'
    )
    return (
        f'<div class="stats">'
        f'  <div class="stat"><div class="stat-num">{s["questions"]}</div><div class="stat-label">Questions answered</div></div>'
        f'  <div class="stat"><div class="stat-num">{s["cache_hits"]}</div><div class="stat-label">Cache hits</div></div>'
        f'</div>'
        f'<div class="feed">{body}</div>'
    )


def render() -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Codebase Concierge — live log</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif;
            background: #fafafa; color: #222; margin: 0; padding: 24px; }}
    .wrap {{ max-width: 820px; margin: 0 auto; }}
    h1 {{ font-size: 22px; margin: 0 0 4px 0; }}
    .sub {{ color: #888; font-size: 13px; margin-bottom: 18px; }}
    .stats {{ display: flex; gap: 24px; margin: 0 0 14px 0;
              padding: 14px 18px; background: #fff; border-radius: 8px;
              box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
    .stat {{ flex: 0 0 auto; }}
    .stat-num {{ font-size: 24px; font-weight: 600; }}
    .stat-label {{ font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }}
    .feed {{ background: #fff; border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.04);
             overflow: hidden; }}
    a {{ color: #0a3a99; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    code {{ background: #f0f0f0; padding: 1px 5px; border-radius: 3px; font-size: 12px; }}

    .chat {{ background: #fff; border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.04);
             padding: 16px 18px; margin-bottom: 18px; }}
    .chat-row {{ display: flex; gap: 8px; margin-bottom: 10px; }}
    .chat input, .chat select, .chat textarea, .chat button {{
      font-family: inherit; font-size: 14px; padding: 8px 10px;
      border: 1px solid #ddd; border-radius: 6px; background: #fff;
    }}
    .chat textarea {{ flex: 1; resize: vertical; min-height: 38px; }}
    .chat select {{ flex: 0 0 110px; }}
    .chat input.sender {{ flex: 0 0 220px; }}
    .chat button {{ background: #0a3a99; color: white; border: none;
                    cursor: pointer; padding: 8px 16px; font-weight: 500; }}
    .chat button:hover {{ background: #0c46b8; }}
    .chat button:disabled {{ background: #aaa; cursor: not-allowed; }}
    .chat-out {{ margin-top: 12px; }}
    .chat-bubble {{ padding: 10px 14px; border-radius: 8px;
                    background: #f4f6fa; border-left: 3px solid #0a3a99;
                    margin-bottom: 8px; font-size: 14px; line-height: 1.45; }}
    .chat-bubble.cache {{ border-left-color: #f5b800; background: #fff8d6; }}
    .chat-meta {{ font-size: 11px; color: #888; margin-top: 6px; }}
    .chat-bubble h3 {{ font-size: 13px; margin: 10px 0 4px; color: #555; }}
    .chat-bubble ul {{ margin: 4px 0 4px 18px; padding: 0; }}
    .chat-bubble pre {{ background: #fff; padding: 8px; border-radius: 4px;
                        border: 1px solid #eee; overflow-x: auto; font-size: 12px; }}
    .chat-bubble code {{ background: #fff; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Codebase Concierge — live log</h1>
    <div class="sub">One brain, two channels. Ask via email <code>codebaseconcierge@agentmail.to</code> or right here. <a href="/admin" style="margin-left:8px">admin</a></div>

    <div class="chat">
      <div class="chat-row">
        <select id="mode">
          <option value="eng">eng</option>
          <option value="sales">sales</option>
          <option value="marketing">marketing</option>
          <option value="support">support</option>
        </select>
        <input class="sender" id="sender" type="text" list="users" autocomplete="off" placeholder="from (e.g. you@team.com)">
        <datalist id="users"></datalist>
      </div>
      <div class="chat-row">
        <textarea id="question" rows="1" placeholder="Ask anything about the indexed codebase…"></textarea>
        <button id="send">Send</button>
      </div>
      <div class="chat-out" id="chat-out"></div>
    </div>

    <div id="feed-mount"></div>
  </div>

  <script>
    const mount = document.getElementById('feed-mount');
    const out = document.getElementById('chat-out');
    const send = document.getElementById('send');
    const q = document.getElementById('question');
    const m = document.getElementById('mode');
    const s = document.getElementById('sender');

    async function refreshFeed() {{
      try {{
        const r = await fetch('/api/feed');
        if (r.ok) mount.innerHTML = await r.text();
      }} catch (e) {{ /* swallow */ }}
    }}

    async function ask() {{
      const text = q.value.trim();
      if (!text) return;
      send.disabled = true;
      const t0 = performance.now();
      const echo = document.createElement('div');
      echo.className = 'chat-bubble';
      echo.style.background = '#fff';
      echo.style.borderLeftColor = '#999';
      echo.innerHTML = '<strong>You:</strong> ' + text.replace(/[<>&]/g, c => ({{'<':'&lt;','>':'&gt;','&':'&amp;'}}[c])) +
                       '<div class="chat-meta">mode: ' + m.value + (s.value ? ' · from: ' + s.value : '') + '</div>';
      out.prepend(echo);

      const loading = document.createElement('div');
      loading.className = 'chat-bubble';
      loading.textContent = 'Thinking…';
      out.prepend(loading);

      try {{
        const r = await fetch('/skill/ask', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ question: text, mode: m.value, sender: s.value || null }}),
        }});
        const data = await r.json();
        const dur = ((performance.now() - t0) / 1000).toFixed(1);
        loading.className = 'chat-bubble' + (data.cache_hit ? ' cache' : '');
        loading.innerHTML = data.answer_html +
          '<div class="chat-meta">' +
          (data.cache_hit ? '⚡ cache hit · ' : '') +
          dur + 's · mode: ' + (data.mode || m.value) +
          '</div>';
        q.value = '';
        refreshFeed();
      }} catch (e) {{
        loading.textContent = 'Error: ' + e.message;
      }} finally {{
        send.disabled = false;
      }}
    }}

    send.addEventListener('click', ask);
    q.addEventListener('keydown', e => {{
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) ask();
    }});

    async function loadUsers() {{
      try {{
        const r = await fetch('/api/users');
        if (!r.ok) return;
        const list = await r.json();
        const dl = document.getElementById('users');
        dl.innerHTML = list.map(u => {{
          const label = u.display_name ? `${{u.display_name}} <${{u.email}}>` : u.email;
          return `<option value="${{u.email}}" label="${{label}} · ${{u.default_mode}}">`;
        }}).join('');
      }} catch (e) {{ /* swallow */ }}
    }}

    // When the user picks a known address, snap the mode dropdown to their default.
    s.addEventListener('change', async () => {{
      try {{
        const r = await fetch('/api/users');
        if (!r.ok) return;
        const list = await r.json();
        const hit = list.find(u => u.email.toLowerCase() === s.value.trim().toLowerCase());
        if (hit) m.value = hit.default_mode;
      }} catch (e) {{ /* swallow */ }}
    }});

    refreshFeed();
    loadUsers();
    setInterval(refreshFeed, 5000);
  </script>
</body>
</html>"""
