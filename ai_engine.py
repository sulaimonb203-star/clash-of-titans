import os
import httpx
import logging
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

async def get_explanation_async(question_text, correct_answer, options):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key: return f"The correct answer is {correct_answer}."

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    
    prompt = f"Question: {question_text}\nOptions: {options}\nCorrect Answer: {correct_answer}\n\nExplain in 1 or 2 very short sentences why this is the correct answer. Keep the entire explanation under 150 characters. Be educational and direct."
    payload = {"model": "openai/gpt-oss-20b", "messages": [{"role": "user", "content": prompt}]}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            exp = data["choices"][0]["message"]["content"].strip()
            
            # HARD LIMIT FOR TELEGRAM (Max 200 characters)
            if len(exp) > 190:
                exp = exp[:187] + "..."
            return exp
    except Exception as e:
        logger.error(f"Groq API Error: {e}")
        return f"The correct answer is {correct_answer}."
