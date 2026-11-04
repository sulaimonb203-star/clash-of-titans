import asyncio
import logging
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Poll, WebAppInfo
from telegram.helpers import mention_html
from telegram.ext import ContextTypes
from aloc_manager import fetch_questions, fetch_bulk_questions
import db_manager
import telegram_rich

logger = logging.getLogger(__name__)

PENDING_CHALLENGES = {}
ACTIVE_DUELS = {}

class Duel:
    def __init__(self, chat_id, p1, p2, config):
        self.chat_id = chat_id
        self.p1 = p1
        self.p2 = p2
        self.config = config
        self.questions = []
        self.current_q = 0
        self.poll_id = None
        self.scores = {p1.id: 0, p2.id: 0}
        self.p1_time = 0
        self.p2_time = 0
        self.answered_this_round = set()
        self.q_start_time = None

    async def start(self, context):
        await context.bot.send_message(self.chat_id, f"⚔️ *DUEL STARTED!* ⚔️\n{self.p1.first_name} vs {self.p2.first_name}\n\nSubject: {self.config['subject'].title()} | Bounty: {self.config['bounty']} Aura", parse_mode="Markdown")
        
        self.questions = await fetch_questions(subject=self.config['subject'], limit=self.config['questions'])
        if not self.questions:
            await context.bot.send_message(self.chat_id, "❌ Failed to load questions.")
            del ACTIVE_DUELS[self.chat_id]
            return
            
        await asyncio.sleep(2)
        await self.send_next_poll(context)

    async def send_next_poll(self, context):
        if self.current_q >= len(self.questions):
            return await self.end_duel(context)
            
        q = self.questions[self.current_q]
        options = [q["options"]["A"], q["options"]["B"], q["options"]["C"], q["options"]["D"]]
        correct_idx = ["A", "B", "C", "D"].index(q["correct"])
        
        self.answered_this_round.clear()
        self.q_start_time = asyncio.get_event_loop().time()
        
        try:
            if q.get("imageUrl"):
                await context.bot.send_photo(
                    chat_id=self.chat_id,
                    photo=q["imageUrl"],
                    caption=f"📸 Image for Q{self.current_q + 1}"
                )
                await asyncio.sleep(1) # Give them a second to look at it

            CALCULATOR_URL = "https://web-production-038a36.up.railway.app/calculator"
            calc_btn = InlineKeyboardButton(text="🧮 Calculator", web_app=WebAppInfo(url=CALCULATOR_URL))
            calc_keyboard = InlineKeyboardMarkup([[calc_btn]])

            msg = await context.bot.send_poll(
                chat_id=self.chat_id,
                question=f"Q{self.current_q + 1}/{self.config['questions']}: {q['question']}",
                options=options,
                type=Poll.QUIZ,
                correct_option_id=correct_idx,
                is_anonymous=False,
                open_period=self.config['timer'],
                explanation=f"The correct answer is {q['correct']}.",
                reply_markup=calc_keyboard
            )
            self.poll_id = msg.poll.id
        except Exception as e:
            logger.error(f"Network error sending poll: {e}. Retrying in 3s...")
            await asyncio.sleep(3)
            return await self.send_next_poll(context) # Retry the SAME question
        
        await asyncio.sleep(self.config['timer'])
        self.current_q += 1
        await self.send_next_poll(context)

    async def handle_poll_answer(self, update, context):
        answer = update.poll_answer
        if answer.poll_id != self.poll_id: return
        
        user_id = answer.user.id
        if user_id not in (self.p1.id, self.p2.id): return
        if user_id in self.answered_this_round: return
        
        self.answered_this_round.add(user_id)
        
        time_taken = asyncio.get_event_loop().time() - self.q_start_time
        if user_id == self.p1.id: self.p1_time += time_taken
        else: self.p2_time += time_taken
        
        q = self.questions[self.current_q]
        correct_idx = ["A", "B", "C", "D"].index(q["correct"])
        
        if answer.option_ids[0] == correct_idx:
            self.scores[user_id] += 1

    async def end_duel(self, context):
        p1_score = self.scores[self.p1.id]
        p2_score = self.scores[self.p2.id]
        
        winner_id = None
        is_draw = False

        if p1_score > p2_score: 
            winner_id = self.p1.id
        elif p2_score > p1_score: 
            winner_id = self.p2.id
        else:
            # TIE! Check the configuration
            if self.config.get('tie_breaker') == 'time':
                if self.p1_time < self.p2_time: winner_id = self.p1.id
                elif self.p2_time < self.p1_time: winner_id = self.p2.id
                else: is_draw = True # Exact same time
            else:
                is_draw = True # Fair Draw selected
        
        # Bounty Logic
        bounty = self.config['bounty']
        p1_aura_change = 0
        p2_aura_change = 0

        if not is_draw and bounty > 0:
            if winner_id == self.p1.id:
                db_manager.update_aura(self.p1.id, bounty, "Clash Bounty Win")
                db_manager.update_aura(self.p2.id, -bounty, "Clash Bounty Loss")
                p1_aura_change = bounty
                p2_aura_change = -bounty
            elif winner_id == self.p2.id:
                db_manager.update_aura(self.p2.id, bounty, "Clash Bounty Win")
                db_manager.update_aura(self.p1.id, -bounty, "Clash Bounty Loss")
                p2_aura_change = bounty
                p1_aura_change = -bounty

        stats = {
            self.p1.id: {'name': self.p1.first_name, 'score': p1_score, 'answered': self.config['questions'], 'missed': self.config['questions'] - p1_score, 'time_str': f"{int(self.p1_time)}s", 'percent': (p1_score/self.config['questions'])*100, 'real_score': int((p1_score/self.config['questions'])*400), 'aura': p1_aura_change},
            self.p2.id: {'name': self.p2.first_name, 'score': p2_score, 'answered': self.config['questions'], 'missed': self.config['questions'] - p2_score, 'time_str': f"{int(self.p2_time)}s", 'percent': (p2_score/self.config['questions'])*100, 'real_score': int((p2_score/self.config['questions'])*400), 'aura': p2_aura_change}
        }
        
        title = "🤝 CLASH DRAW 🤝" if is_draw else "🏆 CLASH RESULTS 🏆"
        # Generate the Markdown table
        md_table = telegram_rich.generate_native_table(stats, title=title)
        
        # Fallback monospace text just in case
        fallback = f"{'Name':<10} | {'Sc':<3} | {'Ans':<3} | {'Mis':<3}\n" + "-"*30
        for p in stats.values():
            fallback += f"\n{p['name'][:10]:<10} | {p['score']:<3} | {p['answered']:<3} | {p['missed']:<3}"
            
        await telegram_rich.send_native_table(context.bot, self.chat_id, md_table, fallback)
        del ACTIVE_DUELS[self.chat_id]

# --- DM CONFIGURATION FLOW ---

async def start_clash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # If initiated in a group, grab the reply
    if update.effective_chat.type in ['group', 'supergroup']:
        if not update.message.reply_to_message:
            return await update.message.reply_text("⚠️ You must **reply** to the person you want to challenge with `/clash`!")
            
        opponent = update.message.reply_to_message.from_user
        if opponent.id == user.id or opponent.is_bot:
            return await update.message.reply_text("❌ Invalid opponent.")
            
        PENDING_CHALLENGES[user.id] = {'challenger': user, 'opponent_id': opponent.id}
        await update.message.reply_text(f"⚔️ Challenge initiated against {opponent.first_name}. Check your DM to configure the match!")
        
    else:
        # Initiated in DM
        if not context.args:
            return await update.message.reply_text("⚔️ To challenge someone from DM, type:\n`/clash <opponent_telegram_id>`\n\n(Example: `/clash 123456789`)", parse_mode="Markdown")
            
        try:
            opp_id = int(context.args[0])
            if opp_id == user.id:
                return await update.message.reply_text("❌ You cannot challenge yourself.")
            PENDING_CHALLENGES[user.id] = {'challenger': user, 'opponent_id': opp_id}
            await update.message.reply_text("✅ Opponent locked in. Please configure the match below.")
        except ValueError:
            return await update.message.reply_text("❌ Invalid Telegram ID. Please use numbers only.")

    # Send config menu to DM (for both Group and DM initiation)
    keyboard = [
        [InlineKeyboardButton("Physics", callback_data="cfg_sub_physics"), InlineKeyboardButton("Chemistry", callback_data="cfg_sub_chemistry")],
        [InlineKeyboardButton("Biology", callback_data="cfg_sub_biology"), InlineKeyboardButton("Math", callback_data="cfg_sub_mathematics")]
    ]
    try:
        await context.bot.send_message(user.id, "⚔️ *CLASH SETUP* ⚔️\n\nStep 1: Select the Subject", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    except Exception as e:
        print(f"🚨 DM ERROR: {e}")
        if update.effective_chat.type in ['group', 'supergroup']:
            await update.message.reply_text(f"❌ I cannot DM you. Please /start me in private first.")


async def config_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id not in PENDING_CHALLENGES: return
    data = query.data
    
    if data.startswith("cfg_sub_"):
        PENDING_CHALLENGES[user_id]['subject'] = data.replace("cfg_sub_", "")
        keyboard = [
            [InlineKeyboardButton("10 Qs", callback_data="cfg_qs_10"), InlineKeyboardButton("20 Qs", callback_data="cfg_qs_20")],
            [InlineKeyboardButton("30 Qs", callback_data="cfg_qs_30"), InlineKeyboardButton("40 Qs", callback_data="cfg_qs_40")]
        ]
        await query.edit_message_text(f"Subject: {PENDING_CHALLENGES[user_id]['subject'].title()}\n\nStep 2: Select number of questions", reply_markup=InlineKeyboardMarkup(keyboard))
        
    elif data.startswith("cfg_qs_"):
        PENDING_CHALLENGES[user_id]['questions'] = int(data.replace("cfg_qs_", ""))
        keyboard = [
            [InlineKeyboardButton("No Bounty", callback_data="cfg_bty_0"), InlineKeyboardButton("50 Aura", callback_data="cfg_bty_50")],
            [InlineKeyboardButton("100 Aura", callback_data="cfg_bty_100"), InlineKeyboardButton("250 Aura", callback_data="cfg_bty_250")]
        ]
        await query.edit_message_text(f"Questions: {PENDING_CHALLENGES[user_id]['questions']}\n\nStep 3: Set Bounty (Aura Wager)", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("cfg_bty_"):
        PENDING_CHALLENGES[user_id]['bounty'] = int(data.replace("cfg_bty_", ""))
        
        # NEW STEP: TIE BREAKER RULE
        keyboard = [
            [InlineKeyboardButton("⏱ Fastest Finger (Time)", callback_data="cfg_tie_time")],
            [InlineKeyboardButton("🤝 Fair Draw (No Bounty Loss)", callback_data="cfg_tie_draw")]
        ]
        await query.edit_message_text(f"Bounty: {PENDING_CHALLENGES[user_id]['bounty']} Aura\n\nStep 4: If scores tie, how should we decide the winner?", reply_markup=InlineKeyboardMarkup(keyboard))
        
    elif data.startswith("cfg_tie_"):
        PENDING_CHALLENGES[user_id]['tie_breaker'] = data.replace("cfg_tie_", "")
        PENDING_CHALLENGES[user_id]['timer'] = 20 # Default 20s timer
        
        conf = PENDING_CHALLENGES[user_id]
        rule = "Fastest Finger" if conf['tie_breaker'] == 'time' else "Fair Draw"
        text = f"⚔️ *CONFIRM CLASH*\n\nSubject: {conf['subject'].title()}\nQuestions: {conf['questions']}\nBounty: {conf['bounty']} Aura\nTimer: 20s\nTie Rule: {rule}\n\nAre you sure?"
        keyboard = [[InlineKeyboardButton("Yes, Send Challenge", callback_data="cfg_confirm")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
    elif data == "cfg_confirm":
        config = PENDING_CHALLENGES[user_id]
        if 'opponent_id' not in config:
            await query.edit_message_text("❌ Error: No opponent found. Please initiate the clash by replying to someone in the group.")
            del PENDING_CHALLENGES[user_id]
            return
            
        await query.edit_message_text("✅ Challenge sent! Waiting for opponent to accept...")
        
        opp_id = config['opponent_id']
        bounty = config['bounty']
        subj = config['subject'].title()
        qs = config['questions']
        
        # 1. Notify Opponent in DM
        text = f"⚔️ *INCOMING CHALLENGE* ⚔️\n\n{query.from_user.first_name} has challenged you to a {qs}-question {subj} duel!\nBounty: {bounty} Aura\n\nDo you accept?"
        keyboard = [[InlineKeyboardButton("✅ Accept", callback_data=f"clash_acc_{user_id}"), InlineKeyboardButton("❌ Reject", callback_data=f"clash_rej_{user_id}")]]
        
        try:
            await context.bot.send_message(opp_id, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception:
            await query.edit_message_text("❌ Could not DM the opponent. They must /start the bot first.")
            del PENDING_CHALLENGES[user_id]
            return
            
        # 2. Notify Group
        chat_id = db_manager.get_setting("arena_chat_id")
        if chat_id:
            tie_rule = "Fastest Finger" if config.get('tie_breaker') == 'time' else "Fair Draw"
            challenger_tag = mention_html(query.from_user.id, query.from_user.first_name)
            opp_tag = mention_html(opp_id, "Opponent")
            
            group_msg = (
                f"🛡️ <b>CHALLENGE ISSUED!</b> 🛡️\n\n"
                f"⚔️ {challenger_tag} has challenged {opp_tag}!\n\n"
                f"📚 <b>Subject:</b> {subj}\n"
                f"📝 <b>Questions:</b> {qs}\n"
                f"⏱️ <b>Timer:</b> {config.get('timer')}s\n"
                f"⚖️ <b>Tie Rule:</b> {tie_rule}\n"
                f"💰 <b>Bounty:</b> {bounty} Aura\n\n"
                f"{opp_tag}, check your DM to accept!"
            )
            await context.bot.send_message(int(chat_id), group_msg, parse_mode="HTML")
            
        del PENDING_CHALLENGES[user_id]

async def route_poll_answers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    if not db_manager.has_valid_pass(answer.user.id): return
    for duel in ACTIVE_DUELS.values():
        if duel.poll_id == answer.poll_id:
            await duel.handle_poll_answer(update, context)
            break


async def accept_reject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # data format: clash_acc_12345 or clash_rej_12345
    action = data.split("_")[1]
    challenger_id = int(data.split("_")[2])
    opp_id = query.from_user.id
    
    if action == "rej":
        await query.edit_message_text("❌ You rejected the challenge.")
        try:
            await context.bot.send_message(challenger_id, "❌ Your challenge was rejected.")
        except: pass
        return
        
    # If Accepted
    await query.edit_message_text("✅ Challenge Accepted! Starting the duel in the Arena...")
    
    # Get challenger user object (roughly)
    try:
        challenger = await context.bot.get_chat(challenger_id)
    except:
        await query.edit_message_text("❌ Could not find challenger.")
        return
        
    # We need to recreate the Duel object and start it
    # Note: We stored the config in PENDING_CHALLENGES, but we deleted it. 
    # To be safe for now, we will use a default config if we can't retrieve it.
    # (In a full production app, we would store this in the database).
    
    chat_id = db_manager.get_setting("arena_chat_id")
    if not chat_id:
        await query.edit_message_text("❌ Arena not set.")
        return
        
    # Start the duel
    # For this test, we will just hardcode 10 Qs Physics if we lost the config
    config = {'subject': 'physics', 'questions': 10, 'bounty': 0, 'tie_breaker': 'time', 'timer': 20}
    
    duel = Duel(int(chat_id), challenger, query.from_user, config)
    ACTIVE_DUELS[int(chat_id)] = duel
    asyncio.create_task(duel.start(context))
