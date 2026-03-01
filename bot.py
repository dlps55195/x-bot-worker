import os
import json
import asyncio
import random
import httpx
from supabase import create_client, Client
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from fake_useragent import UserAgent

# --- CONFIGURATION ---
URL: str = os.environ.get("SUPABASE_URL", "")
KEY: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
AI_API_KEY = os.environ.get("AI_API_KEY", "")
SEEN_POSTS_FILE = "seen_posts.json"

# Initialize Supabase
supabase: Client = None
if URL and KEY:
    supabase = create_client(URL, KEY)

# --- HELPER FUNCTIONS ---

def sanitize_cookies(cookie_list):
    """
    Standardizes cookies for Playwright by removing non-standard browser keys.
    """
    cleaned = []
    valid_samesite = ["Strict", "Lax", "None"]
    for cookie in cookie_list:
        if "sameSite" in cookie:
            if cookie["sameSite"] not in valid_samesite:
                cookie["sameSite"] = "Lax"
        
        # Remove browser-specific internal keys that can cause load errors
        for key in ["hostOnly", "session", "id", "storeId"]:
            cookie.pop(key, None)
        cleaned.append(cookie)
    return cleaned

def get_ai_reply(tweet_data):
    """
    Generates a reply using OpenRouter only.
    """
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://education-bot-demo.com", 
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
    
    payload = {
        "model": "google/gemini-2.0-flash-001", 
        "messages": [{"role": "user", "content": system_instruction}]
    }
    
    try:
        with httpx.Client() as client:
            resp = client.post(url, headers=headers, json=payload, timeout=30.0)
            if resp.status_code == 200:
                content = resp.json()['choices'][0]['message']['content']
                return content.strip().lower()
            else:
                print(f"⚠️ AI API Error {resp.status_code}")
                return None
    except Exception as e:
        print(f"⚠️ AI Network Error: {e}")
        return None

async def process_user(context, profile):
    page = await context.new_page()
    list_url = profile.get("target_lists", [None])[0]
    if not list_url: 
        return

    # Memory handling
    seen_posts = []
    if os.path.exists(SEEN_POSTS_FILE):
        try:
            with open(SEEN_POSTS_FILE, 'r') as f:
                data = json.load(f)
                seen_posts = data if isinstance(data, list) else []
        except:
            seen_posts = []

    print(f"📡 Accessing List: {list_url}")
    try:
        await page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(5) 

        # Handle UI overlays
        dismiss_selectors = ['[data-testid="app-bar-close"]', '[aria-label="Close"]']
        for selector in dismiss_selectors:
            if await page.locator(selector).is_visible():
                await page.locator(selector).click()

        # Wait for tweets to render
        await page.wait_for_selector('article[data-testid="tweet"]', timeout=20000)
        tweets = await page.locator('article[data-testid="tweet"]').all()
        
        replies_count = 0
        for tweet in tweets:
            if replies_count >= 3: break 

            try:
                # Ensure visibility
                await tweet.scroll_into_view_if_needed()
                await asyncio.sleep(1.5)
                
                text_el = tweet.locator('[data-testid="tweetText"]').first
                author_el = tweet.locator('[data-testid="User-Name"]').first
                
                if not await text_el.count(): continue
                
                tweet_text = await text_el.inner_text()
                author_handle = await author_el.inner_text()
                post_id = str(hash(f"{author_handle}_{tweet_text[:50]}"))

                if post_id in seen_posts:
                    continue

                reply_content = get_ai_reply({"author": author_handle, "text": tweet_text, "media_desc": "none"})

                if reply_content:
                    reply_btn = tweet.locator('[data-testid="reply"]').first
                    await reply_btn.click()
                    
                    # Human-like typing
                    composer = page.locator('[data-testid="tweetTextarea_0"]').first
                    await composer.wait_for(state="visible", timeout=5000)
                    await composer.click()
                    await page.keyboard.type(reply_content, delay=40)
                    await asyncio.sleep(1)
                    await page.keyboard.press("Control+Enter")
                    
                    print(f"✅ Sent reply to {author_handle.splitlines()[0]}")
                    
                    # Memory update capped at 1000 items to prevent bloat
                    seen_posts.append(post_id)
                    with open(SEEN_POSTS_FILE, 'w') as f:
                        json.dump(seen_posts[-1000:], f)
                    
                    replies_count += 1
                    await asyncio.sleep(random.randint(45, 90))

            except Exception as e:
                print(f"⚠️ Skipping tweet due to UI error: {str(e)[:50]}")
                continue

    except PlaywrightTimeoutError:
        print("❌ Timeout: The page took too long to load.")
        # Debugging step: Save a screenshot to see what blocked the script
        await page.screenshot(path="timeout_error.png")
        print("📸 Saved error screenshot to timeout_error.png")
    except Exception as e:
        print(f"❌ Error during list processing: {e}")
            
    await page.close()

# --- MAIN ENGINE LOOP ---

async def run_bot():
    print("🚀 Starting Bot Engine...")
    
    if not supabase:
        print("❌ Supabase credentials not found.")
        return

    response = supabase.table("profiles").select("*").eq("is_active", True).execute()
    active_profiles = response.data

    if not active_profiles:
        print("😴 No active profiles.")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        for profile in active_profiles:
            print(f"🤖 Processing Profile: {profile['id']}")
            context = await browser.new_context(user_agent=UserAgent().chrome)
            
            try:
                raw_cookies = json.loads(profile['x_cookies'])
                clean_cookies = sanitize_cookies(raw_cookies)
                await context.add_cookies(clean_cookies)
                
                await process_user(context, profile)
                
            except Exception as e:
                print(f"❌ Profile {profile['id']} failed: {e}")
            
            await context.close()
            await asyncio.sleep(random.randint(5, 10))
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_bot())
