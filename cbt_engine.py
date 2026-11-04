import asyncio
import logging
import json
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Poll, WebAppInfo
from telegram.ext import ContextTypes
from aloc_manager import fetch_questions
import telegram_rich
import db_manager
from ai_engine import get_explanation_async
import ai_engine

logger = logging.getLogger(__name__)
ACTIVE_CBT = {}
CBT_TOPICS_CACHE = {}
CALCULATOR_URL = "https://web-production-038a36.up.railway.app/calculator"

class CBT:
    def __init__(self, chat_id, config):
        self.chat_id = chat_id
        self.config = config
        self.questions = []
        self.current_q = 0
        self.poll_id = None
        self.score = 0
        self.answered = 0
        self.wrong_qs = []
        self.answer_event = asyncio.Event()

    async def start(self, context):
        mode = self.config.get('mode', 'mock').title()
        subjs = ', '.join(self.config['subjects']).title()
        await context.bot.send_message(self.chat_id, f"📝 <b>EXAM STARTING</b>\nMode: {mode}\nSubjects: {subjs}\nQuestions: {self.config['total_qs']}\nTime per Q: {self.config['timer']}s", parse_mode="HTML")
        
        # Fetch questions for each subject
        seen_ids = set()
        for subj, details in self.config['subject_config'].items():
            topic_slug = details.get('topic_slug') if details.get('topic_slug') != 'general' else None
            year = details.get('year')
            q_count = details.get('q_count', 10)
            
            # 1. Exact Match (Subject + Topic + Year)
            batch = await fetch_questions(subject=subj, topic=topic_slug, year=year, limit=15)
            
            # 2. Fallback: Ignore Year (Subject + Topic)
            if not batch and topic_slug:
                print(f"⚠️ API failed for {subj} {year}. Trying without year...")
                batch = await fetch_questions(subject=subj, topic=topic_slug, limit=15)
                
            # 3. Fallback: Ignore Topic (Subject only)
            if not batch:
                print(f"⚠️ API failed for {subj} {topic_slug}. Falling back to General {subj}...")
                batch = await fetch_questions(subject=subj, limit=15)
                
            # 4. Ultimate Fallback: Fetch any random questions
            if not batch:
                print(f"🚨 ALOC API completely down. Fetching general questions...")
                batch = await fetch_questions(limit=15)
                
            for q in batch[:q_count]:
                if q['id'] not in seen_ids:
                    # Shuffle options if enabled
                    if self.config.get('shuffle_opts'):
                        opts_items = list(q['options'].items())
                        random.shuffle(opts_items)
                        old_correct = q['options'][q['correct']]
                        q['options'] = {chr(65+i): v for i, (k, v) in enumerate(opts_items)}
                        # Find new correct letter
                        for new_k, new_v in q['options'].items():
                            if new_v == old_correct:
                                q['correct'] = new_k
                                break
                    self.questions.append(q)
                    seen_ids.add(q['id'])
                    
        if self.config.get('shuffle_qs'):
            random.shuffle(self.questions)
            
        self.questions = self.questions[:self.config['total_qs']]
        
        if not self.questions:
            del ACTIVE_CBT[self.chat_id]
            return await context.bot.send_message(self.chat_id, "❌ Failed to load questions. Try different subjects.")

        self.config['total_qs'] = len(self.questions)
        await asyncio.sleep(2)
        await self.send_next_poll(context)

    async def send_next_poll(self, context):
        if self.current_q >= self.config['total_qs']:
            return await self.end_cbt(context)
            
        q = self.questions[self.current_q]
        options = [q["options"]["A"], q["options"]["B"], q["options"]["C"], q["options"]["D"]]
        correct_idx = ["A", "B", "C", "D"].index(q["correct"])
        
        try:
            if q.get("imageUrl"):
                try:
                    await context.bot.send_photo(chat_id=self.chat_id, photo=q["imageUrl"], caption=f"📸 Image for Q{self.current_q + 1}")
                    await asyncio.sleep(1)
                except: pass

            calc_btn = InlineKeyboardButton(text="🧮 Calculator", web_app=WebAppInfo(url=CALCULATOR_URL))
            calc_keyboard = InlineKeyboardMarkup([[calc_btn]])

            self.answer_event.clear()
            
            mode = self.config.get('mode', 'mock')
            
            # Practice Mode: Fetch REAL AI explanation instantly
            if mode == "practice":
                # We await this before sending the poll so it's ready instantly
                explanation = await get_explanation_async(q['question'], q['correct'], q['options'])
            else:
                explanation = None

            msg = await context.bot.send_poll(
                chat_id=self.chat_id,
                question=f"Q{self.current_q + 1}/{self.config['total_qs']} [{q.get('subject', '').title()}]: {q['question']}",
                options=options,
                type=Poll.QUIZ,
                correct_option_id=correct_idx,
                is_anonymous=False,
                open_period=self.config['timer'],
                explanation=explanation,
                reply_markup=calc_keyboard
            )
            self.poll_id = msg.poll.id
            
            try:
                await asyncio.wait_for(self.answer_event.wait(), timeout=self.config['timer'])
            except asyncio.TimeoutError:
                pass 
                
        except Exception as e:
            logger.error(f"CBT error: {e}")
            await asyncio.sleep(3)
            return await self.send_next_poll(context)
            
        self.current_q += 1
        await self.send_next_poll(context)

    async def handle_answer(self, answer):
        if answer.poll_id != self.poll_id: return
        self.answered += 1
        q = self.questions[self.current_q]
        correct_idx = ["A", "B", "C", "D"].index(q["correct"])
        
        if answer.option_ids[0] == correct_idx:
            self.score += 1
        else:
            self.wrong_qs.append(q)
            
        self.answer_event.set()

    async def end_cbt(self, context):
        percent = (self.score / self.config['total_qs']) * 100
        stats = {1: {'name': "You", 'score': self.score, 'answered': self.answered, 'missed': self.config['total_qs'] - self.answered, 'time_str': "--", 'percent': percent, 'real_score': int(percent * 4), 'aura': 0}}
        md_table = telegram_rich.generate_native_table(stats, title="📝 CBT RESULTS 📝")
        await telegram_rich.send_native_table(context.bot, self.chat_id, md_table, "CBT Finished")
        
        # Mock Mode: Show AI explanations for wrong answers
        if self.config.get('mode') == 'mock' and self.wrong_qs:
            await context.bot.send_message(self.chat_id, "🤖 <b>AI TUTOR (Mock Mode Explanations)</b>\nHere are the questions you missed:", parse_mode="HTML")
            for q in self.wrong_qs[:5]: # Limit to 5 to prevent spam
                exp = ai_engine.get_explanation(q['question'], q['correct'], q['options'])
                await context.bot.send_message(self.chat_id, f"❓ <b>Q:</b> {q['question']}\n✅ <b>Correct:</b> {q['correct']}\n💡 <b>Tutor:</b> {exp}", parse_mode="HTML")
                await asyncio.sleep(1)

        db_manager.update_streak(self.chat_id)
        if self.chat_id in ACTIVE_CBT: del ACTIVE_CBT[self.chat_id]

# --- WIZARD FLOW ---
async def start_cbt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db_manager.has_valid_pass(user_id):
        msg = "🚫 You need an active Titan Pass to use CBT."
        return await (update.callback_query.message.reply_text(msg) if update.callback_query else update.message.reply_text(msg))
        
    if user_id in ACTIVE_CBT: return

    context.user_data.clear()
    context.user_data['subjects'] = []
    context.user_data['subject_config'] = {}
    context.user_data['mode'] = 'mock'
    context.user_data['shuffle_qs'] = True
    context.user_data['shuffle_opts'] = True
    
    keyboard = [
        [InlineKeyboardButton("Practice (Instant Answers)", callback_data="cbt_mode_practice")],
        [InlineKeyboardButton("Mock (Results Only)", callback_data="cbt_mode_mock")]
    ]
    text = "🛠️ <b>CBT SETUP</b> 🛠️\n\n<b>Step 1: Test Mode</b>"
    await (update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML") if update.callback_query else update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"))

async def cbt_config_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    subjects = context.user_data.get('subjects', [])
    
    if data.startswith("cbt_mode_"):
        context.user_data['mode'] = data.replace("cbt_mode_", "")
        keyboard = [
            [InlineKeyboardButton("Physics", callback_data="cbt_tgl_physics"), InlineKeyboardButton("Math", callback_data="cbt_tgl_mathematics")],
            [InlineKeyboardButton("Biology", callback_data="cbt_tgl_biology"), InlineKeyboardButton("Chemistry", callback_data="cbt_tgl_chemistry")],
            [InlineKeyboardButton("English", callback_data="cbt_tgl_english")],
            [InlineKeyboardButton("✅ Done Selecting", callback_data="cbt_done_subjects")]
        ]
        await query.edit_message_text("🛠️ <b>Step 2: Select Subjects</b> (Max 4)", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        
    elif data.startswith("cbt_tgl_"):
        subj = data.replace("cbt_tgl_", "")
        if subj in subjects: subjects.remove(subj)
        elif len(subjects) >= 4: return await query.answer("Max 4 subjects!", show_alert=True)
        else: subjects.append(subj)
        
        context.user_data['subjects'] = subjects
        keyboard = [
            [InlineKeyboardButton(f"{'✅ ' if 'physics' in subjects else ''}Physics", callback_data="cbt_tgl_physics"), InlineKeyboardButton(f"{'✅ ' if 'mathematics' in subjects else ''}Math", callback_data="cbt_tgl_mathematics")],
            [InlineKeyboardButton(f"{'✅ ' if 'biology' in subjects else ''}Biology", callback_data="cbt_tgl_biology"), InlineKeyboardButton(f"{'✅ ' if 'chemistry' in subjects else ''}Chemistry", callback_data="cbt_tgl_chemistry")],
            [InlineKeyboardButton(f"{'✅ ' if 'english' in subjects else ''}English", callback_data="cbt_tgl_english")],
            [InlineKeyboardButton("✅ Done Selecting", callback_data="cbt_done_subjects")]
        ]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        
    elif data == "cbt_done_subjects":
        if not subjects: return await query.answer("Select at least one subject!", show_alert=True)
        # Start configuring the first subject
        await setup_subject(query, subjects[0], context)

    elif data.startswith("cbt_year_"):
        yr = data.replace("cbt_year_", "")
        current_subj = context.user_data.get('configuring_subj')
        if 'subject_config' not in context.user_data: context.user_data['subject_config'] = {}
        context.user_data['subject_config'].setdefault(current_subj, {})['year'] = None if yr == "any" else yr
        
        # Select Q count
        keyboard = [[InlineKeyboardButton("10", callback_data="cbt_qc_10"), InlineKeyboardButton("20", callback_data="cbt_qc_20"), InlineKeyboardButton("40", callback_data="cbt_qc_40")]]
        await query.edit_message_text(f"🛠️ <b>{current_subj.title()}</b>\nStep 2: Questions", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data.startswith("cbt_qc_"):
        qc = int(data.replace("cbt_qc_", ""))
        current_subj = context.user_data.get('configuring_subj')
        context.user_data['subject_config'][current_subj]['q_count'] = qc
        
        # Select Topic
        try:
            with open("syllabus.json", "r") as f:
                syllabus = json.load(f)
                topics = syllabus.get(current_subj, [])
        except: topics = []
        
        CBT_TOPICS_CACHE[query.from_user.id] = topics
        keyboard = [[InlineKeyboardButton("🎲 General (All)", callback_data=f"cbt_tpc_{current_subj}_-1")]]
        for i, t in enumerate(topics[:15]): # Limit to 15 to fit
            display = t[:50] + "..." if len(t) > 50 else t
            keyboard.append([InlineKeyboardButton(display, callback_data=f"cbt_tpc_{current_subj}_{i}")])
            
        await query.edit_message_text(f"🛠️ <b>{current_subj.title()}</b>\nStep 3: Topic", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data.startswith("cbt_tpc_"):
        parts = data.split("_")
        subj = parts[2]
        index = parts[3]
        
        if index == "-1":
            topic_name = "General"
            topic_slug = "general"
        else:
            topics = CBT_TOPICS_CACHE.get(query.from_user.id, [])
            topic_name = topics[int(index)]
            topic_slug = topic_name.lower().replace(" ", "-")
            
        context.user_data['subject_config'][subj]['topic_slug'] = topic_slug
        context.user_data['subject_config'][subj]['topic_name'] = topic_name
        
        # Move to next subject or global settings
        subjects = context.user_data['subjects']
        configured = list(context.user_data['subject_config'].keys())
        
        remaining = [s for s in subjects if s not in configured]
        if remaining:
            await setup_subject(query, remaining[0], context)
        else:
            # Global settings
            keyboard = [
                [InlineKeyboardButton("30s", callback_data="cbt_t_30"), InlineKeyboardButton("60s", callback_data="cbt_t_60")],
                [InlineKeyboardButton("90s", callback_data="cbt_t_90"), InlineKeyboardButton("120s", callback_data="cbt_t_120")]
            ]
            await query.edit_message_text("🛠️ <b>Global Settings: Time per Question</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data.startswith("cbt_t_"):
        timer = int(data.replace("cbt_t_", ""))
        context.user_data['timer'] = timer
        
        # Calculate total questions
        total = sum(c.get('q_count', 10) for c in context.user_data['subject_config'].values())
        context.user_data['total_qs'] = total
        
        # Launch
        config = {
            'mode': context.user_data['mode'],
            'subjects': context.user_data['subjects'],
            'subject_config': context.user_data['subject_config'],
            'total_qs': total,
            'timer': timer,
            'shuffle_qs': context.user_data['shuffle_qs'],
            'shuffle_opts': context.user_data['shuffle_opts']
        }
        
        await query.edit_message_text(f"🚀 <b>Launching {total}-Question Exam...</b>\nMode: {config['mode'].title()}", parse_mode="HTML")
        ACTIVE_CBT[query.from_user.id] = CBT(query.from_user.id, config)
        asyncio.create_task(ACTIVE_CBT[query.from_user.id].start(context))

async def setup_subject(query, subj, context):
    context.user_data['configuring_subj'] = subj
    keyboard = [
        [InlineKeyboardButton("Any Year", callback_data="cbt_year_any")],
        [InlineKeyboardButton("2024", callback_data="cbt_year_2024"), InlineKeyboardButton("2023", callback_data="cbt_year_2023")],
        [InlineKeyboardButton("2022", callback_data="cbt_year_2022"), InlineKeyboardButton("2021", callback_data="cbt_year_2021")]
    ]
    await query.edit_message_text(f"🛠️ <b>{subj.title()}</b>\nStep 1: Select Year", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def start_blitz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db_manager.has_valid_pass(user_id): return
    config = {
        'mode': 'mock', 'subjects': ['physics'], 'total_qs': 10, 'timer': 5, 
        'shuffle_qs': True, 'shuffle_opts': True,
        'subject_config': {'physics': {'year': None, 'q_count': 10, 'topic_slug': 'general'}}
    }
    ACTIVE_CBT[user_id] = CBT(user_id, config)
    asyncio.create_task(ACTIVE_CBT[user_id].start(context))

async def route_cbt_answers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    if answer.user.id in ACTIVE_CBT:
        if not db_manager.has_valid_pass(answer.user.id): return
        await ACTIVE_CBT[answer.user.id].handle_answer(answer)
