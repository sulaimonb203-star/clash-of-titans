import asyncio
import logging
import datetime
import pytz
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
        msg = title + chr(10) + chr(10) + "Subject: " + subject.title() + chr(10) + "Topic: " + topic_name + chr(10) + "Timer: " + str(self.timer) + "s" + chr(10) + "Forging " + str(self.total_qs) + " questions..."
        await context.bot.send_message(self.chat_id, msg, parse_mode="Markdown")

        if self.is_test:
            qs_list, _ = await fetch_questions(subject=subject, topic=topic_slug, limit=self.total_qs)
            self.questions = qs_list
        else:
            self.questions = await fetch_bulk_questions(subject=subject, topic=topic_slug, total_needed=self.total_qs)

        if not self.questions:
            await context.bot.send_message(self.chat_id, "⚠️ No fresh questions found for this topic (14-day cooldown). Falling back to General.", parse_mode="HTML")
            self.questions = await fetch_bulk_questions(subject=subject, total_needed=self.total_qs)
            
        if not self.questions:
            await context.bot.send_message(self.chat_id, "❌ ALOC API has no fresh questions. Please try again later.")
            return await self.end_crucible(context)

        self.total_qs = len(self.questions)
        await context.bot.send_message(self.chat_id, "✅ " + str(self.total_qs) + " questions loaded. Starting in 3 seconds...")
        await asyncio.sleep(3)
        await self.send_next_poll(context)

    async def send_next_poll(self, context):
        if self.current_q >= self.total_qs:
            return await self.end_crucible(context)

        q = self.questions[self.current_q]
        options = [str(q["options"]["A"])[:100], str(q["options"]["B"])[:100], str(q["options"]["C"])[:100], str(q["options"]["D"])[:100]]
        correct_idx = ["A", "B", "C", "D"].index(q["correct"])
        question_text = ("Q" + str(self.current_q + 1) + "/" + str(self.total_qs) + ": " + q['question'])[:300]
        
        self.q_start_time = datetime.datetime.now()

        try:
            if q.get("imageUrl"):
                try:
                    await context.bot.send_photo(chat_id=self.chat_id, photo=q["imageUrl"], caption="📸 Image for Q" + str(self.current_q + 1))
                    await asyncio.sleep(1)
                except: pass

            ai_explanation = (await get_explanation_async(q['question'], q['correct'], q['options']))[:195]

            # Attach Calculator Button
            calc_btn = InlineKeyboardButton(text="🧮 Calculator", web_app=WebAppInfo(url=CALCULATOR_URL))
            calc_keyboard = InlineKeyboardMarkup([[calc_btn]])

            try:
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
                print(f"🚨 CALCULATOR BUTTON REJECTED BY TELEGRAM: {e1}")
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
            logger.error("🚨 CRUCIAL POLL ERROR: " + str(e))
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
                'missed': missed, 'time_str': str(mins) + "m" + str(secs) + "s", 'percent': percent,
                'real_score': int(real_score), 'aura': aura_bonus
            }
            db_manager.update_aura(uid, aura_bonus, "Crucible Participation")
            db_manager.update_streak(uid)

        md_table = telegram_rich.generate_native_table(final_stats, title="🏆 THE CRUCIBLE RESULTS 🏆")
        await telegram_rich.send_native_table(context.bot, self.chat_id, md_table, "CRUCIBLE RESULTS")
        if self.chat_id in ACTIVE_CRUCIBLE: del ACTIVE_CRUCIBLE[self.chat_id]

async def stop_crucible_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != admin_engine.ADMIN_ID: return
    chat_id = update.effective_chat.id
    if chat_id in ACTIVE_CRUCIBLE:
        del ACTIVE_CRUCIBLE[chat_id]
        await update.message.reply_text("🛑 *CRUCIBLE CANCELLED BY THE ARBITER.*", parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ No active Crucible found in this chat.")

async def set_topic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != admin_engine.ADMIN_ID: return
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
        await query.edit_message_text("🔍 Fetching official " + subject.title() + " topics from ALOC...", parse_mode="HTML")
        
        api_topics = await fetch_topics(subject)
        if not api_topics: return await query.edit_message_text("❌ ALOC API returned 0 topics.")
        topics = [t.get('displayName', t.get('name')) for t in api_topics]

        SUBJECT_TOPICS_CACHE[subject] = topics
        keyboard = [[InlineKeyboardButton("🎲 General (All)", callback_data="wiz_top_" + subject + "_-1")]]
        for i, t in enumerate(topics):
            keyboard.append([InlineKeyboardButton(t[:60], callback_data="wiz_top_" + subject + "_" + str(i))])
            
        await query.edit_message_text("Step 2: Select a topic for <b>" + subject.title() + "</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data.startswith("wiz_top_"):
        parts = data.split("_")
        subject, index = parts[2], parts[3]
        context.user_data['wiz_subject'] = subject
        
        if index == "-1":
            context.user_data['wiz_topic_slug'] = "general"
            context.user_data['wiz_topic_name'] = "General (All Topics)"
        else:
            topic_name = SUBJECT_TOPICS_CACHE.get(subject, [])[int(index)]
            context.user_data['wiz_topic_name'] = topic_name
            api_topics = await fetch_topics(subject)
            for t in api_topics:
                if t.get('displayName') == topic_name: context.user_data['wiz_topic_slug'] = t.get('name'); break

        default_s = "30" if subject == "chemistry" else "25"
        keyboard = [
            [InlineKeyboardButton("15s", callback_data="wiz_time_15"), InlineKeyboardButton("20s", callback_data="wiz_time_20")],
            [InlineKeyboardButton("25s", callback_data="wiz_time_25"), InlineKeyboardButton(default_s + "s (Rec)", callback_data="wiz_time_" + default_s)],
            [InlineKeyboardButton("40s", callback_data="wiz_time_40")]
        ]
        await query.edit_message_text("Subject: " + subject.title() + "\nTopic: " + context.user_data['wiz_topic_name'] + "\n\nStep 3: Select the timer:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("wiz_time_"):
        timer = int(data.replace("wiz_time_", ""))
        context.user_data['wiz_timer'] = timer
        keyboard = [[InlineKeyboardButton("🚀 Start Now", callback_data="wiz_start_0")], [InlineKeyboardButton("⏳ In 15 Mins", callback_data="wiz_start_15"), InlineKeyboardButton("⏳ In 30 Mins", callback_data="wiz_start_30")]]
        await query.edit_message_text("Step 4: When should the Crucible start?", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("wiz_start_"):
        delay_mins = int(data.replace("wiz_start_", ""))
        timer = context.user_data.get('wiz_timer', 25)
        subj = context.user_data.get('wiz_subject', 'chemistry')
        slug = context.user_data.get('wiz_topic_slug', 'general')
        name = context.user_data.get('wiz_topic_name', 'General')
        
        db_manager.set_setting("current_subject", subj)
        db_manager.set_setting("current_topic", slug)
        db_manager.set_setting("current_topic_name", name)
        db_manager.set_setting("crucible_timer", str(timer))
        
        lagos_tz = pytz.timezone('Africa/Lagos')
        start_time = datetime.datetime.now(lagos_tz) + datetime.timedelta(minutes=delay_mins)
        end_time = start_time + datetime.timedelta(seconds=100 * timer) 
        
        arena_id = db_manager.get_setting("arena_chat_id")
        msg = f"🚨 <b>NEW CRUCIBLE CONFIGURED</b> 🚨\n\n📚 <b>Subject:</b> {subj.title()}\n🎯 <b>Topic:</b> {name}\n⏱️ <b>Timer:</b> {timer}s\n📝 <b>Qs:</b> 100\n\n🕒 <b>Starts:</b> {start_time.strftime('%I:%M %p')}\n🕒 <b>Ends:</b> {end_time.strftime('%I:%M %p')}\n\n<i>Get ready, Titans!</i>"
        
        if not arena_id: return await query.edit_message_text("❌ Arena group not set!")
        await context.bot.send_message(int(arena_id), msg, parse_mode="HTML")
        
        if delay_mins == 0:
            await query.edit_message_text("✅ Saved! Starting right now...", parse_mode="HTML")
            ACTIVE_CRUCIBLE[int(arena_id)] = Crucible(int(arena_id), is_test=False)
            asyncio.create_task(ACTIVE_CRUCIBLE[int(arena_id)].start(context))
        else:
            await query.edit_message_text(f"✅ Saved! Will start at {start_time.strftime('%I:%M %p')}.", parse_mode="HTML")
            async def delayed_start():
                if delay_mins >= 10:
                    await asyncio.sleep((delay_mins - 10) * 60)
                    await context.bot.send_message(int(arena_id), f"⏳ <b>REMINDER:</b> Starts in 10 minutes!", parse_mode="HTML")
                    await asyncio.sleep(5 * 60)
                    await context.bot.send_message(int(arena_id), f"⏳ <b>REMINDER:</b> Starts in 5 minutes!", parse_mode="HTML")
                    await asyncio.sleep(5 * 60)
                else:
                    await asyncio.sleep(delay_mins * 60)
                await context.bot.send_message(int(arena_id), "🚨 <b>THE CRUCIBLE IS STARTING NOW!</b> 🚨", parse_mode="HTML")
                ACTIVE_CRUCIBLE[int(arena_id)] = Crucible(int(arena_id), is_test=False)
                await ACTIVE_CRUCIBLE[int(arena_id)].start(context)
            asyncio.create_task(delayed_start())

async def set_general_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_manager.set_setting("current_subject", "physics")
    db_manager.set_setting("current_topic", "general")
    db_manager.set_setting("current_topic_name", "General")
    await update.message.reply_text("✅ Topic cleared.")

async def test_crucible(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ACTIVE_CRUCIBLE[update.effective_chat.id] = Crucible(update.effective_chat.id, is_test=True)
    asyncio.create_task(ACTIVE_CRUCIBLE[update.effective_chat.id].start(context))

async def start_crucible_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != admin_engine.ADMIN_ID: return
    chat_id = update.effective_chat.id
    if chat_id in ACTIVE_CRUCIBLE: return await update.message.reply_text("⚠️ Already running!")
    ACTIVE_CRUCIBLE[chat_id] = Crucible(chat_id, is_test=False)
    asyncio.create_task(ACTIVE_CRUCIBLE[chat_id].start(context))

async def route_crucible_answers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for crucible in ACTIVE_CRUCIBLE.values():
        if crucible.poll_id == update.poll_answer.poll_id:
            await crucible.handle_answer(update.poll_answer); break
