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

# --- HELPER FUNCTIONS ---

def sanitize_cookies(cookie_list):
    """
    Fixes raw cookies from extensions to be Playwright-compatible.
    Removes invalid 'sameSite' values and internal browser keys.
    """
    cleaned = []
    for cookie in cookie_list:
        if "sameSite" in cookie:
            if cookie["sameSite"] not in ["Strict", "Lax", "None"]:
                cookie["sameSite"] = "Lax" # Default to Lax if unknown
        
        # Remove keys that cause issues
        cookie.pop("hostOnly", None)
        cookie.pop("session", None)
        cleaned.append(cookie)
    return cleaned

def get_ai_reply(tweet_data):
    """
    Generates a reply using OpenRouter only.
    """
    url = "https://openrouter.ai/api/v1/chat/completions"
    # Note: AI_API_KEY should be your OpenRouter key
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://your-site.com", # Optional, for OpenRouter rankings
    }
    
    # --- YOUR ORIGINAL PROMPT (UNTOUCHED) ---
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
        "model": "google/gemini-2.0-flash-001", # OpenRouter model string
        "messages": [{"role": "user", "content": system_instruction}]
    }
    
    try:
        with httpx.Client() as client:
            resp = client.post(url, headers=headers, json=payload, timeout=30.0)
            if resp.status_code == 200:
                return resp.json()['choices'][0]['message']['content'].strip().lower()
            else:
                print(f"⚠️ AI Error {resp.status_code}: {resp.text}")
                return None
    except Exception as e:
        print(f"⚠️ AI Network Error: {e}")
        return None

async def process_user(context, profile):
    page = await context.new_page()
    list_url = profile.get("target_lists", [None])[0]
    if not list_url: return

    # FIX 1: Ensure seen_posts is strictly a list
    seen_posts = []
    if os.path.exists(SEEN_POSTS_FILE):
        try:
            with open(SEEN_POSTS_FILE, 'r') as f:
                data = json.load(f)
                seen_posts = data if isinstance(data, list) else []
        except:
            seen_posts = []

    print(f"📡 Scanning List: {list_url}")
    try:
        await page.goto(list_url, wait_until="commit", timeout=60000)
        await asyncio.sleep(10) 

        # FIX 2: Close any popups/banners that intercept clicks
        try:
            # Look for "Close" or "Dismiss" on any annoying banners
            banner_close = page.locator('[data-testid="app-bar-close"], [aria-label="Close"]').first
            if await banner_close.is_visible():
                await banner_close.click()
        except:
            pass

        # Wait for tweets
        await page.wait_for_selector('article[data-testid="tweet"]', timeout=20000)
        tweets = await page.locator('article[data-testid="tweet"]').all()
        
        replies_count = 0
        for i, tweet in enumerate(tweets):
            if replies_count >= 3: break # Keep it light per user

            try:
                # Scroll the tweet into view so it's not "outside viewport"
                await tweet.scroll_into_view_if_needed()
                
                text_el = tweet.locator('[data-testid="tweetText"]').first
                author_el = tweet.locator('[data-testid="User-Name"]').first
                
                if not await text_el.count(): continue
                
                tweet_text = await text_el.inner_text()
                author_handle = await author_el.inner_text()
                post_id = str(hash(f"{author_handle}_{tweet_text[:50]}"))

                if post_id in seen_posts:
                    print(f"⏭️ Already seen {author_handle[:15]}")
                    continue

                reply_content = get_ai_reply({"author": author_handle, "text": tweet_text, "media_desc": "none"})

                if reply_content:
                    # FIX 3: Use force=True to click 'through' any invisible overlays
                    reply_btn = tweet.locator('[data-testid="reply"]').first
                    await reply_btn.click(force=True, timeout=5000)
                    
                    await asyncio.sleep(3)
                    await page.locator('[data-testid="tweetTextarea_0"]').fill(reply_content)
                    await asyncio.sleep(1)
                    await page.keyboard.press("Control+Enter")
                    
                    print(f"✅ REPLIED to {author_handle}")
                    
                    # Update Memory correctly
                    seen_posts.append(post_id)
                    with open(SEEN_POSTS_FILE, 'w') as f:
                        json.dump(seen_posts, f)
                    
                    replies_count += 1
                    await asyncio.sleep(random.randint(40, 80))

            except Exception as e:
                print(f"⚠️ Error on tweet {i}: {str(e)[:50]}...")
                continue

    except Exception as e:
        print(f"❌ Error on list: {e}")
            
    await page.close()

# --- MAIN ENGINE LOOP ---

async def run_bot():
    print("🚀 SaaS Engine Ignition...")
    
    # 1. Fetch Active Users from Supabase
    response = supabase.table("profiles").select("*").eq("is_active", True).execute()
    active_profiles = response.data

    if not active_profiles:
        print("😴 No active bots found in database.")
        return

    async with async_playwright() as p:
        # Launch Browser in Stealth Mode
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-setuid-sandbox'
            ]
        )
        
        for profile in active_profiles:
            print(f"🤖 Processing User: {profile['id']}")
            context = await browser.new_context(user_agent=UserAgent().random)
            
            try:
                # 2. Load & Sanitize Cookies
                raw_cookies = json.loads(profile['x_cookies'])
                clean_cookies = sanitize_cookies(raw_cookies)
                await context.add_cookies(clean_cookies)
                
                # 3. Run Logic
                await process_user(context, profile)
                
            except Exception as e:
                print(f"❌ Failed user {profile['id']}: {e}")
            
            await context.close()
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_bot())