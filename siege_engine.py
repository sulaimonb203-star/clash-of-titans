import asyncio
import logging
import datetime
from telegram import Update, Poll
from telegram.ext import ContextTypes
from aloc_manager import fetch_questions
import db_manager
import telegram_rich

logger = logging.getLogger(__name__)
ACTIVE_SIEGE = {}

TRACK_1 = ["biology", "physics", "english", "chemistry"]
TRACK_2 = ["mathematics", "physics", "english", "chemistry"]

class Siege:
    def __init__(self, chat_id, is_test=False):
        self.chat_id = chat_id
        self.is_test = is_test
        self.total_qs = 4 if is_test else 180
        self.questions = []
        self.current_q = 0
        self.poll_id = None
        self.stats = {} 
        self.q_start_time = None

    async def start(self, context):
        title = "🧪 *TEST SIEGE* 🧪" if self.is_test else "🏰 *THE WEEKLY SIEGE HAS BEGUN!* 🏰"
        await context.bot.send_message(self.chat_id, f"{title}\n\nFetching {self.total_qs} questions across all tracks...", parse_mode="Markdown")
        
        for i in range(self.total_qs):
            subject = TRACK_1[i % len(TRACK_1)] if i % 2 == 0 else TRACK_2[i % len(TRACK_2)]
            batch = await fetch_questions(subject=subject, limit=1)
            if batch:
                self.questions.append(batch[0])
            await asyncio.sleep(0.5)
            
        if not self.questions:
            return await context.bot.send_message(self.chat_id, "❌ Failed to load questions.")
            
        self.total_qs = len(self.questions)
        await context.bot.send_message(self.chat_id, f"✅ {self.total_qs} questions loaded. Starting in 3 seconds...", parse_mode="Markdown")
        await asyncio.sleep(3)
        await self.send_next_poll(context)

    async def send_next_poll(self, context):
        if self.current_q >= self.total_qs:
            return await self.end_siege(context)
            
        q = self.questions[self.current_q]
        options = [q["options"]["A"], q["options"]["B"], q["options"]["C"], q["options"]["D"]]
        correct_idx = ["A", "B", "C", "D"].index(q["correct"])
        
        self.q_start_time = datetime.datetime.now()
        
        try:
            # Send image if it exists
            if q.get("imageUrl"):
                await context.bot.send_photo(
                    chat_id=self.chat_id,
                    photo=q["imageUrl"],
                    caption=f"📸 Image for Q{self.current_q + 1}"
                )
                await asyncio.sleep(1)

            msg = await context.bot.send_poll(
                chat_id=self.chat_id,
                question=f"Q{self.current_q + 1}/{self.total_qs} ({q['subject'].title()}): {q['question']}",
                options=options,
                type=Poll.QUIZ,
                correct_option_id=correct_idx,
                is_anonymous=False,
                open_period=25,
                explanation=f"The correct answer is {q['correct']}."
            )
            self.poll_id = msg.poll.id
        except Exception as e:
            logger.error(f"Network error sending poll: {e}. Retrying...")
            await asyncio.sleep(3)
            return await self.send_next_poll(context)
            
        await asyncio.sleep(25) 
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

    async def end_siege(self, context):
        if not self.stats:
            if self.chat_id in ACTIVE_SIEGE: del ACTIVE_SIEGE[self.chat_id]
            return await context.bot.send_message(self.chat_id, "🏁 *SIEGE ENDED!* No one participated.", parse_mode="Markdown")

        final_stats = {}
        for uid, data in self.stats.items():
            missed = self.total_qs - data['answered']
            percent = (data['score'] / self.total_qs) * 100
            real_score = (data['score'] / self.total_qs) * 400
            aura_bonus = int(data['score'] * 4)
            mins = int(data['total_time'] // 60)
            secs = int(data['total_time'] % 60)
            
            final_stats[uid] = {
                'name': data['name'], 'score': data['score'], 'answered': data['answered'],
                'missed': missed, 'time_str': f"{mins}m{secs}s", 'percent': percent, 
                'real_score': int(real_score), 'aura': aura_bonus
            }
            db_manager.update_aura(uid, aura_bonus, "Siege Participation")

        md_table = telegram_rich.generate_native_table(final_stats, title="🏆 THE SIEGE RESULTS 🏆")
        fallback = f"THE SIEGE RESULTS\n"
        
        await telegram_rich.send_native_table(context.bot, self.chat_id, md_table, fallback)
            
        if self.chat_id in ACTIVE_SIEGE: del ACTIVE_SIEGE[self.chat_id]

async def test_siege(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ACTIVE_SIEGE[chat_id] = Siege(chat_id, is_test=True)
    asyncio.create_task(ACTIVE_SIEGE[chat_id].start(context))

async def route_siege_answers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    if not db_manager.has_valid_pass(answer.user.id): return
    for siege in ACTIVE_SIEGE.values():
        if siege.poll_id == answer.poll_id:
            await siege.handle_answer(answer)
            break
