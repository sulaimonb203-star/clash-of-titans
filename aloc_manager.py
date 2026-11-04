from dotenv import load_dotenv
load_dotenv()
import os
import httpx
import logging
import db_manager
from db_manager import is_question_on_cooldown, log_question_used

logger = logging.getLogger(__name__)

ALOC_URL = "https://dev.aloc.com.ng/api/v1/questions"

async def fetch_questions(subject: str = None, topic: str = None, year: str = None, limit: int = 10) -> list[dict]:
    api_key = os.getenv("ALOC_API_KEY")
    if not api_key:
        logger.error("ALOC API Key missing!")
        return []

    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    
    params = {"limit": 15}
    if subject: params["subject"] = subject.lower()
    if topic: params["topic"] = topic.lower()
    if year: params["year"] = year

    valid_questions = []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(ALOC_URL, headers=headers, params=params)
            response.raise_for_status()
            response_json = response.json()
            data = response_json.get("data", [])
            print(f"📦 ALOC Response: Found {len(data)} questions.")
            
            for q in data:
                q_id = q["id"]
                
                if is_question_on_cooldown(q_id, days=14):
                    continue
                
                valid_questions.append({
                    "id": q_id,
                    "question": q["text"],
                    "options": q["options"],
                    "correct": q["correctAnswer"],
                    "subject": q.get("subject", subject),
                    "topic": q.get("topic", topic),
                    "year": q.get("year", year),
                    "imageUrl": q.get("imageUrl") # <--- CAPTURING THE IMAGE URL
                })
                
                log_question_used(q_id, q.get("subject", subject), q.get("topic", topic), q.get("year", year))

                if len(valid_questions) >= limit:
                    break
                    
            return valid_questions

    except Exception as e:
        logger.error(f"Armory Error fetching questions: {e}")
        return []

async def fetch_bulk_questions(subject: str = None, topic: str = None, year: str = None, total_needed: int = 100) -> list[dict]:
    """Fetches a large number of unique questions by making multiple API calls."""
    all_questions = []
    seen_ids = set()
    attempts = 0
    max_attempts = 15 # Prevent infinite loops if API is out of questions

    while len(all_questions) < total_needed and attempts < max_attempts:
        # Fetch a batch (API max is 15 per request)
        batch = await fetch_questions(subject=subject, topic=topic, year=year, limit=15)
        
        if not batch:
            # If we get an empty batch, the API might be out of questions for this filter
            break
            
        for q in batch:
            if q["id"] not in seen_ids:
                all_questions.append(q)
                seen_ids.add(q["id"])
                
                if len(all_questions) >= total_needed:
                    break
                    
        attempts += 1
        # Small delay to prevent hitting API rate limits too hard
        import asyncio
        await asyncio.sleep(1)

    return all_questions


async def fetch_topics(subject: str) -> list[dict]:
    """Fetches the official topic taxonomy from the ALOC API."""
    api_key = os.getenv("ALOC_API_KEY")
    if not api_key:
        return []
        
    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    
    # Subject slug variants (mathematics vs general-mathematics)
    subject_variants = [subject.lower(), f"general-{subject.lower()}"]
    
    for subj_slug in subject_variants:
        url = f"https://dev.aloc.com.ng/api/v1/subjects/{subj_slug}/topics"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    # THE FIX: Parse the "data" key from the response
                    data = response.json().get("data", [])
                    if data:
                        print(f"✅ Found {len(data)} topics for {subj_slug}")
                        return data
                else:
                    print(f"⚠️ Status {response.status_code} for {subj_slug}")
        except Exception as e:
            print(f"❌ Error fetching topics for {subj_slug}: {e}")
    
    return []
