import pytz
import asyncio
import logging
import datetime
import json
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes
from aloc_manager import fetch_questions, fetch_bulk_questions, fetch_topics
import db_manager
import telegram_rich
import admin_engine
from ai_engine import get_explanation_async

logger = logging.getLogger(__name__)
ACTIVE_CRUCIBLE = {}
SUBJECT_TOPICS_CACHE = {}
CALCULATOR_URL = "https://web-production-038a36.up.railway.app/calculator"

class Crucible:
    def __init__(self, chat_id, is_test=False):
        self.chat_id = chat_id
        self.is_test = is_test
        self.total_qs = 3 if is_test else 100
        self.questions = []
        self.current_q = 0
        self.poll_id = None
        self.stats = {}
        self.q_start_time = None
        self.timer = int(db_manager.get_setting("crucible_timer") or 25)

    async def start(self, context):
        subject = db_manager.get_setting("current_subject") or "physics"
        topic_slug = db_manager.get_setting("current_topic") or "general"
        topic_name = db_manager.get_setting("current_topic_name") or "General"

        title = "🧪 *TEST CRUCIBLE* 🧪" if self.is_test else "🔥 *THE CRUCIBLE HAS BEGUN!* 🔥"
        msg = f"{title}{chr(10)}{chr(10)}Subject: {subject.title()}{chr(10)}Topic: {topic_name}{chr(10)}Timer: {self.timer}s{chr(10)}Forging {self.total_qs} questions..."
        await context.bot.send_message(self.chat_id, msg, parse_mode="Markdown")

        if self.is_test:
            qs_list, _ = await fetch_questions(subject=subject, topic=topic_slug, limit=self.total_qs)
            self.questions = qs_list
        else:
            self.questions = await fetch_bulk_questions(subject=subject, topic=topic_slug, total_needed=self.total_qs)

        if not self.questions:
            print(f"⚠️ ALOC returned 0 questions for {topic_slug}. Falling back to General {subject}.")
            await context.bot.send_message(self.chat_id, f"⚠️ ALOC has 0 questions for the slug <code>{topic_slug}</code>. Falling back to General {subject.title()} questions.", parse_mode="HTML")
            self.questions = await fetch_bulk_questions(subject=subject, total_needed=self.total_qs)
            
        if not self.questions:
            await context.bot.send_message(self.chat_id, "❌ ALOC API is completely down or has no questions. Please try again later.")
            return await self.end_crucible(context)

        self.total_qs = len(self.questions)
        
        if self.total_qs < 100 and not self.is_test:
            await context.bot.send_message(self.chat_id, f"⚠️ <b>Note:</b> ALOC only has {self.total_qs} questions for this exact topic. Running a {self.total_qs}-question drill.", parse_mode="HTML")
        else:
            await context.bot.send_message(self.chat_id, f"✅ {self.total_qs} questions loaded. Starting in 3 seconds...")
            
        await asyncio.sleep(3)
        await self.send_next_poll(context)

    async def send_next_poll(self, context):
        if self.current_q >= self.total_qs:
            return await self.end_crucible(context)

        q = self.questions[self.current_q]
        
        # 1. HARD LIMIT: Options must be <= 100 characters
        options = [
            str(q["options"]["A"])[:100],
            str(q["options"]["B"])[:100],
            str(q["options"]["C"])[:100],
            str(q["options"]["D"])[:100]
        ]
        correct_idx = ["A", "B", "C", "D"].index(q["correct"])
        
        # 2. HARD LIMIT: Question must be <= 300 characters
        question_text = f"Q{self.current_q + 1}/{self.total_qs}: {q['question']}"
        question_text = question_text[:300]
        
        self.q_start_time = datetime.datetime.now()

        try:
            if q.get("imageUrl"):
                try:
                    await context.bot.send_photo(chat_id=self.chat_id, photo=q["imageUrl"], caption=f"📸 Image for Q{self.current_q + 1}")
                    await asyncio.sleep(1)
                except: pass

            # 3. HARD LIMIT: Explanation must be <= 200 characters
            ai_explanation = await get_explanation_async(q['question'], q['correct'], q['options'])
            ai_explanation = ai_explanation[:195] # Safe cutoff

            # 4. Try sending WITH the calculator button first
            try:
                calc_btn = InlineKeyboardButton(text="🧮 Calculator", web_app=WebAppInfo(url=CALCULATOR_URL))
                calc_keyboard = InlineKeyboardMarkup([[calc_btn]])
                
                msg = await context.bot.send_poll(
                    chat_id=self.chat_id,
                    question=question_text,
                    options=options,
                    type=Poll.QUIZ,
                    correct_option_id=correct_idx,
                    is_anonymous=False,
                    open_period=self.timer,
                    explanation=ai_explanation,
                    reply_markup=calc_keyboard
                )
            except Exception as e1:
                # 5. If the calculator button fails (Telegram group restriction), send WITHOUT it
                print(f"⚠️ Poll failed with calculator button: {e1}. Retrying without button...")
                msg = await context.bot.send_poll(
                    chat_id=self.chat_id,
                    question=question_text,
                    options=options,
                    type=Poll.QUIZ,
                    correct_option_id=correct_idx,
                    is_anonymous=False,
                    open_period=self.timer,
                    explanation=ai_explanation
                )

            self.poll_id = msg.poll.id
        except Exception as e:
            # 6. Log the EXACT error so we can see it in Railway
            logger.error(f"🚨 CRUCIAL POLL ERROR on Q{self.current_q + 1}: {e}")
            await context.bot.send_message(self.chat_id, f"⚠️ Skipping Q{self.current_q + 1} due to a Telegram error.")
            self.current_q += 1
            await asyncio.sleep(2)
            return await self.send_next_poll(context)

        await asyncio.sleep(self.timer)
        self.current_q += 1
        await self.send_next_poll(context)
    async def handle_answer(self, answer):
        if answer.poll_id != self.poll_id: return
        user = answer.user
        user_id = user.id
        if user_id not in self.stats:
            self.stats[user_id] = {'name': user.first_name, 'score': 0, 'answered': 0, 'missed': 0, 'total_time': 0}

        time_taken = (datetime.datetime.now() - self.q_start_time).total_seconds()
        self.stats[user_id]['answered'] += 1
        self.stats[user_id]['total_time'] += time_taken

        q = self.questions[self.current_q]
        correct_idx = ["A", "B", "C", "D"].index(q["correct"])
        if answer.option_ids[0] == correct_idx:
            self.stats[user_id]['score'] += 1

    async def end_crucible(self, context):
        if not self.stats:
            if self.chat_id in ACTIVE_CRUCIBLE: del ACTIVE_CRUCIBLE[self.chat_id]
            return await context.bot.send_message(self.chat_id, "🏁 *CRUCIBLE ENDED!* No one participated.", parse_mode="Markdown")

        final_stats = {}
        for uid, data in self.stats.items():
            missed = self.total_qs - data['answered']
            percent = (data['score'] / self.total_qs) * 100 if self.total_qs > 0 else 0
            real_score = (data['score'] / self.total_qs) * 400 if self.total_qs > 0 else 0
            aura_bonus = int(data['score'] * 2)
            mins = int(data['total_time'] // 60)
            secs = int(data['total_time'] % 60)

            final_stats[uid] = {
                'name': data['name'], 'score': data['score'], 'answered': data['answered'],
                'missed': missed, 'time_str': f"{mins}m{secs}s", 'percent': percent,
                'real_score': int(real_score), 'aura': aura_bonus
            }
            db_manager.update_aura(uid, aura_bonus, "Crucible Participation")
            db_manager.update_streak(uid)

        md_table = telegram_rich.generate_native_table(final_stats, title="🏆 THE CRUCIBLE RESULTS 🏆")
        await telegram_rich.send_native_table(context.bot, self.chat_id, md_table, "CRUCIBLE RESULTS")
        if self.chat_id in ACTIVE_CRUCIBLE: del ACTIVE_CRUCIBLE[self.chat_id]

# --- STOP COMMAND ---
async def stop_crucible_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != admin_engine.ADMIN_ID: return
    chat_id = update.effective_chat.id
    if chat_id in ACTIVE_CRUCIBLE:
        del ACTIVE_CRUCIBLE[chat_id]
        await update.message.reply_text("🛑 *CRUCIBLE CANCELLED BY THE ARBITER.*", parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ No active Crucible found in this chat.")

# --- SETUP WIZARD ---
async def set_topic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != admin_engine.ADMIN_ID:
        return await update.message.reply_text("❌ Only the Arbiter can use this command.")

    keyboard = [
        [InlineKeyboardButton("Physics", callback_data="wiz_sub_physics"), InlineKeyboardButton("Chemistry", callback_data="wiz_sub_chemistry")],
        [InlineKeyboardButton("Biology", callback_data="wiz_sub_biology"), InlineKeyboardButton("Math", callback_data="wiz_sub_mathematics")],
        [InlineKeyboardButton("English", callback_data="wiz_sub_english")]
    ]
    await update.message.reply_text("🛠️ <b>CRUCIBLE SETUP WIZARD</b> 🛠️\n\nStep 1: Select the Subject", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("wiz_sub_"):
        subject = data.replace("wiz_sub_", "")
        context.user_data['wiz_subject'] = subject
        
        try:
            with open("syllabus.json", "r") as f:
                syllabus = json.load(f)
                topics = syllabus.get(subject, [])
        except:
            return await query.edit_message_text("❌ Could not read syllabus.json.")
            
        if not topics:
            return await query.edit_message_text(f"❌ No topics found for {subject}.")

        SUBJECT_TOPICS_CACHE[subject] = topics
        keyboard = [[InlineKeyboardButton("🎲 General (All)", callback_data=f"wiz_top_{subject}_-1")]]
        for i, t in enumerate(topics):
            display = t[:60] + "..." if len(t) > 60 else t
            keyboard.append([InlineKeyboardButton(display, callback_data=f"wiz_top_{subject}_{i}")])
            
        await query.edit_message_text(f"Step 2: Select a topic for <b>{subject.title()}</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data.startswith("wiz_top_"):
        parts = data.split("_")
        subject = parts[2]
        index = parts[3]
        
        context.user_data['wiz_subject'] = subject
        if index == "-1":
            context.user_data['wiz_topic_slug'] = "general"
            context.user_data['wiz_topic_name'] = "General (All Topics)"
        else:
            topics = SUBJECT_TOPICS_CACHE.get(subject, [])
            topic_name = topics[int(index)]
            context.user_data['wiz_topic_name'] = topic_name
            
            # Fetch the exact slug from the API
            api_topics = await fetch_topics(subject)
            real_slug = "general"
            for t in api_topics:
                if t.get('displayName') == topic_name or t.get('name') == topic_name:
                    real_slug = t.get('name')
                    break
            context.user_data['wiz_topic_slug'] = real_slug

        default_s = "30" if subject == "chemistry" else "25"
        keyboard = [
            [InlineKeyboardButton("15s", callback_data="wiz_time_15"), InlineKeyboardButton("20s", callback_data="wiz_time_20")],
            [InlineKeyboardButton("25s", callback_data="wiz_time_25"), InlineKeyboardButton(f"{default_s}s (Recommended)", callback_data=f"wiz_time_{default_s}")],
            [InlineKeyboardButton("40s", callback_data="wiz_time_40")]
        ]
        await query.edit_message_text(f"Subject: {subject.title()}\nTopic: {context.user_data['wiz_topic_name']}\n\nStep 3: Select the timer:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("wiz_time_"):
        timer = int(data.replace("wiz_time_", ""))
        context.user_data['wiz_timer'] = timer
        
        # Step 4: Start Time
        keyboard = [
            [InlineKeyboardButton("🚀 Start Now", callback_data="wiz_start_0")],
            [InlineKeyboardButton("⏳ In 15 Mins", callback_data="wiz_start_15"), InlineKeyboardButton("⏳ In 30 Mins", callback_data="wiz_start_30")],
            [InlineKeyboardButton("⏳ In 1 Hour", callback_data="wiz_start_60")]
        ]
        await query.edit_message_text("Step 4: When should the Crucible start?", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("wiz_start_"):
        delay_mins = int(data.replace("wiz_start_", ""))
        timer = context.user_data.get('wiz_timer', 25)
        subj = context.user_data.get('wiz_subject')
        slug = context.user_data.get('wiz_topic_slug')
        name = context.user_data.get('wiz_topic_name')
        
        db_manager.set_setting("current_subject", subj)
        db_manager.set_setting("current_topic", slug)
        db_manager.set_setting("current_topic_name", name)
        db_manager.set_setting("crucible_timer", str(timer))
        
        # Calculate Times
        lagos_tz = pytz.timezone('Africa/Lagos')
        start_time = datetime.datetime.now(lagos_tz) + datetime.timedelta(minutes=delay_mins)
        # 100 Questions * timer (in seconds)
        end_time = start_time + datetime.timedelta(seconds=100 * timer) 
        start_str = start_time.strftime("%I:%M %p")
        end_str = end_time.strftime("%I:%M %p")
        
        arena_id = db_manager.get_setting("arena_chat_id")
        
        msg = (
            f"🚨 <b>NEW CRUCIBLE CONFIGURED</b> 🚨\n\n"
            f"📚 <b>Subject:</b> {subj.title()}\n"
            f"🎯 <b>Topic:</b> {name}\n"
            f"⏱️ <b>Timer:</b> {timer} seconds per question\n"
            f"📝 <b>Questions:</b> 100\n\n"
            f"🕒 <b>Starts:</b> {start_str}\n"
            f"🕒 <b>Expected End:</b> {end_str}\n\n"
            f"<i>Get ready, Titans! The Arena awaits your knowledge.</i>"
        )
        
        if not arena_id:
            return await query.edit_message_text("❌ <b>Error:</b> The Arena group is not set!\n\nPlease go to your group and type <code>/setarena</code> first.", parse_mode="HTML")
            
        try:
            await context.bot.send_message(int(arena_id), msg, parse_mode="HTML")
            
            if delay_mins == 0:
                # Start immediately in 10 seconds
                await query.edit_message_text("✅ <b>Configuration Saved!</b>\n\nThe bot has notified the Arena.\n\n⚔️ The Crucible is starting right now!", parse_mode="HTML")
                await asyncio.sleep(3)
                ACTIVE_CRUCIBLE[int(arena_id)] = Crucible(int(arena_id), is_test=False)
                asyncio.create_task(ACTIVE_CRUCIBLE[int(arena_id)].start(context))
            else:
                # Schedule it
                await query.edit_message_text(f"✅ <b>Configuration Saved!</b>\n\nThe bot has notified the Arena.\n\n⏳ The Crucible will automatically start at {start_str}.", parse_mode="HTML")
                
                async def delayed_start_with_reminders():
                    start_msg = f"🚨 <b>THE CRUCIBLE IS STARTING NOW!</b> 🚨\n\nSubject: {subj.title()}\nTopic: {name}\n\nGet ready!"
                    
                    if delay_mins >= 10:
                        # Wait until 10 mins before start
                        await asyncio.sleep((delay_mins - 10) * 60)
                        await context.bot.send_message(int(arena_id), f"⏳ <b>REMINDER:</b> The {subj.title()} Crucible ({name}) starts in <b>10 minutes</b>!", parse_mode="HTML")
                        
                        # Wait 5 mins (now 5 mins before start)
                        await asyncio.sleep(5 * 60)
                        await context.bot.send_message(int(arena_id), f"⏳ <b>REMINDER:</b> The {subj.title()} Crucible starts in <b>5 minutes</b>! Get your calculators ready.", parse_mode="HTML")
                        
                        # Wait the final 5 mins
                        await asyncio.sleep(5 * 60)
                    else:
                        # If scheduled for less than 10 mins, just wait the remaining time
                        await asyncio.sleep(delay_mins * 60)
                        
                    # Send Start Message and Launch
                    await context.bot.send_message(int(arena_id), start_msg, parse_mode="HTML")
                    ACTIVE_CRUCIBLE[int(arena_id)] = Crucible(int(arena_id), is_test=False)
                    await ACTIVE_CRUCIBLE[int(arena_id)].start(context)
                    
                asyncio.create_task(delayed_start_with_reminders())
                
        except Exception as e:
            await query.edit_message_text(f"❌ <b>Failed to send message to the group.</b>\nError: {e}", parse_mode="HTML")

async def set_general_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_manager.set_setting("current_subject", "physics")
    db_manager.set_setting("current_topic", "general")
    db_manager.set_setting("current_topic_name", "General")
    db_manager.set_setting("crucible_timer", "25")
    await update.message.reply_text("✅ Topic cleared. Bot will fetch random questions.", parse_mode="HTML")

async def test_crucible(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ACTIVE_CRUCIBLE[chat_id] = Crucible(chat_id, is_test=True)
    asyncio.create_task(ACTIVE_CRUCIBLE[chat_id].start(context))

async def route_crucible_answers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    for crucible in ACTIVE_CRUCIBLE.values():
        if crucible.poll_id == answer.poll_id:
            await crucible.handle_answer(answer)
            break

async def start_crucible_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != admin_engine.ADMIN_ID: return
    chat_id = update.effective_chat.id
    if chat_id in ACTIVE_CRUCIBLE:
        return await update.message.reply_text("⚠️ A Crucible is already running here.")
    ACTIVE_CRUCIBLE[chat_id] = Crucible(chat_id, is_test=False)
    asyncio.create_task(ACTIVE_CRUCIBLE[chat_id].start(context))
