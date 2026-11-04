import datetime
import sqlite3
from telegram import Update
from telegram.ext import ContextTypes
import db_manager

ADMIN_ID = 8507903968

async def is_admin(update: Update):
    return update.effective_user.id == ADMIN_ID

async def grant_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update): return
    if not update.message.reply_to_message:
        return await update.message.reply_text("⚠️ Reply to a user to grant pass.")
    target = update.message.reply_to_message.from_user
    expiry = (datetime.datetime.now() + datetime.timedelta(days=30)).isoformat()
    with sqlite3.connect(db_manager.DB_PATH) as conn:
        conn.execute("UPDATE players SET pass_expiry = ? WHERE telegram_id = ?", (expiry, target.id))
        conn.commit()
    await update.message.reply_text(f"✅ Pass granted to {target.first_name}.")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update): return
    msg = update.message.text.replace("/broadcast", "").strip()
    if not msg: return await update.message.reply_text("⚠️ Provide a message.")
    
    # 1. Send to Group
    chat_id = db_manager.get_setting("arena_chat_id")
    if chat_id:
        try:
            await context.bot.send_message(int(chat_id), f"📢 *ARBITER ANNOUNCEMENT:*\n\n{msg}", parse_mode="Markdown")
        except Exception as e:
            pass

    # 2. Send to DMs
    with sqlite3.connect(db_manager.DB_PATH) as conn:
        players = conn.execute("SELECT telegram_id FROM players").fetchall()
    success = 0
    for p in players:
        try:
            await context.bot.send_message(p[0], f"📢 *ARBITER ANNOUNCEMENT:*\n\n{msg}", parse_mode="Markdown")
            success += 1
        except: pass
    await update.message.reply_text(f"✅ Broadcast sent to Group and {success} players in DM.")
