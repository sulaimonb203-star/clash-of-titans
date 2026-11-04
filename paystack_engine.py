import os
import uuid
import httpx
import sqlite3
import datetime
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import db_manager

logger = logging.getLogger(__name__)

async def buy_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    secret_key = os.getenv("PAYSTACK_SECRET_KEY")
    
    if not secret_key:
        msg = "❌ Paystack is not configured."
        if update.callback_query:
            return await update.callback_query.message.reply_text(msg)
        return await update.message.reply_text(msg)

    ref = f"TITAN_{user.id}_{uuid.uuid4().hex[:6]}"
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json"
    }
    data = {
        "email": f"user{user.id}@gmail.com",
        "amount": 100000, # ₦1,000
        "reference": ref,
        "callback_url": f"https://t.me/{context.bot.username}"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post("https://api.paystack.co/transaction/initialize", json=data, headers=headers)
            res_data = res.json()
            
            if res_data.get("status"):
                auth_url = res_data["data"]["authorization_url"]
                keyboard = [
                    [InlineKeyboardButton("💳 Pay ₦1,000 Now", url=auth_url)],
                    [InlineKeyboardButton("✅ I have paid", callback_data=f"verify_{ref}")]
                ]
                
                text = "👑 <b>TITAN PASS</b> 👑\n\nUnlock the Arena for 30 days!"
                
                # Safely handle both message and callback_query
                if update.callback_query:
                    await update.callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
                else:
                    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            else:
                error_msg = f"❌ Error: {res_data.get('message')}"
                if update.callback_query:
                    await update.callback_query.message.reply_text(error_msg)
                else:
                    await update.message.reply_text(error_msg)
                    
        except Exception as e:
            logger.error(f"Paystack Error: {e}")
            err_msg = f"❌ Network Error: {str(e)}"
            if update.callback_query:
                await update.callback_query.message.reply_text(err_msg)
            else:
                await update.message.reply_text(err_msg)

async def verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ref = query.data.replace("verify_", "")
    secret_key = os.getenv("PAYSTACK_SECRET_KEY")
    headers = {"Authorization": f"Bearer {secret_key}"}
    
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(f"https://api.paystack.co/transaction/verify/{ref}", headers=headers)
            res_data = res.json()
            
            if res_data.get("status") and res_data["data"]["status"] == "success":
                user_id = query.from_user.id
                expiry = (datetime.datetime.now() + datetime.timedelta(days=30)).isoformat()
                
                with sqlite3.connect(db_manager.DB_PATH) as conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE players SET pass_expiry = ? WHERE telegram_id = ?", (expiry, user_id))
                    conn.commit()
                    
                await query.edit_message_text("✅ <b>Payment Verified!</b> Your Titan Pass is active for 30 days.", parse_mode="HTML")
            else:
                await query.edit_message_text(f"❌ Payment not successful. Status: {res_data.get('message')}")
        except Exception as e:
            await query.edit_message_text(f"❌ Verification Error: {str(e)}")
