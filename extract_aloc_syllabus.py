import asyncio
import httpx
import os
from dotenv import load_dotenv
load_dotenv()

SUBJECTS = ["english", "mathematics", "physics", "chemistry", "biology"]

async def fetch_subject_with_retry(client, subject, headers):
    subj_slug = "general-mathematics" if subject == "mathematics" else subject
    url = f"https://dev.aloc.com.ng/api/v1/subjects/{subj_slug}/topics"
    
    for attempt in range(4): # Try 4 times
        try:
            res = await client.get(url, headers=headers, timeout=15.0)
            if res.status_code == 200:
                return res.json().get("data", [])
        except Exception as e:
            if attempt == 3:
                return f"❌ Error after 4 tries: {e}"
            print(f"⚠️ Network blip on {subject}. Retrying in 2 seconds... (Attempt {attempt+1}/4)")
            await asyncio.sleep(2)
    return []

async def fetch_full_syllabus():
    api_key = os.getenv("ALOC_API_KEY")
    if not api_key:
        print("❌ API Key missing!")
        return

    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    output = []

    print("🔍 Fetching syllabus for all subjects... Please wait.\n")

    async with httpx.AsyncClient() as client:
        for subject in SUBJECTS:
            print(f"📥 Downloading {subject.upper()}...")
            output.append(f"\n{'='*50}\n📚 {subject.upper()} SYLLABUS\n{'='*50}\n")
            
            topics = await fetch_subject_with_retry(client, subject, headers)
            
            if isinstance(topics, str):
                output.append(topics + "\n")
                continue
                
            if not topics:
                output.append("❌ No topics found in ALOC for this subject.\n")
                continue

            for t in topics:
                t_name = t.get("displayName", "Unknown Topic")
                t_count = t.get("questionCount", 0)
                output.append(f"📌 {t_name} ({t_count} questions)")
                
                subtopics = t.get("subtopics", [])
                if subtopics:
                    for sub in subtopics:
                        sub_name = sub.get("displayName", "Unknown Subtopic")
                        output.append(f"   ↳ {sub_name}")
                output.append("") # Spacing
                
            await asyncio.sleep(1) # Small break between subjects

    final_text = "\n".join(output)
    
    # Save to Termux
    with open("ALOC_Syllabus_Guide.txt", "w") as f:
        f.write(final_text)
        
    # Save to Android Downloads folder
    try:
        with open("/sdcard/Download/ALOC_Syllabus_Guide.txt", "w") as f:
            f.write(final_text)
        print("\n✅ Success! File saved to your phone's Download folder: ALOC_Syllabus_Guide.txt")
    except Exception as e:
        print(f"\n✅ File saved in Termux: ALOC_Syllabus_Guide.txt (Couldn't save to Download: {e})")

    print("\n🎉 Extraction Complete! Open the file in your Downloads folder to see the full guide.")

asyncio.run(fetch_full_syllabus())
