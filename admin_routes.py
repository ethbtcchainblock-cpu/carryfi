import os
import time
import hashlib
import threading
from datetime import datetime, timezone
from functools import wraps
from concurrent.futures import ThreadPoolExecutor, as_completed

import stripe as _stripe_lib
import requests as req
from flask import request, session, redirect, jsonify, render_template_string

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
TG_TOKEN       = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHANNEL     = os.environ.get("TELEGRAM_CHANNEL_ID", "")
STRIPE_SECRET  = os.environ.get("STRIPE_SECRET_KEY", "")
PRICE_USD      = 19

_last_alert_run: dict = {"time": None, "fired": 0, "error": None}


def _stripe():
    _stripe_lib.api_key = STRIPE_SECRET
    return _stripe_lib


def _tg(chat_id: str, text: str) -> bool:
    if not TG_TOKEN or not chat_id:
        return False
    try:
        req.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        return True
    except Exception:
        return False


def _requires_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_ok"):
            if request.path.startswith("/admin/api/"):
                return jsonify({"error": "not authenticated"}), 401
            return redirect("/admin/login")
        return f(*args, **kwargs)
    return decorated


# ─── HTML ────────────────────────────────────────────────────────────────────

LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CarryFi Admin</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#090909;color:#e5e5e5;font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#111;border:1px solid #1e1e1e;border-radius:16px;padding:48px 40px;width:100%;max-width:380px}
h1{font-size:1.4rem;font-weight:800;color:#f0b429;margin-bottom:6px;letter-spacing:-0.03em}
.sub{color:#555;font-size:0.82rem;margin-bottom:32px}
input{width:100%;background:#0d0d0d;border:1px solid #252525;border-radius:8px;padding:13px 16px;color:#e5e5e5;font-size:1rem;margin-bottom:16px;outline:none;transition:border-color .15s}
input:focus{border-color:#f0b429}
button{width:100%;background:#f0b429;color:#000;border:none;border-radius:8px;padding:14px;font-size:0.95rem;font-weight:700;cursor:pointer;transition:background .15s}
button:hover{background:#d4a017}
.err{color:#ef4444;font-size:0.82rem;margin-bottom:14px;background:#1a0808;border:1px solid #3a1212;border-radius:6px;padding:10px 12px}
</style>
</head>
<body>
<div class="card">
  <h1>⚡ CarryFi Admin</h1>
  <p class="sub">Master control panel — authorized access only</p>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="POST">
    <input type="password" name="password" placeholder="Admin password" autofocus autocomplete="current-password">
    <button type="submit">Enter Dashboard</button>
  </form>
</div>
</body>
</html>"""


ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CarryFi Admin</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#090909;--card:#111;--border:#1e1e1e;--border2:#252525;--gold:#f0b429;--green:#22c55e;--red:#ef4444;--dim:#555;--text:#e5e5e5;--text2:#999}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',sans-serif;font-size:14px;padding-bottom:60px}

/* Header */
.header{position:sticky;top:0;z-index:100;background:#0d0d0dcc;backdrop-filter:blur(12px);border-bottom:1px solid var(--border);padding:14px 24px;display:flex;align-items:center;gap:16px}
.logo{font-size:1rem;font-weight:800;color:var(--gold);letter-spacing:-0.03em;margin-right:auto}
.hbtn{background:transparent;border:1px solid var(--border2);color:var(--text2);border-radius:6px;padding:6px 14px;font-size:0.8rem;cursor:pointer;transition:all .15s}
.hbtn:hover{border-color:var(--gold);color:var(--gold)}
.hbtn.danger:hover{border-color:var(--red);color:var(--red)}
#clock{color:var(--dim);font-size:0.8rem}

/* Layout */
.page{max-width:1200px;margin:0 auto;padding:24px}
.section{margin-bottom:32px}
.section-title{font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;color:var(--dim);margin-bottom:14px;display:flex;align-items:center;gap:10px}
.section-title .dot{width:6px;height:6px;border-radius:50%;background:var(--gold)}

/* Stat cards */
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:32px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px 20px 16px}
.card .label{font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:var(--dim);margin-bottom:8px}
.card .val{font-size:2rem;font-weight:800;letter-spacing:-0.04em;color:var(--text);line-height:1}
.card .sub{font-size:0.75rem;color:var(--dim);margin-top:6px}
.card.gold .val{color:var(--gold)}
.card.green .val{color:var(--green)}

/* Two-col grid */
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:32px}
@media(max-width:800px){.cards{grid-template-columns:1fr 1fr}.grid2{grid-template-columns:1fr}}

/* Panel */
.panel{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px}
.panel-hd{font-size:0.78rem;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:0.07em;margin-bottom:16px;display:flex;align-items:center;justify-content:space-between}

/* Tables */
table{width:100%;border-collapse:collapse}
th{font-size:0.68rem;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:var(--dim);padding:0 12px 10px;text-align:left;border-bottom:1px solid var(--border)}
td{padding:10px 12px;border-bottom:1px solid #141414;font-size:0.85rem;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#161616}
.tbl-wrap{overflow-x:auto}

/* Badges */
.badge{display:inline-block;font-size:0.68rem;font-weight:700;padding:2px 8px;border-radius:20px;text-transform:uppercase;letter-spacing:0.05em}
.badge.active{background:#0d2a1a;color:var(--green);border:1px solid #1a4030}
.badge.trialing{background:#1a1a0a;color:#facc15;border:1px solid #3a3010}
.badge.past_due{background:#2a0d0d;color:var(--red);border:1px solid #4a1a1a}
.badge.ok{background:#0d2a1a;color:var(--green)}
.badge.error{background:#2a0d0d;color:var(--red)}
.badge.warn{background:#1a1a0a;color:#facc15}

/* APR color */
.apr-high{color:var(--green);font-weight:700}
.apr-mid{color:var(--gold);font-weight:600}
.apr-low{color:var(--text2)}

/* Action buttons */
.btn{display:inline-block;border:none;border-radius:6px;padding:7px 14px;font-size:0.78rem;font-weight:600;cursor:pointer;transition:all .15s;line-height:1}
.btn-gold{background:var(--gold);color:#000}.btn-gold:hover{background:#d4a017}
.btn-outline{background:transparent;border:1px solid var(--border2);color:var(--text2)}.btn-outline:hover{border-color:var(--gold);color:var(--gold)}
.btn-red{background:transparent;border:1px solid #3a1212;color:var(--red)}.btn-red:hover{background:#2a0808}
.btn-green{background:#0d2a1a;color:var(--green);border:1px solid #1a4030}.btn-green:hover{background:#1a4030}
.btn-sm{padding:4px 10px;font-size:0.72rem}
.btn[disabled]{opacity:.4;cursor:not-allowed}

/* Forms */
.field{width:100%;background:#0d0d0d;border:1px solid var(--border2);border-radius:8px;padding:10px 14px;color:var(--text);font-size:0.9rem;outline:none;transition:border-color .15s;font-family:inherit}
.field:focus{border-color:var(--gold)}
textarea.field{resize:vertical;min-height:80px}
.field-row{display:flex;gap:8px}
.field-row .field{flex:1}
.form-note{font-size:0.72rem;color:var(--dim);margin-top:6px}

/* Alert run row */
.alert-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.pulse{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 0 0 rgba(34,197,94,.4);animation:pulse 2s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(34,197,94,.4)}70%{box-shadow:0 0 0 8px rgba(34,197,94,0)}100%{box-shadow:0 0 0 0 rgba(34,197,94,0)}}

/* Loader */
.loader{display:inline-block;width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--gold);border-radius:50%;animation:spin .7s linear infinite;vertical-align:middle;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
.loading-row{color:var(--dim);font-size:0.82rem;padding:20px;text-align:center}

/* Toast */
#toast-wrap{position:fixed;bottom:24px;right:24px;z-index:999;display:flex;flex-direction:column;gap:8px;max-width:340px}
.toast{background:#1a1a1a;border:1px solid var(--border);border-radius:10px;padding:12px 16px;font-size:0.82rem;display:flex;align-items:flex-start;gap:10px;animation:slide-in .2s ease}
@keyframes slide-in{from{transform:translateX(40px);opacity:0}to{transform:translateX(0);opacity:1}}
.toast.success{border-color:#1a4030}.toast.error{border-color:#4a1a1a}
.toast .icon{font-size:1rem;flex-shrink:0}
.toast .msg{flex:1;line-height:1.4}

/* Exchange health rows */
.exch-row{display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid #141414}
.exch-row:last-child{border-bottom:none}
.exch-name{font-weight:600;width:110px;flex-shrink:0}
.exch-stat{flex:1}
.exch-num{color:var(--gold);font-weight:700;font-size:1.1rem;width:50px;text-align:right}
.exch-lat{color:var(--dim);font-size:0.75rem;width:50px;text-align:right}

/* Invite result */
#invite-result{display:none;margin-top:12px;background:#0d0d0d;border:1px solid var(--border);border-radius:8px;padding:12px}
#invite-link{font-family:monospace;font-size:0.78rem;color:var(--gold);word-break:break-all;margin-bottom:8px}
</style>
</head>
<body>

<header class="header">
  <div class="logo">⚡ CarryFi Admin</div>
  <span id="clock"></span>
  <button class="hbtn" onclick="refreshAll()">↻ Refresh</button>
  <button class="hbtn" onclick="location.href='/admin/logout'" style="color:var(--red);border-color:#3a1212">Sign Out</button>
</header>

<div class="page">

  <!-- METRIC CARDS -->
  <div class="cards" id="cards">
    <div class="card gold"><div class="label">MRR</div><div class="val" id="c-mrr">—</div><div class="sub">monthly recurring</div></div>
    <div class="card green"><div class="label">Active Subs</div><div class="val" id="c-active">—</div><div class="sub">paying subscribers</div></div>
    <div class="card"><div class="label">Trialing</div><div class="val" id="c-trial">—</div><div class="sub">7-day free trial</div></div>
    <div class="card"><div class="label">New Today</div><div class="val" id="c-new">—</div><div class="sub">signups since midnight</div></div>
  </div>

  <!-- EXCHANGE HEALTH + ALERT CONTROLS -->
  <div class="grid2">

    <div class="panel">
      <div class="panel-hd">
        Exchange Health
        <button class="btn btn-outline btn-sm" onclick="loadExchangeHealth()" id="btn-health">Check Now</button>
      </div>
      <div id="exch-body">
        <div class="loading-row" style="padding:30px 0;color:var(--dim)">Click "Check Now" — takes ~5 seconds</div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-hd">Alert Engine</div>
      <div id="alert-status" style="color:var(--dim);font-size:0.82rem;margin-bottom:16px">Loading...</div>
      <div class="alert-row">
        <div class="pulse"></div>
        <span style="color:var(--dim);font-size:0.82rem">GitHub Actions runs every 15 min</span>
      </div>
      <div style="margin-top:16px;display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn btn-gold" onclick="runAlerts()">▶ Run Alerts Now</button>
        <button class="btn btn-outline btn-sm" onclick="loadOpportunities()">Load Top Opportunities</button>
      </div>
    </div>

  </div>

  <!-- TOP OPPORTUNITIES -->
  <div class="section" id="opp-section" style="display:none">
    <div class="section-title"><span class="dot"></span>Top Opportunities Right Now</div>
    <div class="panel">
      <div class="tbl-wrap" id="opp-body"></div>
    </div>
  </div>

  <!-- SUBSCRIBERS -->
  <div class="section">
    <div class="section-title"><span class="dot"></span>Subscribers</div>
    <div class="panel">
      <div class="tbl-wrap" id="sub-body">
        <div class="loading-row"><span class="loader"></span>Loading subscribers...</div>
      </div>
    </div>
  </div>

  <!-- RECENT PAYMENTS -->
  <div class="section">
    <div class="section-title"><span class="dot"></span>Recent Payments</div>
    <div class="panel">
      <div class="tbl-wrap" id="pay-body">
        <div class="loading-row"><span class="loader"></span>Loading payments...</div>
      </div>
    </div>
  </div>

  <!-- ACTIONS -->
  <div class="grid2">

    <!-- BROADCAST -->
    <div class="panel">
      <div class="panel-hd">📢 Broadcast to Channel</div>
      <textarea class="field" id="bc-msg" placeholder="Message to send to all subscribers in the Telegram channel..."></textarea>
      <div class="form-note" id="bc-count">0 characters</div>
      <div style="margin-top:10px">
        <button class="btn btn-gold" onclick="sendBroadcast()">Send to Channel</button>
      </div>
    </div>

    <!-- INVITE GENERATOR -->
    <div class="panel">
      <div class="panel-hd">🔗 Generate Invite Link</div>
      <div class="field-row">
        <input class="field" id="inv-email" placeholder="Email (optional label)" type="email">
        <button class="btn btn-green" onclick="generateInvite()">Generate</button>
      </div>
      <div class="form-note">One-time link, expires in 7 days</div>
      <div id="invite-result">
        <div id="invite-link"></div>
        <button class="btn btn-outline btn-sm" onclick="copyInvite()">Copy Link</button>
      </div>
    </div>

  </div>

</div><!-- /page -->

<div id="toast-wrap"></div>

<script>
// ─── Clock ───────────────────────────────────────────────────────────────────
function tick() {
  document.getElementById('clock').textContent =
    new Date().toLocaleString('en-US', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
}
tick(); setInterval(tick, 10000);

// ─── Toast ───────────────────────────────────────────────────────────────────
function toast(msg, type='success') {
  const wrap = document.getElementById('toast-wrap');
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.innerHTML = `<span class="icon">${type==='success'?'✅':'❌'}</span><span class="msg">${msg}</span>`;
  wrap.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

// ─── Stats ───────────────────────────────────────────────────────────────────
async function loadStats() {
  try {
    const d = await fetch('/admin/api/stats').then(r=>r.json());
    document.getElementById('c-mrr').textContent   = '$' + (d.mrr || 0);
    document.getElementById('c-active').textContent = d.active ?? '—';
    document.getElementById('c-trial').textContent  = d.trialing ?? '—';
    document.getElementById('c-new').textContent    = d.new_today ?? '—';

    const pa = document.getElementById('pay-body');
    if (!d.recent_payments?.length) {
      pa.innerHTML = '<div class="loading-row">No payments found</div>';
    } else {
      pa.innerHTML = `<table>
        <thead><tr><th>Email</th><th>Amount</th><th>Status</th><th>Time</th></tr></thead>
        <tbody>${d.recent_payments.map(p=>`<tr>
          <td>${p.email||'—'}</td>
          <td style="color:var(--green);font-weight:700">${p.amount}</td>
          <td><span class="badge ${p.status==='succeeded'?'active':'warn'}">${p.status}</span></td>
          <td style="color:var(--dim)">${p.time}</td>
        </tr>`).join('')}</tbody>
      </table>`;
    }

    // Alert status
    const as_ = document.getElementById('alert-status');
    if (d.last_alert) {
      as_.innerHTML = `Last run: <strong style="color:var(--text)">${d.last_alert.time}</strong>
        &nbsp;·&nbsp; Fired: <strong style="color:var(--gold)">${d.last_alert.fired}</strong> alerts
        ${d.last_alert.error ? '<br><span style="color:var(--red)">Error: '+d.last_alert.error+'</span>' : ''}`;
    } else {
      as_.textContent = 'No manual runs yet this session';
    }
  } catch(e) { console.error(e); }
}

// ─── Subscribers ─────────────────────────────────────────────────────────────
async function loadSubscribers() {
  const el = document.getElementById('sub-body');
  try {
    const d = await fetch('/admin/api/subscribers').then(r=>r.json());
    const subs = d.subscribers || [];
    if (!subs.length) {
      el.innerHTML = '<div class="loading-row">No subscribers found</div>';
      return;
    }
    el.innerHTML = `<table>
      <thead><tr><th>Email</th><th>Status</th><th>Since</th><th>Sub ID</th><th>Actions</th></tr></thead>
      <tbody>${subs.map(s=>`<tr id="sub-${s.sub_id}">
        <td style="font-weight:600">${s.email||'—'}</td>
        <td><span class="badge ${s.status}">${s.status}</span></td>
        <td style="color:var(--dim)">${s.created}</td>
        <td style="font-family:monospace;font-size:0.72rem;color:var(--dim)">${s.sub_id}</td>
        <td style="display:flex;gap:6px">
          <button class="btn btn-green btn-sm" onclick="quickInvite('${s.email}')">Invite</button>
          <button class="btn btn-red btn-sm" onclick="cancelSub('${s.sub_id}','${s.email}')">Cancel</button>
        </td>
      </tr>`).join('')}</tbody>
    </table>`;
  } catch(e) { el.innerHTML = '<div class="loading-row" style="color:var(--red)">Error loading subscribers</div>'; }
}

// ─── Exchange Health ──────────────────────────────────────────────────────────
async function loadExchangeHealth() {
  const el = document.getElementById('exch-body');
  const btn = document.getElementById('btn-health');
  el.innerHTML = '<div class="loading-row"><span class="loader"></span>Checking all 5 exchanges in parallel... (~5s)</div>';
  btn.disabled = true;
  try {
    const res = await fetch('/admin/api/exchange-health');
    if (!res.ok) { throw new Error('HTTP ' + res.status + ' — ' + await res.text()); }
    const data = await res.json();
    el.innerHTML = data.map(e => `
      <div class="exch-row">
        <div class="exch-name">${e.exchange}</div>
        <div class="exch-stat"><span class="badge ${e.status==='ok'?'ok':'error'}">${e.status==='ok'?'✓ Live':'✗ Error'}</span>
          ${e.error?`<span style="color:var(--red);font-size:0.72rem;margin-left:8px">${e.error.slice(0,50)}</span>`:''}</div>
        <div class="exch-num">${e.markets}</div>
        <div class="exch-lat">${e.latency}s</div>
      </div>`).join('');
  } catch(e) {
    el.innerHTML = `<div class="loading-row" style="color:var(--red)">Error: ${e.message}</div>`;
  }
  btn.disabled = false;
}

// ─── Opportunities ────────────────────────────────────────────────────────────
async function loadOpportunities() {
  document.getElementById('opp-section').style.display = '';
  const el = document.getElementById('opp-body');
  el.innerHTML = '<div class="loading-row"><span class="loader"></span>Fetching live opportunities across all 5 exchanges... (~12s)</div>';
  try {
    const res = await fetch('/admin/api/opportunities');
    if (!res.ok) { throw new Error('HTTP ' + res.status + ' — ' + await res.text()); }
    const d = await res.json();
    const rows = d.opportunities || [];
    if (!rows.length) { el.innerHTML = '<div class="loading-row">No opportunities above 0% APR right now</div>'; return; }
    el.innerHTML = `<table>
      <thead><tr><th>#</th><th>Coin</th><th>Exchange</th><th>APR</th><th>Rate</th><th>Volume</th><th>OI</th><th>Next Funding</th></tr></thead>
      <tbody>${rows.map((r,i)=>`<tr>
        <td style="color:var(--dim)">${i+1}</td>
        <td style="font-weight:700">${r.coin}</td>
        <td>${r.exchange}</td>
        <td class="${r.apr>=50?'apr-high':r.apr>=20?'apr-mid':'apr-low'}">${r.apr.toFixed(1)}%</td>
        <td style="font-family:monospace;font-size:0.78rem;color:var(--dim)">${r.rate_display}</td>
        <td>${r.volume_24h}</td>
        <td>${r.open_interest}</td>
        <td style="color:var(--dim)">${r.next_funding}</td>
      </tr>`).join('')}</tbody>
    </table>`;
  } catch(e) { el.innerHTML = `<div class="loading-row" style="color:var(--red)">Error: ${e.message}</div>`; }
}

// ─── Run Alerts ───────────────────────────────────────────────────────────────
async function runAlerts() {
  try {
    const d = await fetch('/admin/api/run-alerts', {method:'POST'}).then(r=>r.json());
    toast('Alert run triggered — check Telegram for results');
    setTimeout(loadStats, 5000);
  } catch(e) { toast('Failed to trigger alert run', 'error'); }
}

// ─── Broadcast ───────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('bc-msg').addEventListener('input', function() {
    document.getElementById('bc-count').textContent = this.value.length + ' characters';
  });
});

async function sendBroadcast() {
  const msg = document.getElementById('bc-msg').value.trim();
  if (!msg) { toast('Type a message first', 'error'); return; }
  try {
    const d = await fetch('/admin/api/broadcast', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({message:msg})
    }).then(r=>r.json());
    if (d.ok) { toast('Sent to channel ✓'); document.getElementById('bc-msg').value=''; }
    else toast('Send failed: ' + (d.error||'unknown'), 'error');
  } catch(e) { toast('Error: '+e.message,'error'); }
}

// ─── Invite ───────────────────────────────────────────────────────────────────
async function generateInvite() {
  const email = document.getElementById('inv-email').value.trim();
  const res = document.getElementById('invite-result');
  const linkEl = document.getElementById('invite-link');
  res.style.display = 'none';
  try {
    const d = await fetch('/admin/api/invite', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email})
    }).then(r=>r.json());
    if (d.link) {
      linkEl.textContent = d.link;
      res.style.display = '';
      toast('Invite link generated ✓');
    } else toast('Error: '+(d.error||'unknown'),'error');
  } catch(e) { toast('Error: '+e.message,'error'); }
}

function quickInvite(email) {
  document.getElementById('inv-email').value = email;
  generateInvite();
  document.querySelector('.panel:has(#invite-result)').scrollIntoView({behavior:'smooth'});
}

function copyInvite() {
  const link = document.getElementById('invite-link').textContent;
  navigator.clipboard.writeText(link).then(()=>toast('Copied to clipboard ✓'));
}

// ─── Cancel Sub ───────────────────────────────────────────────────────────────
async function cancelSub(subId, email) {
  if (!confirm('Cancel subscription for ' + email + '? This is immediate.')) return;
  try {
    const d = await fetch('/admin/api/cancel-subscriber', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({sub_id:subId})
    }).then(r=>r.json());
    if (d.ok) {
      toast('Subscription cancelled for ' + email);
      document.getElementById('sub-'+subId)?.remove();
    } else toast('Error: '+(d.error||'unknown'),'error');
  } catch(e) { toast('Error: '+e.message,'error'); }
}

// ─── Bootstrap ────────────────────────────────────────────────────────────────
function refreshAll() { loadStats(); loadSubscribers(); }
document.addEventListener('DOMContentLoaded', () => { loadStats(); loadSubscribers(); });
setInterval(loadStats, 60000);
</script>
</body>
</html>"""


# ─── Routes ──────────────────────────────────────────────────────────────────

def register_admin(server):
    # Session secret: derive from ADMIN_PASSWORD so it's stable across restarts
    secret = hashlib.sha256(
        (ADMIN_PASSWORD + "cf_admin_2026").encode()
    ).hexdigest() if ADMIN_PASSWORD else os.urandom(24).hex()
    server.secret_key = server.secret_key or secret

    @server.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        error = ""
        if request.method == "POST":
            pw = request.form.get("password", "")
            if ADMIN_PASSWORD and pw == ADMIN_PASSWORD:
                session["admin_ok"] = True
                return redirect("/admin/")
            error = "Wrong password"
        return render_template_string(LOGIN_HTML, error=error)

    @server.route("/admin/logout")
    def admin_logout():
        session.clear()
        return redirect("/admin/login")

    @server.route("/admin/")
    @_requires_admin
    def admin_home():
        return render_template_string(ADMIN_HTML)

    @server.route("/admin/api/stats")
    @_requires_admin
    def admin_api_stats():
        data = {"active": 0, "trialing": 0, "mrr": 0, "new_today": 0,
                "recent_payments": [], "last_alert": None}
        if STRIPE_SECRET:
            try:
                s = _stripe()
                active   = s.Subscription.list(status="active",   limit=100)
                trialing = s.Subscription.list(status="trialing", limit=100)
                data["active"]   = len(active.data)
                data["trialing"] = len(trialing.data)
                data["mrr"]      = data["active"] * PRICE_USD

                today_ts = int(datetime.now(timezone.utc)
                               .replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
                data["new_today"] = sum(1 for sub in active.data if sub.created >= today_ts)

                pis = s.PaymentIntent.list(limit=8)
                for pi in pis.data:
                    email = ""
                    if pi.get("receipt_email"):
                        email = pi["receipt_email"]
                    data["recent_payments"].append({
                        "amount": f"${pi.amount/100:.2f}",
                        "status": pi.status,
                        "email": email,
                        "time": datetime.fromtimestamp(
                            pi.created, tz=timezone.utc).strftime("%m/%d %H:%M"),
                    })
            except Exception as e:
                data["stripe_error"] = str(e)

        if _last_alert_run["time"]:
            data["last_alert"] = {
                "time": _last_alert_run["time"],
                "fired": _last_alert_run["fired"],
                "error": _last_alert_run["error"],
            }
        return jsonify(data)

    @server.route("/admin/api/subscribers")
    @_requires_admin
    def admin_api_subscribers():
        subs = []
        if not STRIPE_SECRET:
            return jsonify({"subscribers": subs, "error": "No Stripe key"})
        try:
            s = _stripe()
            for status in ("active", "trialing", "past_due"):
                page = s.Subscription.list(status=status, limit=100,
                                           expand=["data.customer"])
                for sub in page.data:
                    cust = sub.get("customer")
                    email = (cust.email if hasattr(cust, "email")
                             else (cust if isinstance(cust, str) else ""))
                    cid   = cust.id if hasattr(cust, "id") else ""
                    subs.append({
                        "email": email,
                        "status": sub.status,
                        "created": datetime.fromtimestamp(
                            sub.created, tz=timezone.utc).strftime("%Y-%m-%d"),
                        "sub_id": sub.id,
                        "customer_id": cid,
                    })
        except Exception as e:
            return jsonify({"subscribers": [], "error": str(e)})
        return jsonify({"subscribers": subs})

    @server.route("/admin/api/exchange-health")
    @_requires_admin
    def admin_api_exchange_health():
        try:
            from fetcher import (fetch_hyperliquid, fetch_okx, fetch_gateio,
                                 fetch_bitget, fetch_mexc)
            fetchers = [
                ("Hyperliquid", fetch_hyperliquid),
                ("OKX",         fetch_okx),
                ("Gate.io",     fetch_gateio),
                ("BitGet",      fetch_bitget),
                ("MEXC",        fetch_mexc),
            ]
            result_map = {}

            def _run(name, fn):
                t0 = time.time()
                try:
                    rows = fn()
                    return {"exchange": name, "status": "ok",
                            "markets": len(rows), "latency": round(time.time()-t0, 2)}
                except Exception as ex:
                    return {"exchange": name, "status": "error", "markets": 0,
                            "latency": round(time.time()-t0, 2), "error": str(ex)[:80]}

            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = {pool.submit(_run, name, fn): name for name, fn in fetchers}
                for fut in as_completed(futures):
                    r = fut.result()
                    result_map[r["exchange"]] = r

            return jsonify([result_map.get(n, {"exchange": n, "status": "error"})
                            for n, _ in fetchers])
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @server.route("/admin/api/opportunities")
    @_requires_admin
    def admin_api_opportunities():
        from fetcher import fetch_all
        try:
            rows = [r for r in fetch_all() if r["apr"] > 0][:25]
            return jsonify({"opportunities": rows})
        except Exception as e:
            return jsonify({"opportunities": [], "error": str(e)})

    @server.route("/admin/api/invite", methods=["POST"])
    @_requires_admin
    def admin_api_invite():
        email = (request.json or {}).get("email", "").strip()
        if not TG_TOKEN or not TG_CHANNEL:
            return jsonify({"error": "Telegram not configured"})
        try:
            r = req.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/createChatInviteLink",
                json={"chat_id": TG_CHANNEL, "member_limit": 1,
                      "expire_date": int(time.time()) + 86400 * 7},
                timeout=10,
            ).json()
            link = r.get("result", {}).get("invite_link", "")
            if not link:
                return jsonify({"error": r.get("description", "Failed")})
            return jsonify({"link": link, "email": email})
        except Exception as e:
            return jsonify({"error": str(e)})

    @server.route("/admin/api/broadcast", methods=["POST"])
    @_requires_admin
    def admin_api_broadcast():
        message = (request.json or {}).get("message", "").strip()
        if not message:
            return jsonify({"error": "Empty message"})
        ok = _tg(TG_CHANNEL, message)
        return jsonify({"ok": ok})

    @server.route("/admin/api/cancel-subscriber", methods=["POST"])
    @_requires_admin
    def admin_api_cancel():
        sub_id = (request.json or {}).get("sub_id", "").strip()
        if not sub_id or not STRIPE_SECRET:
            return jsonify({"error": "Missing data"})
        try:
            _stripe().Subscription.cancel(sub_id)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)})

    @server.route("/admin/api/run-alerts", methods=["POST"])
    @_requires_admin
    def admin_api_run_alerts():
        def _run():
            try:
                import alert_runner
                fired_before = getattr(alert_runner, "_fired_count", 0)
                alert_runner.main()
                fired = getattr(alert_runner, "_fired_count", fired_before) - fired_before
                _last_alert_run["time"]  = datetime.now(timezone.utc).strftime("%H:%M UTC")
                _last_alert_run["fired"] = fired
                _last_alert_run["error"] = None
            except Exception as ex:
                _last_alert_run["time"]  = datetime.now(timezone.utc).strftime("%H:%M UTC")
                _last_alert_run["fired"] = 0
                _last_alert_run["error"] = str(ex)[:80]
        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"ok": True})
