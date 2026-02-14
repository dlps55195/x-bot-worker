import os
import json
import asyncio
import random
import re
import httpx
import time
from datetime import datetime
from fake_useragent import UserAgent
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
LIST_URL = "https://x.com/i/lists/2011289206513930641"
SEEN_POSTS_FILE = "seen_posts.json"
AI_API_KEY = os.getenv("AI_API_KEY")

def sanitize_cookies(cookie_list):
    """Cleans cookie attributes for Playwright compatibility."""
    cleaned = []
    for cookie in cookie_list:
        if "sameSite" in cookie and cookie["sameSite"] not in ["Strict", "Lax", "None"]: 
            cookie["sameSite"] = "Lax"
        cookie.pop("hostOnly", None)
        cookie.pop("session", None)
        cleaned.append(cookie)
    return cleaned

def get_ai_reply(tweet_data):
    # --- PROMPT PRESERVED EXACTLY ---
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
        print(f"⚠️ AI Generation Error: {e}")
        return None

async def run_bot():
    print("💓 Bot Start: Resilient Engine Active")
    current_time = time.time()
    seen_data = {}
    
    if os.path.exists(SEEN_POSTS_FILE):
        try:
            with open(SEEN_POSTS_FILE, 'r') as f:
                raw_data = json.load(f)
                seen_data = raw_data if isinstance(raw_data, dict) else {p: current_time for p in raw_data}
            seen_data = {k: v for k, v in seen_data.items() if current_time - v < 86400}
        except: pass

    async with async_playwright() as p:
        # Launch with Stealth Arguments
        browser = await p.chromium.launch(headless=True, args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox'
        ])
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent=UserAgent().random
        )
        
        cookie_raw = os.getenv("X_COOKIES")
        if not cookie_raw:
            print("❌ Error: X_COOKIES environment variable not found.")
            return
        
        await context.add_cookies(sanitize_cookies(json.loads(cookie_raw)))
        page = await context.new_page()
        
        # --- RESILIENT NAVIGATION (Fixes the Timeout Error) ---
        print(f"📡 Navigating to List...")
        try:
            await page.goto(LIST_URL, wait_until="commit", timeout=60000)
            print("⏳ Waiting for tweets to load...")
            await page.wait_for_selector('article[data-testid="tweet"]', timeout=30000)
            await asyncio.sleep(5) 
        except Exception as e:
            print(f"⚠️ Navigation Warning (Proceeding anyway): {e}")

        # Check for Login Redirect
        if "login" in page.url:
            print("🚨 CRITICAL: Cookies are EXPIRED. Bot cannot see the list.")
            await browser.close()
            return

        # Scroll to load fresh content
        await page.evaluate("window.scrollBy(0, 1000)")
        await asyncio.sleep(3)

        tweet_elements = await page.locator('article[data-testid="tweet"]').all()
        candidates = []

        # DATA COLLECTION
        for tweet in tweet_elements:
            try:
                # Filter out ads and replies
                is_reply = await tweet.locator('div:has-text("Replying to")').count() > 0
                if is_reply: continue

                link = tweet.locator('a[href*="/status/"]').first
                tweet_url = await link.get_attribute("href")
                unique_id = tweet_url.split('/')[-1] if tweet_url else None
                
                if unique_id and unique_id not in seen_data:
                    text_el = tweet.locator('[data-testid="tweetText"]').first
                    text = (await text_el.inner_text()).replace('\n', ' ') if await text_el.count() > 0 else ""
                    
                    author_el = tweet.locator('div[dir="ltr"] > span').first
                    author = await author_el.inner_text() if await author_el.count() > 0 else "Unknown"
                    
                    candidates.append({
                        "element": tweet, 
                        "id": unique_id, 
                        "data": {"text": text, "author": author, "media_desc": "Visual context included"}
                    })
            except: continue
        
        print(f"🎯 Found {len(candidates)} new posts.")

        # INTERACTION
        for target in candidates[:3]: 
            reply_text = get_ai_reply(target['data'])
            if not reply_text: continue
            
            print(f"💬 Replying to {target['data']['author']}: {reply_text}")

            try:
                await target['element'].scroll_into_view_if_needed()
                await asyncio.sleep(2)
                
                reply_btn = target['element'].locator('[data-testid="reply"]').first
                await reply_btn.click(force=True)
                
                textarea = page.locator('[data-testid="tweetTextarea_0"]')
                await textarea.wait_for(state="visible", timeout=10000)
                
                await textarea.click()
                for char in reply_text:
                    await page.keyboard.type(char)
                    await asyncio.sleep(random.uniform(0.02, 0.07))
                
                await asyncio.sleep(2)
                await page.keyboard.press("Control+Enter")
                await asyncio.sleep(5)

                # Verification and cleanup
                if not await textarea.is_visible():
                    print(f"✅ Success: {target['id']}")
                    seen_data[target['id']] = time.time()
                    with open(SEEN_POSTS_FILE, 'w') as f:
                        json.dump(seen_data, f)
                else:
                    await page.keyboard.press("Escape")

                await asyncio.sleep(random.uniform(15, 30))
                
            except Exception as e:
                print(f"⚠️ Interaction Error: {e}")
                await page.keyboard.press("Escape")

        await browser.close()
        print("🏁 Session Complete.")

if __name__ == "__main__":
    asyncio.run(run_bot())