import asyncio
import logging
import datetime
from telegram import Update, Poll
from telegram.ext import ContextTypes
from aloc_manager import fetch_questions
import db_manager
import telegram_rich

logger = logging.getLogger(__name__)
ACTIVE_MELEE = {}

class Melee:
    def __init__(self, chat_id, is_test=False):
        self.chat_id = chat_id
        self.is_test = is_test
        self.stage = 1
        self.questions = []
        self.current_q = 0
        self.poll_id = None
        self.stats = {}
        self.q_start_time = None
        self.total_qs_per_stage = 3 if is_test else 30

    async def start(self, context):
        await context.bot.send_message(self.chat_id, f"🏟️ *THE GRAND MELEE HAS BEGUN!* 🏟️\n\nStage 1: {self.total_qs_per_stage} Questions. Top 50% advance!", parse_mode="Markdown")
        self.questions = await fetch_questions(subject="physics", limit=self.total_qs_per_stage)
        
        if not self.questions:
            del ACTIVE_MELEE[self.chat_id]
            return await context.bot.send_message(self.chat_id, "❌ Failed to load questions.")
            
        await asyncio.sleep(3)
        await self.send_next_poll(context)

    async def send_next_poll(self, context):
        if self.current_q >= len(self.questions):
            return await self.end_stage(context)
            
        q = self.questions[self.current_q]
        options = [q["options"]["A"], q["options"]["B"], q["options"]["C"], q["options"]["D"]]
        correct_idx = ["A", "B", "C", "D"].index(q["correct"])
        
        self.q_start_time = datetime.datetime.now()
        
        try:
            msg = await context.bot.send_poll(
                chat_id=self.chat_id,
                question=f"Stage {self.stage} | Q{self.current_q + 1}/{len(self.questions)}: {q['question']}",
                options=options,
                type=Poll.QUIZ,
                correct_option_id=correct_idx,
                is_anonymous=False,
                open_period=20,
                explanation=f"The correct answer is {q['correct']}."
            )
            self.poll_id = msg.poll.id
        except Exception as e:
            await asyncio.sleep(3)
            return await self.send_next_poll(context)
            
        await asyncio.sleep(20)
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

    async def end_stage(self, context):
        if not self.stats:
            del ACTIVE_MELEE[self.chat_id]
            return await context.bot.send_message(self.chat_id, "🏁 *MELEE ENDED!* No participants.", parse_mode="Markdown")

        final_stats = {}
        for uid, data in self.stats.items():
            missed = len(self.questions) - data['answered']
            percent = (data['score'] / len(self.questions)) * 100
            mins = int(data['total_time'] // 60)
            secs = int(data['total_time'] % 60)
            final_stats[uid] = {
                'name': data['name'], 'score': data['score'], 'answered': data['answered'],
                'missed': missed, 'time_str': f"{mins}m{secs}s", 'percent': percent, 
                'real_score': int(percent * 4), 'aura': data['score'] * 5
            }

        md_table = telegram_rich.generate_native_table(final_stats, title=f"🏟️ MELEE STAGE {self.stage} RESULTS 🏟️")
        await telegram_rich.send_native_table(context.bot, self.chat_id, md_table, "Stage Results")
        
        # In a full build, we would filter top 50% and start Stage 2. 
        # For this version, the tournament ends after one stage to ensure stability.
        await context.bot.send_message(self.chat_id, "🏆 *The Melee concludes!* Champions have been crowned. Check the table above.", parse_mode="Markdown")
        
        if self.chat_id in ACTIVE_MELEE: del ACTIVE_MELEE[self.chat_id]

async def test_melee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ACTIVE_MELEE[chat_id] = Melee(chat_id, is_test=True)
    asyncio.create_task(ACTIVE_MELEE[chat_id].start(context))

async def route_melee_answers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    if not db_manager.has_valid_pass(answer.user.id): return
    for melee in ACTIVE_MELEE.values():
        if melee.poll_id == answer.poll_id:
            await melee.handle_answer(answer)
            break
