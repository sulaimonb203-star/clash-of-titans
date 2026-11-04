import os
import httpx
import logging
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)
ALOC_URL = "https://dev.aloc.com.ng/api/v1/questions"

async def fetch_questions(subject: str = None, topic: str = None, year: str = None, limit: int = 15, cursor: str = None) -> tuple[list[dict], str]:
    """Fetches a single batch of questions. Returns (questions, next_cursor)."""
    api_key = os.getenv("ALOC_API_KEY")
    if not api_key: return [], None

    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    params = {"limit": min(limit, 15)} # API max is 15
    
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
                formatted.append({
                    "id": q["id"],
                    "question": q["text"],
                    "options": q["options"],
                    "correct": q["correctAnswer"],
                    "subject": q.get("subject", subject),
                    "topic": q.get("topic", topic),
                    "year": q.get("year", year),
                    "imageUrl": q.get("imageUrl")
                })
            return formatted, next_cursor
    except Exception as e:
        logger.error(f"ALOC Fetch Error: {e}")
        return [], None

async def fetch_bulk_questions(subject: str = None, topic: str = None, total_needed: int = 100) -> list[dict]:
    """Fetches exactly `total_needed` questions for a SPECIFIC topic using pagination."""
    all_questions = []
    seen_ids = set()
    cursor = None
    attempts = 0
    max_attempts = 15 # Prevent infinite loops

    while len(all_questions) < total_needed and attempts < max_attempts:
        batch, next_cursor = await fetch_questions(
            subject=subject, topic=topic, limit=15, cursor=cursor
        )
        
        if not batch:
            break # No more questions available for this topic
            
        for q in batch:
            if q["id"] not in seen_ids:
                all_questions.append(q)
                seen_ids.add(q["id"])
                if len(all_questions) >= total_needed:
                    break
                    
        cursor = next_cursor
        if not cursor:
            break # No more pages
            
        attempts += 1
        import asyncio
        await asyncio.sleep(0.5) # Be nice to the API

    print(f"🔍 Strict Fetch: Requested {total_needed} for '{topic}', Found {len(all_questions)}.")
    return all_questions

async def fetch_topics(subject: str) -> list[dict]:
    """Fetches the official topic taxonomy (including subtopics) from the ALOC API."""
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
                        all_topics = []
                        for topic in data:
                            # Add the main topic
                            all_topics.append({
                                "name": topic.get("name"),
                                "displayName": topic.get("displayName"),
                                "questionCount": topic.get("questionCount", 0)
                            })
                            # Add all subtopics
                            for sub in topic.get("subtopics", []):
                                all_topics.append({
                                    "name": sub.get("name"),
                                    "displayName": f"{topic.get('displayName')} - {sub.get('displayName')}",
                                    "questionCount": sub.get("questionCount", 0)
                                })
                        print(f"✅ Found {len(all_topics)} topics/subtopics for {subj_slug}")
                        return all_topics
        except Exception as e:
            logger.error(f"Error fetching topics for {subj_slug}: {e}")
    
    return []