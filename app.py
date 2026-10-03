"""
WhatsApp Bulk Messenger - GUI App
Uses Selenium to automate WhatsApp Web, SQLite for contact/message storage.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import sqlite3
import time
import os
import sys
from pathlib import Path

# ─── paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DB_PATH  = BASE_DIR / "whatsapp.db"

# ─── database helpers ─────────────────────────────────────────────────────────

def init_db():
    """Create tables and seed demo data."""
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS contacts (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            name    TEXT NOT NULL,
            phone   TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            message    TEXT NOT NULL,
            status     TEXT DEFAULT 'pending',   -- pending | sent | failed
            sent_at    TEXT
        );
    """)
    # Seed the requested contact + message
    cur.execute("INSERT OR IGNORE INTO contacts (name, phone) VALUES (?, ?)",
                ("Demo Contact", "919876543210"))
    cur.execute("""
        INSERT INTO messages (contact_id, message, status)
        SELECT id, 'Hello from WhatsApp Bulk Sender!', 'pending' FROM contacts WHERE phone='919876543210'
          AND NOT EXISTS (
              SELECT 1 FROM messages m
              JOIN contacts c ON m.contact_id=c.id
              WHERE c.phone='919876543210'
          )
    """)
    con.commit()
    con.close()


def get_db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def fetch_contacts(con):
    cur = con.cursor()
    cur.execute("""
        SELECT c.id, c.name, c.phone,
               COUNT(m.id) total,
               SUM(CASE WHEN m.status='sent' THEN 1 ELSE 0 END) sent,
               SUM(CASE WHEN m.status='pending' THEN 1 ELSE 0 END) pending,
               SUM(CASE WHEN m.status='failed' THEN 1 ELSE 0 END) failed
        FROM contacts c
        LEFT JOIN messages m ON m.contact_id=c.id
        GROUP BY c.id ORDER BY c.name
    """)
    return cur.fetchall()


def fetch_messages(con, contact_id):
    cur = con.cursor()
    cur.execute("""
        SELECT id, message, status, sent_at FROM messages
        WHERE contact_id=? ORDER BY id
    """, (contact_id,))
    return cur.fetchall()


def add_contact(con, name, phone):
    cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO contacts (name, phone) VALUES (?,?)", (name, phone))
    con.commit()
    cur.execute("SELECT id FROM contacts WHERE phone=?", (phone,))
    return cur.fetchone()[0]


def add_message(con, contact_id, message):
    cur = con.cursor()
    cur.execute("INSERT INTO messages (contact_id, message) VALUES (?,?)", (contact_id, message))
    con.commit()
    return cur.lastrowid


def update_message_status(con, msg_id, status):
    cur = con.cursor()
    cur.execute("UPDATE messages SET status=?, sent_at=datetime('now','localtime') WHERE id=?",
                (status, msg_id))
    con.commit()


def get_pending_messages(con):
    cur = con.cursor()
    cur.execute("""
        SELECT m.id, c.phone, c.name, m.message
        FROM messages m JOIN contacts c ON m.contact_id=c.id
        WHERE m.status='pending'
        ORDER BY m.id
    """)
    return cur.fetchall()


# ─── selenium whatsapp sender ─────────────────────────────────────────────────

class WhatsAppBot:
    def __init__(self, log_callback):
        self.driver = None
        self.log = log_callback

    def start(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from webdriver_manager.chrome import ChromeDriverManager

        self.log("🚀 Launching Chrome…")
        opts = Options()
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        # Keep browser open (no headless so user can scan QR)
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=opts)
        self.driver.get("https://web.whatsapp.com/")
        self.log("📱 WhatsApp Web opened — please scan the QR code in Chrome.")

    def wait_for_login(self, timeout=120):
        """Block until WhatsApp Web finishes loading after QR scan."""
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.common.by import By

        self.log("⏳ Waiting for QR scan (up to 2 min)…")
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, 'div[data-testid="chat-list"],'
                                      'div[aria-label="Chat list"],'
                                      '#side')
                )
            )
            self.log("✅ Logged in to WhatsApp Web!")
            return True
        except Exception:
            self.log("❌ Login timeout — QR not scanned in time.")
            return False

    def send_message(self, phone, name, message):
        """Open chat by phone URL and send message."""
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys

        url = f"https://web.whatsapp.com/send?phone={phone}&text={message}"
        self.driver.get(url)
        try:
            # Wait for the send button or input box
            input_box = WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR,
                     'div[data-testid="conversation-compose-box-input"],'
                     'footer div[contenteditable="true"],'
                     'div[title="Type a message"]')
                )
            )
            time.sleep(2)
            input_box.click()
            time.sleep(1)
            input_box.send_keys(Keys.RETURN)
            time.sleep(2)
            self.log(f"✅ Sent to {name} ({phone}): {message[:40]}")
            return True
        except Exception as e:
            self.log(f"❌ Failed to send to {name} ({phone}): {e}")
            return False

    def quit(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None


# ─── main application ─────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("WABlast - Whatsapp Bulk Messsage Sender")
        self.geometry("1000x680")
        self.configure(bg="#1a1a2e")
        self.resizable(True, True)

        init_db()
        self.con = get_db()
        self.bot = None
        self.bot_thread = None
        self.send_thread = None
        self.selected_contact_id = None

        self._build_ui()
        self._refresh_contacts()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#1a1a2e")
        style.configure("TLabel", background="#1a1a2e", foreground="#e0e0e0",
                        font=("Segoe UI", 10))
        style.configure("Header.TLabel", background="#16213e", foreground="#00d2ff",
                        font=("Segoe UI", 14, "bold"))
        style.configure("Green.TButton",  background="#25d366", foreground="white",
                        font=("Segoe UI", 10, "bold"), padding=6)
        style.configure("Red.TButton",    background="#e74c3c", foreground="white",
                        font=("Segoe UI", 10, "bold"), padding=6)
        style.configure("Blue.TButton",   background="#2196F3", foreground="white",
                        font=("Segoe UI", 10, "bold"), padding=6)
        style.configure("TEntry", fieldbackground="#0f3460", foreground="white",
                        insertcolor="white")
        style.configure("Treeview", background="#16213e", foreground="#e0e0e0",
                        fieldbackground="#16213e", rowheight=26)
        style.configure("Treeview.Heading", background="#0f3460",
                        foreground="#00d2ff", font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#0f3460")])

        # ── top header ──
        hdr = tk.Frame(self, bg="#16213e", pady=10)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="📱  WhatsApp Bulk Messenger",
                 bg="#16213e", fg="#25d366",
                 font=("Segoe UI", 18, "bold")).pack(side=tk.LEFT, padx=16)

        self.status_lbl = tk.Label(hdr, text="⚫ Not connected",
                                   bg="#16213e", fg="#e74c3c",
                                   font=("Segoe UI", 11, "bold"))
        self.status_lbl.pack(side=tk.RIGHT, padx=16)

        # ── paned layout ──
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Left panel
        left = tk.Frame(paned, bg="#1a1a2e", width=400)
        paned.add(left, weight=2)

        # Right panel
        right = tk.Frame(paned, bg="#1a1a2e", width=380)
        paned.add(right, weight=1)

        self._build_left(left)
        self._build_right(right)

    def _build_left(self, parent):
        # ── Connection card ──
        conn_frame = tk.LabelFrame(parent, text=" WhatsApp Connection ",
                                   bg="#16213e", fg="#00d2ff",
                                   font=("Segoe UI", 10, "bold"),
                                   bd=1, relief=tk.GROOVE)
        conn_frame.pack(fill=tk.X, padx=6, pady=6)

        btn_row = tk.Frame(conn_frame, bg="#16213e")
        btn_row.pack(fill=tk.X, padx=8, pady=8)

        self.btn_connect = tk.Button(btn_row, text="🔗  Open WhatsApp Web & Scan QR",
                                     bg="#25d366", fg="white",
                                     font=("Segoe UI", 10, "bold"),
                                     relief=tk.FLAT, cursor="hand2",
                                     command=self._on_connect)
        self.btn_connect.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)

        self.btn_disconnect = tk.Button(btn_row, text="✖  Disconnect",
                                        bg="#e74c3c", fg="white",
                                        font=("Segoe UI", 10, "bold"),
                                        relief=tk.FLAT, cursor="hand2",
                                        state=tk.DISABLED,
                                        command=self._on_disconnect)
        self.btn_disconnect.pack(side=tk.LEFT, padx=4)

        # ── Contacts + messages ──
        data_frame = tk.LabelFrame(parent, text=" Contacts & Messages ",
                                   bg="#16213e", fg="#00d2ff",
                                   font=("Segoe UI", 10, "bold"),
                                   bd=1, relief=tk.GROOVE)
        data_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        # Contacts tree
        cols = ("name", "phone", "pending", "sent", "failed")
        self.tree = ttk.Treeview(data_frame, columns=cols, show="headings", height=8)
        for col, txt, w in [("name","Name",150), ("phone","Phone",130),
                             ("pending","Pending",60), ("sent","Sent",55),
                             ("failed","Failed",55)]:
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=w, anchor=tk.CENTER if col not in ("name","phone") else tk.W)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self.tree.bind("<<TreeviewSelect>>", self._on_contact_select)

        # Message sub-list
        msg_lbl = tk.Label(data_frame, text="Messages for selected contact:",
                           bg="#16213e", fg="#aaaaaa",
                           font=("Segoe UI", 9))
        msg_lbl.pack(anchor=tk.W, padx=6)

        msg_cols = ("id", "message", "status", "sent_at")
        self.msg_tree = ttk.Treeview(data_frame, columns=msg_cols, show="headings", height=5)
        for col, txt, w in [("id","ID",35), ("message","Message",200),
                             ("status","Status",70), ("sent_at","Sent At",130)]:
            self.msg_tree.heading(col, text=txt)
            self.msg_tree.column(col, width=w, anchor=tk.CENTER if col not in ("message",) else tk.W)
        self.msg_tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self.msg_tree.tag_configure("sent",    foreground="#25d366")
        self.msg_tree.tag_configure("failed",  foreground="#e74c3c")
        self.msg_tree.tag_configure("pending", foreground="#f1c40f")

        # ── Add contact ──
        add_frame = tk.LabelFrame(parent, text=" Add Contact + Message ",
                                  bg="#16213e", fg="#00d2ff",
                                  font=("Segoe UI", 10, "bold"),
                                  bd=1, relief=tk.GROOVE)
        add_frame.pack(fill=tk.X, padx=6, pady=4)

        form = tk.Frame(add_frame, bg="#16213e")
        form.pack(fill=tk.X, padx=8, pady=6)

        for i, (lbl, attr) in enumerate([("Name:", "entry_name"),
                                          ("Phone (with CC):", "entry_phone"),
                                          ("Message:", "entry_msg")]):
            tk.Label(form, text=lbl, bg="#16213e", fg="#aaaaaa",
                     font=("Segoe UI", 9)).grid(row=i, column=0, sticky=tk.W, pady=2)
            e = tk.Entry(form, bg="#0f3460", fg="white", insertbackground="white",
                         font=("Segoe UI", 10), relief=tk.FLAT, width=30)
            e.grid(row=i, column=1, sticky=tk.EW, padx=6, pady=2)
            setattr(self, attr, e)
        form.columnconfigure(1, weight=1)

        # Pre-fill with sample demo data
        self.entry_name.insert(0, "Demo Contact")
        self.entry_phone.insert(0, "919876543210")
        self.entry_msg.insert(0, "Hello!")

        tk.Button(add_frame, text="➕  Add Contact & Message",
                  bg="#2196F3", fg="white",
                  font=("Segoe UI", 10, "bold"),
                  relief=tk.FLAT, cursor="hand2",
                  command=self._on_add).pack(padx=8, pady=6, fill=tk.X)

    def _build_right(self, parent):
        # ── Send controls ──
        send_frame = tk.LabelFrame(parent, text=" Send Messages ",
                                   bg="#16213e", fg="#00d2ff",
                                   font=("Segoe UI", 10, "bold"),
                                   bd=1, relief=tk.GROOVE)
        send_frame.pack(fill=tk.X, padx=6, pady=6)

        self.btn_send_all = tk.Button(send_frame,
                                      text="🚀  Send All Pending Messages",
                                      bg="#25d366", fg="white",
                                      font=("Segoe UI", 11, "bold"),
                                      relief=tk.FLAT, cursor="hand2",
                                      state=tk.DISABLED,
                                      command=self._on_send_all)
        self.btn_send_all.pack(fill=tk.X, padx=8, pady=8)

        tk.Button(send_frame, text="🔄  Refresh Tables",
                  bg="#607d8b", fg="white",
                  font=("Segoe UI", 9),
                  relief=tk.FLAT, cursor="hand2",
                  command=self._refresh_contacts).pack(fill=tk.X, padx=8, pady=4)

        # ── Log ──
        log_frame = tk.LabelFrame(parent, text=" Activity Log ",
                                  bg="#16213e", fg="#00d2ff",
                                  font=("Segoe UI", 10, "bold"),
                                  bd=1, relief=tk.GROOVE)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        self.log_box = scrolledtext.ScrolledText(
            log_frame, bg="#0a0a1a", fg="#e0e0e0",
            font=("Courier New", 9), relief=tk.FLAT,
            state=tk.DISABLED, wrap=tk.WORD)
        self.log_box.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        tk.Button(log_frame, text="🗑  Clear Log",
                  bg="#455a64", fg="white",
                  font=("Segoe UI", 9), relief=tk.FLAT,
                  command=self._clear_log).pack(side=tk.BOTTOM, pady=4)

    # ── actions ───────────────────────────────────────────────────────────────

    def _log(self, msg):
        """Thread-safe log append."""
        def _do():
            self.log_box.config(state=tk.NORMAL)
            self.log_box.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            self.log_box.see(tk.END)
            self.log_box.config(state=tk.DISABLED)
        self.after(0, _do)

    def _clear_log(self):
        self.log_box.config(state=tk.NORMAL)
        self.log_box.delete("1.0", tk.END)
        self.log_box.config(state=tk.DISABLED)

    def _on_connect(self):
        self.btn_connect.config(state=tk.DISABLED)
        self._log("Initialising bot…")

        def run():
            try:
                self.bot = WhatsAppBot(self._log)
                self.bot.start()
                ok = self.bot.wait_for_login()
                if ok:
                    self.after(0, self._on_login_success)
                else:
                    self.after(0, self._on_login_fail)
            except Exception as e:
                self._log(f"❌ Error: {e}")
                self.after(0, self._on_login_fail)

        self.bot_thread = threading.Thread(target=run, daemon=True)
        self.bot_thread.start()

    def _on_login_success(self):
        self.status_lbl.config(text="🟢 Connected", fg="#25d366")
        self.btn_disconnect.config(state=tk.NORMAL)
        self.btn_send_all.config(state=tk.NORMAL)
        self._log("🎉 Ready to send messages!")

    def _on_login_fail(self):
        self.status_lbl.config(text="⚫ Not connected", fg="#e74c3c")
        self.btn_connect.config(state=tk.NORMAL)
        self.btn_disconnect.config(state=tk.DISABLED)
        self.btn_send_all.config(state=tk.DISABLED)

    def _on_disconnect(self):
        if self.bot:
            self.bot.quit()
            self.bot = None
        self._on_login_fail()
        self._log("🔌 Disconnected.")

    def _on_add(self):
        name  = self.entry_name.get().strip()
        phone = self.entry_phone.get().strip().replace("+", "").replace(" ", "")
        msg   = self.entry_msg.get().strip()
        if not name or not phone or not msg:
            messagebox.showwarning("Missing", "Please fill Name, Phone, and Message.")
            return
        if not phone.isdigit():
            messagebox.showwarning("Invalid", "Phone should contain digits only (with country code, e.g. 919876543210).")
            return
        cid = add_contact(self.con, name, phone)
        add_message(self.con, cid, msg)
        self._refresh_contacts()
        self._log(f"\u2795 Added: {name} ({phone}) -- '{msg}'")

    def _on_send_all(self):
        if not self.bot or not self.bot.driver:
            messagebox.showerror("Not connected", "Please connect WhatsApp first.")
            return
        if self.send_thread and self.send_thread.is_alive():
            messagebox.showinfo("Busy", "Sending already in progress…")
            return

        pending = get_pending_messages(self.con)
        if not pending:
            messagebox.showinfo("Done", "No pending messages.")
            return

        self._log(f"📤 Sending {len(pending)} message(s)…")
        self.btn_send_all.config(state=tk.DISABLED)

        def run():
            for msg_id, phone, name, text in pending:
                ok = self.bot.send_message(phone, name, text)
                update_message_status(self.con, msg_id, "sent" if ok else "failed")
                time.sleep(3)
            self._log("✅ All done!")
            self.after(0, self._refresh_contacts)
            self.after(0, lambda: self.btn_send_all.config(state=tk.NORMAL))

        self.send_thread = threading.Thread(target=run, daemon=True)
        self.send_thread.start()

    def _on_contact_select(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        row = self.tree.item(sel[0], "values")
        cid = int(row[0]) if row else None
        if cid:
            self.selected_contact_id = cid
            self._refresh_messages(cid)

    def _refresh_contacts(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        rows = fetch_contacts(self.con)
        for row in rows:
            cid, name, phone, total, sent, pending, failed = row
            self.tree.insert("", tk.END, values=(name, phone, pending or 0, sent or 0, failed or 0),
                             iid=str(cid))
        if self.selected_contact_id:
            self._refresh_messages(self.selected_contact_id)

    def _refresh_messages(self, contact_id):
        for item in self.msg_tree.get_children():
            self.msg_tree.delete(item)
        rows = fetch_messages(self.con, contact_id)
        for mid, message, status, sent_at in rows:
            self.msg_tree.insert("", tk.END,
                                 values=(mid, message, status, sent_at or "—"),
                                 tags=(status,))

    def on_closing(self):
        if self.bot:
            self.bot.quit()
        self.con.close()
        self.destroy()


# ─── entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
