import os
import httpx
import logging
import asyncio
from dotenv import load_dotenv
from db_manager import is_question_on_cooldown, log_question_used
load_dotenv()

logger = logging.getLogger(__name__)
ALOC_URL = "https://dev.aloc.com.ng/api/v1/questions"

# Banned keywords per subject to clean up ALOC's messy taxonomy
BANNED_TOPICS = {
    "physics": ["chemistry", "biology", "government", "geography", "algebra", "geometry"],
    "english": ["chemistry", "physics", "biology", "government", "geography", "mechanics"],
    "mathematics": ["mechanics", "chemistry", "biology", "government", "geography"],
    "biology": ["physics", "chemistry", "mathematics", "government"],
    "chemistry": ["physics", "biology", "mathematics", "government", "geography"]
}

async def fetch_questions(subject: str = None, topic: str = None, year: str = None, limit: int = 15, cursor: str = None) -> tuple[list[dict], str]:
    api_key = os.getenv("ALOC_API_KEY")
    if not api_key: return [], None

    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    params = {"limit": min(limit, 15)}
    
    if subject: params["subject"] = subject.lower()
    if topic and topic.lower() != "general": params["topic"] = topic.lower()
    if year: params["year"] = year
    if cursor: params["cursor"] = cursor

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(ALOC_URL, headers=headers, params=params)
            response.raise_for_status()
            json_data = response.json()
            
            data = json_data.get("data", [])
            next_cursor = json_data.get("pagination", {}).get("nextCursor")
            
            formatted = []
            for q in data:
                q_id = q["id"]
                # 14-DAY COOLDOWN CHECK
                if is_question_on_cooldown(q_id, days=14):
                    continue
                    
                formatted.append({
                    "id": q_id,
                    "question": q["text"],
                    "options": q["options"],
                    "correct": q["correctAnswer"],
                    "subject": q.get("subject", subject),
                    "topic": q.get("topic", topic),
                    "year": q.get("year", year),
                    "imageUrl": q.get("imageUrl")
                })
                # Mark as used immediately
                log_question_used(q_id, q.get("subject", subject), q.get("topic", topic), q.get("year", year))
                
            return formatted, next_cursor
    except Exception as e:
        logger.error(f"ALOC Fetch Error: {e}")
        return [], None

async def fetch_bulk_questions(subject: str = None, topic: str = None, total_needed: int = 100) -> list[dict]:
    all_questions = []
    seen_ids = set()
    cursor = None
    attempts = 0
    
    while len(all_questions) < total_needed and attempts < 20:
        batch, next_cursor = await fetch_questions(subject=subject, topic=topic, limit=15, cursor=cursor)
        if not batch and not next_cursor: break
            
        for q in batch:
            if q["id"] not in seen_ids:
                all_questions.append(q)
                seen_ids.add(q["id"])
                if len(all_questions) >= total_needed: break
                    
        cursor = next_cursor
        attempts += 1
        await asyncio.sleep(0.5)

    return all_questions[:total_needed]

async def fetch_topics(subject: str) -> list[dict]:
    api_key = os.getenv("ALOC_API_KEY")
    if not api_key: return []
        
    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    subject_variants = [subject.lower(), f"general-{subject.lower()}"]
    
    for subj_slug in subject_variants:
        url = f"https://dev.aloc.com.ng/api/v1/subjects/{subj_slug}/topics"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    data = response.json().get("data", [])
                    if data:
                        clean_topics = []
                        banned = BANNED_TOPICS.get(subject.lower(), [])
                        for t in data:
                            name = t.get("displayName", "").lower()
                            # Filter out cross-contaminated topics
                            if not any(b_word in name for b_word in banned):
                                clean_topics.append({
                                    "name": t.get("name"),
                                    "displayName": t.get("displayName"),
                                    "questionCount": t.get("questionCount", 0)
                                })
                        return clean_topics
        except Exception as e:
            logger.error(f"Error fetching topics: {e}")
    return []
