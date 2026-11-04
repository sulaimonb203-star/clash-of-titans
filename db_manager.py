from dotenv import load_dotenv
load_dotenv()
import sqlite3
import os
import datetime

DB_PATH = os.getenv("DB_PATH", "clash.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # 1. Players
    cur.execute("""CREATE TABLE IF NOT EXISTS players (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        aura INTEGER DEFAULT 1000,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        pass_expiry TEXT,
        vip_status INTEGER DEFAULT 0
    )""")
    
    # 2. Aura Transactions (Audit Log)
    cur.execute("""CREATE TABLE IF NOT EXISTS aura_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER,
        amount INTEGER,
        reason TEXT,
        timestamp TEXT
    )""")
    
    # 3. Question Bank (Cooldowns)
    cur.execute("""CREATE TABLE IF NOT EXISTS question_bank (
        question_id TEXT PRIMARY KEY,
        subject TEXT,
        topic TEXT,
        year TEXT,
        last_used_date TEXT
    )""")
    
    # 4. Coverage Log (For Siege/Melee)
    cur.execute("""CREATE TABLE IF NOT EXISTS coverage_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        subject TEXT,
        topic TEXT
    )""")
    
    # 5. Settings
    cur.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    
    # 6. Payments
    cur.execute("""CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER,
        amount INTEGER,
        reference TEXT,
        timestamp TEXT
    )""")
    
    conn.commit()
    conn.close()

def register_player(user_id, username, first_name):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO players (telegram_id, username, first_name) VALUES (?, ?, ?)", (user_id, username, first_name))
        conn.commit()

def get_profile(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM players WHERE telegram_id = ?", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None

def update_aura(user_id, amount, reason):
    now = datetime.datetime.now().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        # Update Player Aura (cannot drop below 0)
        cur.execute("UPDATE players SET aura = MAX(0, aura + ?) WHERE telegram_id = ?", (amount, user_id))
        # Log Transaction
        cur.execute("INSERT INTO aura_transactions (telegram_id, amount, reason, timestamp) VALUES (?, ?, ?, ?)", (user_id, amount, reason, now))
        conn.commit()

def has_valid_pass(user_id):
    # 🔥 ADMIN BYPASS: The Arbiter always has access
    if int(user_id) == 8507903968: 
        return True
        
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT pass_expiry FROM players WHERE telegram_id = ?", (user_id,))
        row = cur.fetchone()
        if row and row[0]:
            expiry = datetime.datetime.fromisoformat(row[0])
            return expiry > datetime.datetime.now()
    return False
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT pass_expiry FROM players WHERE telegram_id = ?", (user_id,))
        row = cur.fetchone()
        if row and row[0]:
            expiry = datetime.datetime.fromisoformat(row[0])
            return expiry > datetime.datetime.now()
    return False

def log_question_used(q_id, subject, topic, year):
    now = datetime.datetime.now().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO question_bank (question_id, subject, topic, year, last_used_date) VALUES (?, ?, ?, ?, ?)", (q_id, subject, topic, year, now))
        conn.commit()

def is_question_on_cooldown(q_id, days=14):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT last_used_date FROM question_bank WHERE question_id = ?", (q_id,))
        row = cur.fetchone()
        if row and row[0]:
            last_used = datetime.datetime.fromisoformat(row[0])
            if (datetime.datetime.now() - last_used).days < days:
                return True
    return False

def set_setting(key, value):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()

def get_setting(key):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else None

if __name__ == "__main__":
    init_db()
    print("✅ Database initialized with 6 tables.")


def set_event_lock(active: bool):
    set_setting("event_active", "true" if active else "false")

def is_event_active():
    return get_setting("event_active") == "true"


def update_streak(user_id):
    today = datetime.date.today()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT last_played_date, streak FROM players WHERE telegram_id = ?", (user_id,))
        row = cur.fetchone()
        if row:
            last_played, streak = row
            if last_played:
                last_date = datetime.date.fromisoformat(last_played)
                delta = (today - last_date).days
                if delta == 1:
                    streak += 1
                elif delta > 1:
                    streak = 1
            else:
                streak = 1
            cur.execute("UPDATE players SET last_played_date = ?, streak = ? WHERE telegram_id = ?", (today.isoformat(), streak, user_id))
            conn.commit()
            return streak
    return 0

def get_leaderboard():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT first_name, aura FROM players ORDER BY aura DESC LIMIT 10")
        return cur.fetchall()
