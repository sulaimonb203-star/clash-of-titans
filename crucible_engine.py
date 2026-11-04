import asyncio
import logging
import datetime
import json
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from aloc_manager import fetch_questions, fetch_bulk_questions
import db_manager
import telegram_rich
import admin_engine

logger = logging.getLogger(__name__)
ACTIVE_CRUCIBLE = {}
SUBJECT_TOPICS_CACHE = {}

class Crucible:
    def __init__(self, chat_id, is_test=False):
        self.chat_id = chat_id
        self.is_test = is_test
        self.total_qs = 3 if is_test else 100
        self.questions = []
        self.current_q = 0
        self.active_q_index = 0 # BUG FIX: Track the exact question being answered
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

        # Fetch questions
        if self.is_test:
            self.questions = await fetch_questions(subject=subject, topic=topic_slug, limit=self.total_qs)
            if not self.questions:
                self.questions = await fetch_questions(subject=subject, limit=self.total_qs)
        else:
            self.questions = await fetch_bulk_questions(subject=subject, topic=topic_slug, total_needed=self.total_qs)
            if not self.questions:
                print(f"⚠️ Bulk API failed for topic '{topic_slug}'. Falling back to general {subject}.")
                self.questions = await fetch_bulk_questions(subject=subject, total_needed=self.total_qs)

        if not self.questions:
            await context.bot.send_message(self.chat_id, "❌ Failed to load questions. Check Railway logs.")
            return await self.end_crucible(context)

        self.total_qs = len(self.questions)
        await context.bot.send_message(self.chat_id, f"✅ {self.total_qs} questions loaded. Starting in 3 seconds...")
        await asyncio.sleep(3)
        await self.send_next_poll(context)

    async def send_next_poll(self, context):
        if self.current_q >= self.total_qs:
            return await self.end_crucible(context)

        self.active_q_index = self.current_q # Lock the index for this question
        q = self.questions[self.current_q]
        options = [q["options"]["A"], q["options"]["B"], q["options"]["C"], q["options"]["D"]]
        correct_idx = ["A", "B", "C", "D"].index(q["correct"])
        
        self.q_start_time = datetime.datetime.now()

        try:
            if q.get("imageUrl"):
                try:
                    await context.bot.send_photo(chat_id=self.chat_id, photo=q["imageUrl"], caption=f"📸 Image for Q{self.current_q + 1}")
                    await asyncio.sleep(1)
                except: pass

            msg = await context.bot.send_poll(
                chat_id=self.chat_id,
                question=f"Q{self.current_q + 1}/{self.total_qs}: {q['question']}",
                options=options,
                type=Poll.QUIZ,
                correct_option_id=correct_idx,
                is_anonymous=False,
                open_period=self.timer,
                explanation=f"The correct answer is {q['correct']}."
            )
            self.poll_id = msg.poll.id
        except Exception as e:
            logger.error(f"Network error: {e}")
            await asyncio.sleep(3)
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

        # BUG FIX: Use active_q_index instead of current_q
        q = self.questions[self.active_q_index]
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
            context.user_data['wiz_topic_slug'] = topic_name.lower().replace(" ", "-")

        # Step 3: Select Timer (Defaults to 30s for Chemistry)
        default_s = "30" if subject == "chemistry" else "25"
        keyboard = [
            [InlineKeyboardButton("15s", callback_data="wiz_time_15"), InlineKeyboardButton("20s", callback_data="wiz_time_20")],
            [InlineKeyboardButton("25s", callback_data="wiz_time_25"), InlineKeyboardButton(f"{default_s}s (Recommended)", callback_data=f"wiz_time_{default_s}")],
            [InlineKeyboardButton("40s", callback_data="wiz_time_40")]
        ]
        await query.edit_message_text(f"Subject: {subject.title()}\nTopic: {context.user_data['wiz_topic_name']}\n\nStep 3: Select the timer:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("wiz_time_"):
        timer = int(data.replace("wiz_time_", ""))
        
        subj = context.user_data.get('wiz_subject')
        slug = context.user_data.get('wiz_topic_slug')
        name = context.user_data.get('wiz_topic_name')
        
        db_manager.set_setting("current_subject", subj)
        db_manager.set_setting("current_topic", slug)
        db_manager.set_setting("current_topic_name", name)
        db_manager.set_setting("crucible_timer", str(timer))
        
        # Calculate Start and End Times
        start_time = datetime.datetime.now()
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
            return await query.edit_message_text(
                "❌ <b>Error:</b> The Arena group is not set!\n\n"
                "Please go to your group and type <code>/setarena</code> first, then run <code>/settopic</code> again.",
                parse_mode="HTML"
            )
            
        try:
            await context.bot.send_message(int(arena_id), msg, parse_mode="HTML")
            await query.edit_message_text(
                f"✅ <b>Configuration Saved!</b>\n\nThe bot has successfully notified the Arena.\n\n"
                f"⚔️ When you and your opponent are ready, type <code>/startcrucible</code> in the group to begin!",
                parse_mode="HTML"
            )
        except Exception as e:
            await query.edit_message_text(
                f"❌ <b>Failed to send message to the group.</b>\n"
                f"Make sure I am an Admin in the group.\nError: {e}",
                parse_mode="HTML"
            )
        
        # NO AUTO-START. The Admin must use /startcrucible.

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
