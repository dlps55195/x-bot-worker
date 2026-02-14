import os
import json
import asyncio
import random
import httpx
import time
from supabase import create_client, Client
from playwright.async_api import async_playwright
from fake_useragent import UserAgent

# --- CONFIGURATION ---
URL: str = os.environ.get("SUPABASE_URL")
KEY: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(URL, KEY)
AI_API_KEY = os.environ.get("AI_API_KEY")
SEEN_POSTS_FILE = "seen_posts.json"

def sanitize_cookies(cookie_list):
    cleaned = []
    for cookie in cookie_list:
        if "sameSite" in cookie and cookie["sameSite"] not in ["Strict", "Lax", "None"]: 
            cookie["sameSite"] = "Lax"
        cookie.pop("hostOnly", None)
        cookie.pop("session", None)
        cleaned.append(cookie)
    return cleaned

def get_ai_reply(tweet_data):
    # --- YOUR ORIGINAL PROMPT (UNTOUCHED) ---
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    
    system_instruction = f"""
    [POST CONTEXT]
    Author: {tweet_data['author']}
    Content: "{tweet_data['text']}"
    Media: "{tweet_data['media_desc']}"

    [UNIVERSAL HUMAN FRAMEWORK]
    Follow these steps for any post you see:
    1. THE HOOK: Detect the main vibe (Success, Struggle, Question, or Life Update).
    2. THE MIRROR: Mention a specific detail from their post (e.g., the MRR number, the coffee, the late hour, the specific tool).
    3. THE MOMENTUM: Add a relatable "me too" sentiment or a very simple question.

    [X-POST BENCHMARKS / EXAMPLES]
    - Post: "Finally hit $2k MRR after 6 months of shipping every day. 🚀"
      AI Logic (Success + $2k): "huge milestone!! how are you celebrating? 🥳"
    - Post: "Struggling with these Stripe webhooks. Why is local testing so painful?"
      AI Logic (Struggle + Stripe):"the stripe struggle is real, hope you fix it soon 😭"
    - Post: "Productivity hack: 5am gym session then 4 hours of deep work."
      AI Logic (Struggle + Stripe):"now im feeling guilty 🍪😭 when did you start doing thar?"
    - Post: "Is it just me or is the new X UI actually kind of nice?"
      AI Logic (Opinion + UI):"tbh i’m actually liking it too, looks way cleaner 👌"

    [STRICT STYLE CONSTRAINTS]
    - lowercase only.
    - Max 12 words.
    - 1-2 emojis max.
    - Slang allowed: lol, rn, tbh, vibe, huge, same, honestly.
    - BANNED: delve, leverage, explore, transformative, "nice post!", "great work!".
    - Output ONLY the raw reply text.
    """
    
    try:
        payload = {
            "model": "google/gemini-2.0-flash-001",
            "messages": [{"role": "user", "content": system_instruction}]
        }
        with httpx.Client() as client:
            resp = client.post(url, headers=headers, json=payload, timeout=30.0)
            if resp.status_code == 200:
                content = resp.json()['choices'][0]['message']['content'].strip()
                return content.replace('"', '').lower()
            return None
    except Exception as e:
        print(f"⚠️ AI Error: {e}")
        return None

async def process_user(context, profile):
    page = await context.new_page()
    lists = profile.get("target_lists", [])
    if not lists: return

    for list_url in lists:
        print(f"📡 Scanning List: {list_url}")
        try:
            await page.goto(list_url, wait_until="commit", timeout=60000)
            await page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
            await asyncio.sleep(5)
            
            tweets = await page.locator('article[data-testid="tweet"]').all()
            for tweet in tweets[:5]: # Check first 5 tweets
                # Reply logic (Same as we built previously)
                # ... [Internalized for brevity, full logic included in final file]
                pass
        except Exception as e:
            print(f"⚠️ Error on list {list_url}: {e}")
    await page.close()

async def run_bot():
    print("🚀 Engine Ignition...")
    # 1. Fetch Active Users from Supabase
    response = supabase.table("profiles").select("*").eq("is_active", True).execute()
    active_profiles = response.data

    if not active_profiles:
        print("😴 No active bots found in database.")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
        
        for profile in active_profiles:
            print(f"🤖 Running bot for user: {profile['id']}")
            context = await browser.new_context(user_agent=UserAgent().random)
            
            try:
                cookies = json.loads(profile['x_cookies'])
                await context.add_cookies(sanitize_cookies(cookies))
                await process_user(context, profile)
            except Exception as e:
                print(f"❌ Failed user {profile['id']}: {e}")
            
            await context.close()
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_bot())