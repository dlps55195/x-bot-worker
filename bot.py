import os
import json
import asyncio
import random
import httpx
from supabase import create_client, Client
from playwright.async_api import async_playwright

# 1. AUTH SETUP
URL: str = os.environ.get("SUPABASE_URL")
KEY: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(URL, KEY)
AI_API_KEY = os.environ.get("AI_API_KEY")

async def get_ai_reply(tweet_data):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {AI_API_KEY}"}
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
      AI Logic (Productivity + Gym):"now im feeling guilty 🍪😭 when did you start doing thar?"
    - Post: "Is it just me or is the new X UI actually kind of nice?"
      AI Logic (Opinion + UI):"tbh i’m actually liking it too, looks way cleaner 👌"

    [STRICT STYLE CONSTRAINTS]
    - lowercase only.
    - Max 12 words.
    - 1-2 emojis max.
    - Slang allowed: lol, rn, tbh, huge, same, honestly.
    - BANNED: delve, leverage, explore, transformative, "nice post!", "great work!".
    - Output ONLY the raw reply text.
    """
    payload = {
        "model": "google/gemini-2.0-flash-001",
        "messages": [{"role": "user", "content": system_instruction}]
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=payload)
            return resp.json()['choices'][0]['message']['content'].strip().lower()
    except:
        return None

async def process_user(context, user_profile):
    page = await context.new_page()
    list_url = user_profile['target_lists'][0]
    
    try:
        await page.goto(list_url, wait_until="commit", timeout=60000)
        await asyncio.sleep(10) # Essential wait for X UI
        
        tweets = await page.locator('article[data-testid="tweet"]').all()
        for tweet in tweets[:3]:
            text = await tweet.locator('[data-testid="tweetText"]').first.inner_text()
            author = await tweet.locator('[data-testid="User-Name"]').first.inner_text()
            
            reply = await get_ai_reply({"author": author, "text": text})
            if reply:
                await tweet.locator('[data-testid="reply"]').first.click()
                await asyncio.sleep(2)
                await page.locator('[data-testid="tweetTextarea_0"]').fill(reply)
                await page.keyboard.press("Control+Enter")
                print(f"✅ Replied to {author}")
                await asyncio.sleep(random.randint(30, 60))
    except Exception as e:
        print(f"❌ Error: {e}")
    await page.close()

async def main():
    print("🚀 SaaS Engine Starting...")
    users = supabase.table("profiles").select("*").eq("is_active", True).execute().data
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for user in users:
            print(f"👤 Processing User: {user['id']}")
            context = await browser.new_context()
            await context.add_cookies(json.loads(user['x_cookies']))
            await process_user(context, user)
            await context.close()
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())