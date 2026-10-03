"""
WhatsApp Bulk Messenger - Flask Web App
Features: Single-page non-scrollable UI, custom modal dialog, full-width,
Excel template download, and Bulk Excel/CSV import.
No personal numbers stored.
"""

import io
import os
import sqlite3
import threading
import time
import webbrowser
from pathlib import Path
from flask import Flask, render_template_string, request, jsonify, send_file
import pandas as pd

BASE_DIR = Path(__file__).parent
DB_PATH  = BASE_DIR / "whatsapp.db"
app      = Flask(__name__)

# ── bot state ──────────────────────────────────────────────────────────────────
state = {
    "driver": None,
    "connected": False,
    "connecting": False,
    "qr_b64": None,
    "logs": [],
    "sending": False
}

def log(msg):
    entry = f"[{time.strftime('%H:%M:%S')}] {msg}"
    state["logs"].append(entry)
    if len(state["logs"]) > 300:
        state["logs"] = state["logs"][-300:]
    print(entry)

# ── database ───────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS contacts (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            name  TEXT NOT NULL,
            phone TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            message    TEXT NOT NULL,
            status     TEXT DEFAULT 'pending',
            sent_at    TEXT
        );
    """)
    # Initialize schema
    con.commit()
    con.close()

def db(): return sqlite3.connect(DB_PATH)

def get_contacts():
    con = db()
    rows = con.execute("""
        SELECT c.id, c.name, c.phone,
               COALESCE(SUM(m.status='pending'),0),
               COALESCE(SUM(m.status='sent'),0),
               COALESCE(SUM(m.status='failed'),0)
        FROM contacts c LEFT JOIN messages m ON m.contact_id=c.id
        GROUP BY c.id ORDER BY c.id DESC
    """).fetchall()
    con.close()
    return rows

def get_pending():
    con = db()
    rows = con.execute("""
        SELECT m.id, c.phone, c.name, m.message
        FROM messages m JOIN contacts c ON m.contact_id=c.id
        WHERE m.status='pending' ORDER BY m.id
    """).fetchall()
    con.close()
    return rows

def mark(mid, status):
    con = db()
    con.execute("UPDATE messages SET status=?, sent_at=datetime('now','localtime') WHERE id=?",
                (status, mid))
    con.commit(); con.close()

# ── Single Page Creative HTML Template ─────────────────────────────────────────
PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WABlast - Whatsapp Bulk Messsage Sender</title>
<style>
:root{
  --bg:#090a0f;--card:#12141d;--card-border:#1e2230;
  --green:#25d366;--green-glow:rgba(37,211,102,0.25);
  --blue:#3b82f6;--blue-glow:rgba(59,130,246,0.25);
  --purple:#a855f7;--purple-glow:rgba(168,85,247,0.25);
  --red:#ef4444;--red-glow:rgba(239,68,68,0.25);
  --yellow:#f59e0b;--text:#f3f4f6;--muted:#9ca3af;
}

*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100vh;width:100vw;overflow:hidden;background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,-apple-system,sans-serif}
body{display:flex;flex-direction:column}

/* ── Top Bar ── */
.topbar{
  height:56px;padding:0 24px;background:var(--card);
  border-bottom:1px solid var(--card-border);
  display:flex;align-items:center;justify-content:space-between;
  flex-shrink:0;z-index:10;
}
.logo-group{display:flex;align-items:center;gap:12px}
.logo{font-size:1.3rem;font-weight:700;color:var(--green);letter-spacing:-.3px;display:flex;align-items:center;gap:8px}
.logo span{color:var(--text)}
.branding-tag{
  font-size:.8rem;color:var(--muted);background:rgba(255,255,255,0.03);
  padding:4px 14px;border-radius:20px;border:1px solid var(--card-border);
}
.branding-tag a{color:var(--blue);text-decoration:none;font-weight:600;transition:color .2s}
.branding-tag a:hover{color:var(--green)}

.status-dot{display:flex;align-items:center;gap:8px;font-size:.85rem;color:var(--muted);font-weight:600}
.pulse-dot{
  width:10px;height:10px;border-radius:50%;background:var(--red);
  transition:all .3s ease;
}
.pulse-dot.on{
  background:var(--green);
  box-shadow:0 0 0 0 var(--green-glow);
  animation:pulse 2s infinite;
}
@keyframes pulse{
  0%{box-shadow:0 0 0 0 rgba(37,211,102,0.7)}
  70%{box-shadow:0 0 0 10px rgba(37,211,102,0)}
  100%{box-shadow:0 0 0 0 rgba(37,211,102,0)}
}

/* ── Main Dashboard Layout (Single Screen, No Body Scroll) ── */
.app-container{
  flex:1;display:grid;grid-template-columns:360px 1fr;
  gap:16px;padding:16px;overflow:hidden;
}

.col-left, .col-right{
  display:flex;flex-direction:column;gap:16px;height:100%;overflow:hidden;
}

/* ── Cards & Glassmorphism ── */
.card{
  background:var(--card);border:1px solid var(--card-border);
  border-radius:14px;padding:18px;display:flex;flex-direction:column;
  position:relative;box-shadow:0 8px 24px rgba(0,0,0,0.3);
}
.card-title{
  font-size:.9rem;font-weight:700;color:var(--muted);
  text-transform:uppercase;letter-spacing:.06em;margin-bottom:12px;
  display:flex;align-items:center;justify-content:space-between;
}

/* ── Buttons ── */
.btn{
  padding:9px 16px;border:none;border-radius:8px;
  font-size:.85rem;font-weight:600;cursor:pointer;
  transition:all .2s cubic-bezier(0.4, 0, 0.2, 1);
  display:inline-flex;align-items:center;gap:7px;
  text-decoration:none;justify-content:center;
}
.btn:hover{transform:translateY(-1px);filter:brightness(1.1)}
.btn:active{transform:translateY(0)}
.btn:disabled{opacity:.35;cursor:not-allowed;transform:none;filter:none}

.btn-green{background:var(--green);color:#000;box-shadow:0 4px 12px var(--green-glow)}
.btn-blue{background:var(--blue);color:#fff;box-shadow:0 4px 12px var(--blue-glow)}
.btn-purple{background:var(--purple);color:#fff;box-shadow:0 4px 12px var(--purple-glow)}
.btn-red{background:var(--red);color:#fff;box-shadow:0 4px 12px var(--red-glow)}
.btn-ghost{background:rgba(255,255,255,0.05);color:var(--text);border:1px solid var(--card-border)}
.btn-ghost:hover{background:rgba(255,255,255,0.08)}
.btn-sm{padding:6px 12px;font-size:.8rem}

/* ── QR Card Display ── */
.qr-container{
  margin-top:12px;padding:14px;background:#06070a;
  border:1px dashed var(--card-border);border-radius:12px;
  display:none;flex-direction:column;align-items:center;gap:10px;
  text-align:center;animation:fadeIn .3s ease;
}
.qr-container img{
  width:210px;height:210px;border-radius:10px;
  border:2px solid var(--green);background:#fff;padding:6px;
  box-shadow:0 0 20px var(--green-glow);
}
.spinner{
  width:28px;height:28px;border:3px solid var(--card-border);
  border-top-color:var(--green);border-radius:50%;
  animation:spin 1s linear infinite;
}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes fadeIn{from{opacity:0;transform:scale(0.95)}to{opacity:1;transform:scale(1)}}

/* ── Form inputs ── */
.form-grid{display:grid;grid-template-columns:1fr 1fr 2fr auto;gap:10px;align-items:end}
.field label{display:block;font-size:.78rem;color:var(--muted);margin-bottom:4px;font-weight:600}
.field input{
  width:100%;padding:9px 12px;background:#08090d;
  border:1px solid var(--card-border);border-radius:8px;
  color:var(--text);font-size:.85rem;outline:none;
  transition:border-color .2s;
}
.field input:focus{border-color:var(--blue)}

/* ── Table (Inner Scrollable) ── */
.table-card{flex:1;overflow:hidden;display:flex;flex-direction:column}
.tbl-wrap{flex:1;overflow-y:auto;border-radius:8px;border:1px solid var(--card-border)}
table{width:100%;border-collapse:collapse;font-size:.85rem}
thead th{
  position:sticky;top:0;background:#0c0e14;color:var(--muted);
  padding:10px 14px;text-align:left;font-size:.75rem;
  text-transform:uppercase;letter-spacing:.05em;z-index:2;
}
tbody tr{transition:background .15s}
tbody tr:hover td{background:rgba(255,255,255,0.02)}
tbody td{padding:10px 14px;border-top:1px solid var(--card-border);vertical-align:middle}
.empty-row td{text-align:center;color:var(--muted);padding:40px}

/* ── Badges ── */
.badge{padding:2px 8px;border-radius:12px;font-size:.75rem;font-weight:600;display:inline-block}
.badge.pending{background:rgba(245,158,11,0.15);color:var(--yellow);border:1px solid rgba(245,158,11,0.3)}
.badge.sent{background:rgba(37,211,102,0.15);color:var(--green);border:1px solid rgba(37,211,102,0.3)}
.badge.failed{background:rgba(239,68,68,0.15);color:var(--red);border:1px solid rgba(239,68,68,0.3)}
.badge.zero{color:var(--card-border)}

/* ── Activity Log (Inner Scrollable) ── */
.log-card{flex:1;overflow:hidden;display:flex;flex-direction:column}
.log-box{
  flex:1;background:#06070a;border:1px solid var(--card-border);
  border-radius:8px;padding:10px;overflow-y:auto;
  font-family:'Courier New',monospace;font-size:.78rem;line-height:1.6;
}
.log-box .g{color:var(--green)}
.log-box .r{color:var(--red)}
.log-box .b{color:var(--blue)}
.log-box .m{color:var(--muted)}

/* ── Custom Animated Confirmation Modal ── */
.modal-overlay{
  position:fixed;inset:0;background:rgba(0,0,0,0.75);
  backdrop-filter:blur(6px);z-index:100;
  display:none;align-items:center;justify-content:center;
  animation:fadeIn .2s ease;
}
.modal-overlay.active{display:flex}
.modal-box{
  background:var(--card);border:1px solid var(--card-border);
  border-radius:16px;padding:24px;width:420px;max-width:90vw;
  box-shadow:0 20px 40px rgba(0,0,0,0.6);text-align:center;
  transform:scale(0.9);animation:modalPop .25s cubic-bezier(0.175, 0.885, 0.32, 1.275) forwards;
}
@keyframes modalPop{to{transform:scale(1)}}
.modal-icon{
  width:48px;height:48px;border-radius:50%;background:rgba(239,68,68,0.15);
  color:var(--red);display:flex;align-items:center;justify-content:center;
  font-size:1.5rem;margin:0 auto 16px auto;border:1px solid rgba(239,68,68,0.3);
}
.modal-title{font-size:1.1rem;font-weight:700;margin-bottom:8px}
.modal-desc{font-size:.85rem;color:var(--muted);line-height:1.5;margin-bottom:20px}
.modal-actions{display:flex;gap:10px;justify-content:center}

/* ── Footer ── */
footer{
  height:36px;padding:0 24px;background:var(--card);
  border-top:1px solid var(--card-border);
  font-size:.8rem;color:var(--muted);display:flex;align-items:center;
  justify-content:center;flex-shrink:0;
}
footer a{color:var(--green);text-decoration:none;font-weight:600;margin-left:4px}
footer a:hover{text-decoration:underline}
</style>
</head>
<body>

<!-- Top Bar -->
<div class="topbar">
  <div class="logo-group">
    <div class="logo">📱 WA<span>Blast</span></div>
  </div>
  <div class="status-dot">
    <div class="pulse-dot" id="dot"></div>
    <span id="statusTxt">Not connected</span>
  </div>
</div>

<!-- Main Application Workspace (Single Page Viewport) -->
<div class="app-container">

  <!-- LEFT COLUMN: Control Center -->
  <div class="col-left">

    <!-- Card 1: Session Connection -->
    <div class="card">
      <div class="card-title">1. Connection Session</div>
      <div style="display:flex;gap:8px">
        <button class="btn btn-green" id="btnConn" onclick="connect()" style="flex:1">
          🔗 Start Session &amp; QR
        </button>
        <button class="btn btn-red btn-sm" id="btnDisc" onclick="disconnect()" disabled>
          Disconnect
        </button>
      </div>

      <!-- Embedded QR Code Container -->
      <div class="qr-container" id="qrCard">
        <div id="qrSpin" class="spinner"></div>
        <div id="qrMsg" style="font-size:.8rem;color:var(--muted)">Initializing WhatsApp Web…</div>
        <img id="qrImg" style="display:none" alt="WhatsApp QR Code">
      </div>
    </div>

    <!-- Card 2: Queue Actions -->
    <div class="card">
      <div class="card-title">3. Dispatcher Queue</div>
      <button class="btn btn-green" id="btnSend" onclick="sendAll()" disabled style="padding:11px;font-size:.9rem;margin-bottom:10px">
        🚀 Send All Pending Messages
      </button>
      <div style="display:flex;gap:8px">
        <button class="btn btn-ghost btn-sm" onclick="loadContacts()" style="flex:1">🔄 Refresh List</button>
        <button class="btn btn-red btn-sm" onclick="promptClearHistory()" style="flex:1">🗑 Clear History</button>
      </div>
    </div>

    <!-- Card 3: Live Log (Flex fill) -->
    <div class="card log-card">
      <div class="card-title">
        <span>Activity Log</span>
        <button class="btn btn-ghost btn-sm" onclick="clearLog()" style="padding:2px 8px;font-size:.75rem">Clear Log</button>
      </div>
      <div class="log-box" id="logBox"></div>
    </div>

  </div>

  <!-- RIGHT COLUMN: Contacts Table & Bulk Operations -->
  <div class="col-right">

    <!-- Card 1: Quick Add & Bulk Upload -->
    <div class="card">
      <div class="card-title">
        <span>2. Contacts &amp; Import</span>
        <div style="display:flex;gap:8px">
          <a href="/download_template" class="btn btn-ghost btn-sm">
            📥 Download Excel Template
          </a>
          <button class="btn btn-purple btn-sm" onclick="document.getElementById('excelFile').click()">
            📤 Bulk Upload Excel/CSV
          </button>
          <input type="file" id="excelFile" accept=".xlsx,.xls,.csv" style="display:none" onchange="uploadExcel(this)">
        </div>
      </div>

      <!-- Add Form -->
      <div class="form-grid">
        <div class="field">
          <label>Name</label>
          <input id="fName" placeholder="e.g. John Doe">
        </div>
        <div class="field">
          <label>Phone (with country code)</label>
          <input id="fPhone" placeholder="e.g. 919876543210">
        </div>
        <div class="field">
          <label>Message</label>
          <input id="fMsg" placeholder="e.g. Hello there!">
        </div>
        <div class="field">
          <button class="btn btn-blue" onclick="addContact()" style="white-space:nowrap">
            ＋ Add Contact
          </button>
        </div>
      </div>
    </div>

    <!-- Card 2: Contacts Table (Flex Fill Scrollable) -->
    <div class="card table-card">
      <div class="card-title">Contact Queue List</div>
      <div class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th style="width:40px">#</th>
              <th>Name</th>
              <th>Phone</th>
              <th>Pending</th>
              <th>Sent</th>
              <th>Failed</th>
            </tr>
          </thead>
          <tbody id="ctbody">
            <tr class="empty-row"><td colspan="6">Loading contacts…</td></tr>
          </tbody>
        </table>
      </div>
    </div>

  </div>

</div>

<!-- Custom Animated Modal for Clear History -->
<div class="modal-overlay" id="clearModal">
  <div class="modal-box">
    <div class="modal-icon">⚠️</div>
    <div class="modal-title">Clear History &amp; Contacts?</div>
    <div class="modal-desc">Are you sure you want to clear all contacts and message history? This action cannot be undone.</div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeClearModal()">Cancel</button>
      <button class="btn btn-red" onclick="confirmClearHistory()">Yes, Clear All</button>
    </div>
  </div>
</div>

<!-- Footer -->
<footer>
  WABlast • Automated WhatsApp Messaging Platform
</footer>

<script>
let logIdx = 0;

// ── Log Append ───────────────────────────────────────────────────────────────
function appendLog(lines){
  const box = document.getElementById('logBox');
  lines.forEach(l=>{
    const d = document.createElement('div');
    if(l.includes('✅')||l.includes('Sent')||l.includes('OK')||l.includes('Logged')||l.includes('Ready'))
      d.className='g';
    else if(l.includes('❌')||l.includes('fail')||l.includes('Error')||l.includes('timeout'))
      d.className='r';
    else if(l.includes('🚀')||l.includes('📤')||l.includes('🎉')||l.includes('Done'))
      d.className='b';
    else d.className='m';
    d.textContent=l;
    box.appendChild(d);
  });
  box.scrollTop=box.scrollHeight;
}
function clearLog(){ document.getElementById('logBox').innerHTML=''; }

// ── Polling Loop ─────────────────────────────────────────────────────────────
async function poll(){
  try{
    const [lr, sr] = await Promise.all([
      fetch('/api/logs?from='+logIdx),
      fetch('/api/status')
    ]);
    const ld = await lr.json();
    const sd = await sr.json();
    if(ld.lines.length){ appendLog(ld.lines); logIdx=ld.next; }
    updateUI(sd);
  }catch(e){}
}
setInterval(poll, 1500);
setInterval(loadContacts, 6000);

// ── UI Status & QR Updates ────────────────────────────────────────────────────
function updateUI(sd){
  const c = sd.connected;
  const connecting = sd.connecting;
  const qr_b64 = sd.qr_b64;

  const dot = document.getElementById('dot');
  dot.className = 'pulse-dot '+(c?'on':'');

  document.getElementById('statusTxt').textContent = c ? 'Connected ✓' : (connecting ? 'Connecting...' : 'Not connected');
  document.getElementById('btnConn').disabled = c || connecting;
  document.getElementById('btnDisc').disabled = !c && !connecting;
  document.getElementById('btnSend').disabled = !c;

  // QR card
  const qrCard = document.getElementById('qrCard');
  const qrSpin = document.getElementById('qrSpin');
  const qrMsg  = document.getElementById('qrMsg');
  const qrImg  = document.getElementById('qrImg');

  if(c){
    qrCard.style.display = 'none';
  } else if(connecting){
    qrCard.style.display = 'flex';
    if(qr_b64){
      qrSpin.style.display = 'none';
      qrMsg.textContent = 'Scan QR code with WhatsApp on your phone:';
      qrImg.src = 'data:image/png;base64,' + qr_b64;
      qrImg.style.display = 'block';
    } else {
      qrSpin.style.display = 'block';
      qrMsg.textContent = 'Generating QR Code...';
      qrImg.style.display = 'none';
    }
  } else {
    qrCard.style.display = 'none';
  }
}

// ── Session Controls ─────────────────────────────────────────────────────────
async function connect(){
  document.getElementById('btnConn').disabled=true;
  document.getElementById('qrCard').style.display='flex';
  document.getElementById('qrSpin').style.display='block';
  document.getElementById('qrMsg').textContent='Initializing WhatsApp Web…';
  document.getElementById('qrImg').style.display='none';
  await fetch('/api/connect',{method:'POST'});
}
async function disconnect(){
  await fetch('/api/disconnect',{method:'POST'});
}

// ── Contacts & Table ─────────────────────────────────────────────────────────
async function loadContacts(){
  const r = await fetch('/api/contacts');
  const rows = await r.json();
  const tbody = document.getElementById('ctbody');
  if(!rows.length){
    tbody.innerHTML='<tr class="empty-row"><td colspan="6">No contacts in database. Add one above or upload Excel.</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(([id,name,phone,pend,sent,fail], idx)=>`
    <tr>
      <td style="color:var(--muted)">${idx+1}</td>
      <td style="font-weight:600">${esc(name)}</td>
      <td style="font-family:monospace">${esc(phone)}</td>
      <td>${pend>0?`<span class="badge pending">${pend}</span>`:`<span class="badge zero">—</span>`}</td>
      <td>${sent>0?`<span class="badge sent">${sent}</span>`:`<span class="badge zero">—</span>`}</td>
      <td>${fail>0?`<span class="badge failed">${fail}</span>`:`<span class="badge zero">—</span>`}</td>
    </tr>
  `).join('');
}

// ── Add Contact ──────────────────────────────────────────────────────────────
async function addContact(){
  const name  = document.getElementById('fName').value.trim();
  const phone = document.getElementById('fPhone').value.trim().replace(/\D/g,'');
  const msg   = document.getElementById('fMsg').value.trim();
  if(!name||!phone||!msg){ alert('Please fill in Name, Phone, and Message.'); return; }
  const r = await fetch('/api/add',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name,phone,message:msg})
  });
  const d = await r.json();
  if(d.ok){
    appendLog([`[${ts()}] ✅ Added: ${name} (${phone}) — "${msg}"`]);
    loadContacts();
    document.getElementById('fName').value='';
    document.getElementById('fPhone').value='';
    document.getElementById('fMsg').value='';
  } else alert(d.error||'Error adding contact');
}

// ── Bulk Upload ──────────────────────────────────────────────────────────────
async function uploadExcel(input){
  if(!input.files || !input.files[0]) return;
  const file = input.files[0];
  const formData = new FormData();
  formData.append('file', file);

  appendLog([`[${ts()}] 📤 Importing ${file.name}…`]);
  const r = await fetch('/api/import', { method:'POST', body:formData });
  const d = await r.json();
  if(d.ok){
    appendLog([`[${ts()}] ✅ Imported ${d.added} contacts/messages from ${file.name}`]);
    loadContacts();
  } else {
    alert(d.error || 'Import failed');
    appendLog([`[${ts()}] ❌ Import error: ${d.error}`]);
  }
  input.value = '';
}

// ── Custom Confirmation Modal Logic ──────────────────────────────────────────
function promptClearHistory(){
  document.getElementById('clearModal').classList.add('active');
}
function closeClearModal(){
  document.getElementById('clearModal').classList.remove('active');
}
async function confirmClearHistory(){
  closeClearModal();
  const r = await fetch('/api/clear_history', {method:'POST'});
  const d = await r.json();
  if(d.ok){
    appendLog([`[${ts()}] 🗑 Cleared all contacts and message history.`]);
    loadContacts();
  }
}

// ── Send Queue ───────────────────────────────────────────────────────────────
async function sendAll(){
  document.getElementById('btnSend').disabled=true;
  const r = await fetch('/api/send',{method:'POST'});
  const d = await r.json();
  if(!d.ok){
    appendLog([`[${ts()}] ❌ ${d.error}`]);
    document.getElementById('btnSend').disabled=false;
  }
}

// ── Helpers ──────────────────────────────────────────────────────────────────
function ts(){ return new Date().toLocaleTimeString(); }
function esc(s){ const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

// ── Init ─────────────────────────────────────────────────────────────────────
loadContacts();
poll();
</script>
</body>
</html>
"""

# ── API Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(PAGE)

@app.route("/download_template")
def download_template():
    """Generates and returns an Excel template file for bulk upload with generic demo data."""
    df = pd.DataFrame([
        {"Name": "Sample Contact 1", "Phone": "919876543210", "Message": "Hello! Welcome to WABlast."},
        {"Name": "Sample Contact 2", "Phone": "919123456789", "Message": "Hi, your notification is ready."}
    ])
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Contacts')
    output.seek(0)
    return send_file(
        output,
        download_name="whatsapp_bulk_template.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.route("/api/status")
def api_status():
    return jsonify(
        connected=state["connected"],
        connecting=state["connecting"],
        qr_b64=state["qr_b64"]
    )

@app.route("/api/logs")
def api_logs():
    fi = int(request.args.get("from", 0))
    lines = state["logs"][fi:]
    return jsonify(lines=lines, next=fi+len(lines))

@app.route("/api/contacts")
def api_contacts():
    return jsonify(get_contacts())

@app.route("/api/add", methods=["POST"])
def api_add():
    data = request.get_json()
    name, phone, msg = data.get("name","").strip(), \
                       data.get("phone","").strip(), \
                       data.get("message","").strip()
    if not all([name, phone, msg]):
        return jsonify(ok=False, error="Missing fields")
    con = db()
    try:
        con.execute("INSERT OR IGNORE INTO contacts (name,phone) VALUES (?,?)", (name,phone))
        con.commit()
        cid = con.execute("SELECT id FROM contacts WHERE phone=?", (phone,)).fetchone()[0]
        con.execute("INSERT INTO messages (contact_id,message) VALUES (?,?)", (cid,msg))
        con.commit()
    except Exception as e:
        return jsonify(ok=False, error=str(e))
    finally:
        con.close()
    return jsonify(ok=True)

@app.route("/api/import", methods=["POST"])
def api_import():
    if 'file' not in request.files:
        return jsonify(ok=False, error="No file uploaded")
    file = request.files['file']
    if not file.filename:
        return jsonify(ok=False, error="Empty filename")

    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)

        df.columns = [str(c).strip().lower() for c in df.columns]

        name_col = next((c for c in df.columns if 'name' in c), None)
        phone_col = next((c for c in df.columns if 'phone' in c or 'number' in c or 'mobile' in c), None)
        msg_col = next((c for c in df.columns if 'msg' in c or 'message' in c or 'text' in c), None)

        if not name_col or not phone_col or not msg_col:
            return jsonify(ok=False, error="File must contain 'Name', 'Phone', and 'Message' columns.")

        con = db()
        added_count = 0
        for _, row in df.iterrows():
            name = str(row[name_col]).strip()
            phone = ''.join(filter(str.isdigit, str(row[phone_col])))
            msg = str(row[msg_col]).strip()
            if name and phone and msg and phone != 'nan':
                con.execute("INSERT OR IGNORE INTO contacts (name,phone) VALUES (?,?)", (name, phone))
                con.commit()
                cid = con.execute("SELECT id FROM contacts WHERE phone=?", (phone,)).fetchone()[0]
                con.execute("INSERT INTO messages (contact_id,message) VALUES (?,?)", (cid, msg))
                con.commit()
                added_count += 1
        con.close()
        return jsonify(ok=True, added=added_count)
    except Exception as e:
        return jsonify(ok=False, error=str(e))

@app.route("/api/clear_history", methods=["POST"])
def api_clear_history():
    con = db()
    con.execute("DELETE FROM messages")
    con.execute("DELETE FROM contacts")
    con.commit()
    con.close()
    return jsonify(ok=True)

@app.route("/api/connect", methods=["POST"])
def api_connect():
    if state["connecting"] or state["connected"]:
        return jsonify(ok=True)

    state["connecting"] = True
    state["qr_b64"] = None

    def run():
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
            from webdriver_manager.chrome import ChromeDriverManager

            profile_dir = str(BASE_DIR / "chrome_session")
            log("🚀 Launching background Chrome session…")
            opts = Options()
            opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
            opts.add_argument(f"--user-data-dir={profile_dir}")

            drv = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
            state["driver"] = drv
            drv.get("https://web.whatsapp.com/")
            log("⏳ Waiting for WhatsApp Web & QR code…")

            start_time = time.time()
            while time.time() - start_time < 180:
                if not state["connecting"]:
                    break

                # 1. Check if logged in
                logged_in_els = drv.find_elements(By.CSS_SELECTOR, 'div[data-testid="chat-list"],#side,div[aria-label="Chat list"]')
                if logged_in_els:
                    state["connected"] = True
                    state["connecting"] = False
                    state["qr_b64"] = None
                    log("✅ Logged in to WhatsApp Web! Ready to send.")
                    return

                # 2. Check for QR code element
                qr_els = drv.find_elements(By.CSS_SELECTOR, 'canvas, div[data-ref], div[data-testid="qr-code"]')
                if qr_els:
                    try:
                        b64 = qr_els[0].screenshot_as_base64
                        if b64:
                            state["qr_b64"] = b64
                    except Exception:
                        pass
                else:
                    try:
                        state["qr_b64"] = drv.get_screenshot_as_base64()
                    except Exception:
                        pass

                time.sleep(1.5)

            if not state["connected"]:
                log("❌ Connection timeout (QR not scanned within 3 minutes).")
                state["connecting"] = False

        except Exception as e:
            log(f"❌ Connection failed: {e}")
            state["connecting"] = False
            state["connected"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify(ok=True)

@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    state["connecting"] = False
    drv = state.get("driver")
    if drv:
        try: drv.quit()
        except: pass
    state["driver"] = None
    state["connected"] = False
    state["qr_b64"] = None
    log("🔌 Disconnected.")
    return jsonify(ok=True)

@app.route("/api/send", methods=["POST"])
def api_send():
    if not state["connected"]:
        return jsonify(ok=False, error="Not connected to WhatsApp")
    if state["sending"]:
        return jsonify(ok=False, error="Already sending…")
    pending = get_pending()
    if not pending:
        return jsonify(ok=False, error="No pending messages found")

    def run():
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        state["sending"] = True
        drv = state["driver"]
        log(f"📤 Sending {len(pending)} message(s)…")
        for mid, phone, name, message in pending:
            try:
                drv.get(f"https://web.whatsapp.com/send?phone={phone}&text={message}")
                box = WebDriverWait(drv, 30).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR,
                        'div[data-testid="conversation-compose-box-input"],'
                        'footer div[contenteditable="true"],'
                        'div[title="Type a message"]'))
                )
                time.sleep(2); box.click(); time.sleep(0.5)
                box.send_keys(Keys.RETURN); time.sleep(2)
                mark(mid, "sent")
                log(f"✅ Sent → {name} ({phone})")
            except Exception as e:
                mark(mid, "failed")
                log(f"❌ Failed → {name}: {e}")
            time.sleep(3)
        state["sending"] = False
        log("🎉 Done!")
    threading.Thread(target=run, daemon=True).start()
    return jsonify(ok=True)

# ── main ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    print("\n" + "─"*55)
    print("  WABlast - Whatsapp Bulk Messsage Sender")
    print("  Open: http://127.0.0.1:5000")
    print("─"*55 + "\n")
    threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
