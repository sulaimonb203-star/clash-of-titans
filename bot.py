import os
import logging
import datetime
import pytz
import random
import threading
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, PollAnswerHandler, MessageHandler, filters, ContextTypes

load_dotenv()

import db_manager
import duel_engine
import crucible_engine
import siege_engine
import melee_engine
import cbt_engine
import paystack_engine
import admin_engine
from webhook_server import run_server

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_manager.register_player(user.id, user.username, user.first_name)
    msg = (
        f"⚔️ <b>WELCOME TO THE ARENA, {user.first_name.upper()}!</b> ⚔️\n\n"
        f"You have been inducted as a Titan with 1000 Aura.\n\n"
        f"🎯 <b>Your Dashboard:</b>\n"
        f"Use the buttons below to navigate the Arena."
    )
    keyboard = [
        [InlineKeyboardButton("🎯 Practice CBT", callback_data="dash_cbt"), InlineKeyboardButton("⚡ Blitz Mode", callback_data="dash_blitz")],
        [InlineKeyboardButton("⚔️ Challenge", callback_data="dash_clash")],
        [InlineKeyboardButton("👤 My Profile", callback_data="dash_profile"), InlineKeyboardButton("🎟️ Titan Pass", callback_data="dash_pass")]
    ]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    prof = db_manager.get_profile(user.id)
    if not prof: return
    
    pass_status = "✅ Active" if db_manager.has_valid_pass(user.id) else "❌ Inactive"
    msg = (
        f"🛡️ <b>{prof['first_name'].upper()}'S TITAN PROFILE</b> 🛡️\n\n"
        f"🏅 <b>Rank:</b> Mortal\n"
        f"✨ <b>Aura:</b> {prof['aura']}\n\n"
        f"✅ <b>Wins:</b> {prof['wins']}\n"
        f"❌ <b>Losses:</b> {prof['losses']}\n\n"
        f"🎟️ <b>Titan Pass:</b> {pass_status}"
    )
    if update.callback_query:
        await update.callback_query.message.reply_text(msg, parse_mode="HTML")
    else:
        await update.message.reply_text(msg, parse_mode="HTML")

async def dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "dash_cbt":
        await cbt_engine.start_cbt(update, context)
    elif data == "dash_blitz":
        await cbt_engine.start_blitz(update, context)
    elif data == "dash_clash":
        await query.message.reply_text("⚔️ To challenge someone, go to the group and reply to their message with `/clash`.", parse_mode="Markdown")
    elif data == "dash_profile":
        await profile_cmd(update, context)
    elif data == "dash_pass":
        await paystack_engine.buy_pass(update, context)

async def setarena(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_manager.set_setting("arena_chat_id", update.effective_chat.id)
    await update.message.reply_text("✅ Group set as The Arena.")

async def send_daily_countdown(context: ContextTypes.DEFAULT_TYPE):
    days_left = (datetime.date(2027, 3, 15) - datetime.date.today()).days
    quotes = ["The strength of a Titan is forged in discipline.", "Every question is a step closer to the summit."]
    msg = f"⏳ <b>{days_left} DAYS UNTIL UTME 2027</b> ⏳\n\n<i>{random.choice(quotes)}</i>"
    chat_id = db_manager.get_setting("arena_chat_id")
    if chat_id:
        try: await context.bot.send_message(int(chat_id), msg, parse_mode="HTML")
        except: pass

def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        print("❌ BOT_TOKEN missing.")
        return

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("setarena", setarena))
    app.add_handler(CallbackQueryHandler(dashboard_callback, pattern="^dash_"))

    app.add_handler(CommandHandler("buypass", paystack_engine.buy_pass))
    app.add_handler(CallbackQueryHandler(paystack_engine.verify_payment, pattern="^verify_"))

    app.add_handler(CommandHandler("grantpass", admin_engine.grant_pass))
    app.add_handler(CommandHandler("broadcast", admin_engine.broadcast))
    app.add_handler(CommandHandler("settopic", crucible_engine.set_topic_command))
    app.add_handler(CallbackQueryHandler(crucible_engine.wizard_callback, pattern="^wiz_"))
    app.add_handler(CommandHandler("setgeneral", crucible_engine.set_general_command))

    app.add_handler(CommandHandler("testcrucible", crucible_engine.test_crucible))
    app.add_handler(CommandHandler("testsiege", siege_engine.test_siege))
    app.add_handler(CommandHandler("testmelee", melee_engine.test_melee))
    app.add_handler(CommandHandler("startcrucible", crucible_engine.start_crucible_now))
    app.add_handler(CommandHandler("stopcrucible", crucible_engine.stop_crucible_command))

    app.add_handler(CommandHandler("clash", duel_engine.start_clash))
    app.add_handler(CallbackQueryHandler(duel_engine.config_callback, pattern="^cfg_"))
    app.add_handler(CallbackQueryHandler(duel_engine.accept_reject_callback, pattern="^clash_"))

    app.add_handler(CommandHandler("cbt", cbt_engine.start_cbt))
    app.add_handler(CommandHandler("blitz", cbt_engine.start_blitz))
    app.add_handler(CallbackQueryHandler(cbt_engine.cbt_config_callback, pattern="^cbt_"))

    # Poll Routers (grouped properly so they don't clash)
    app.add_handler(PollAnswerHandler(duel_engine.route_poll_answers), group=0)
    app.add_handler(PollAnswerHandler(crucible_engine.route_crucible_answers), group=1)
    app.add_handler(PollAnswerHandler(siege_engine.route_siege_answers), group=2)
    app.add_handler(PollAnswerHandler(melee_engine.route_melee_answers), group=3)
    app.add_handler(PollAnswerHandler(cbt_engine.route_cbt_answers), group=4)

    app.job_queue.run_daily(send_daily_countdown, time=datetime.time(hour=7, minute=0, tzinfo=pytz.timezone('Africa/Lagos')))

    # Start Flask in a background thread so Railway sees a live web server
    print("🌐 Starting Flask web server...")
    threading.Thread(target=run_server, daemon=True).start()

    print("⚔️ Clash of Titans is starting in POLLING mode...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    db_manager.init_db()
    main()
