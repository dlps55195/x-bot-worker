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
    if not lists:
        print("ℹ️ No target lists found for this user.")
        return

    # Load seen posts from local memory to avoid double-replying
    seen_posts = []
    if os.path.exists(SEEN_POSTS_FILE):
        try:
            with open(SEEN_POSTS_FILE, 'r') as f:
                seen_posts = json.load(f)
        except:
            seen_posts = []

    for list_url in lists:
        print(f"📡 Scanning List: {list_url}")
        try:
            # FIX: Wait for 'commit' (the URL changed) then wait manually. 
            # This prevents the 'networkidle' hang you saw in the logs.
            await page.goto(list_url, wait_until="commit", timeout=60000)
            print("⏳ Page committed. Waiting for tweets to render...")
            await asyncio.sleep(10) # Heavy wait for X's slow UI

            # STEALTH: Check if X redirected us to the login page (happens if cookies die)
            if "login" in page.url or "i/flow/login" in page.url:
                print(f"❌ AUTH FAILURE: Cookies for user {profile['id']} have expired.")
                break

            # Ensure tweets are actually on the screen
            await page.wait_for_selector('article[data-testid="tweet"]', timeout=30000)
            
            # Find all tweet articles
            tweets = await page.locator('article[data-testid="tweet"]').all()
            print(f"👀 Found {len(tweets)} tweets. Processing top 5...")

            replies_this_run = 0
            for tweet in tweets:
                if replies_this_run >= 5: # Safety cap per list
                    break

                try:
                    # 1. Extract Tweet Text and Author
                    # We use .first to ensure we don't accidentally grab a sub-element
                    tweet_text_el = tweet.locator('[data-testid="tweetText"]').first
                    author_handle_el = tweet.locator('[data-testid="User-Name"]').first

                    if not await tweet_text_el.count() or not await author_handle_el.count():
                        continue

                    tweet_text = await tweet_text_el.inner_text()
                    author_handle = await author_handle_el.inner_text()

                    # Create a unique ID to track this post
                    post_id = str(hash(f"{author_handle}_{tweet_text[:50]}"))

                    if post_id in seen_posts:
                        print(f"⏭️ Skipping: Already replied to {author_handle.split()[-1]}")
                        continue

                    print(f"🎯 Analyzing post from {author_handle.split()[-1]}...")

                    # 2. Generate the AI Reply using the Universal Human Framework
                    reply_content = get_ai_reply({
                        "author": author_handle,
                        "text": tweet_text,
                        "media_desc": "none"
                    })

                    if reply_content:
                        # 3. Execution: Click Reply -> Type -> Send
                        await tweet.locator('[data-testid="reply"]').first.click()
                        await asyncio.sleep(3) # Wait for reply modal

                        # Fill the tweet textarea
                        await page.locator('[data-testid="tweetTextarea_0"]').fill(reply_content)
                        await asyncio.sleep(2)

                        # Send via Keyboard (Control+Enter is more 'human' than clicking the button)
                        await page.keyboard.press("Control+Enter")
                        print(f"✅ SUCCESSFULLY REPLIED: {reply_content}")

                        # 4. Update Memory
                        seen_posts.append(post_id)
                        with open(SEEN_POSTS_FILE, 'w') as f:
                            json.dump(seen_posts, f)

                        replies_this_run += 1
                        
                        # 5. Anti-Ban Delay (Randomized)
                        delay = random.randint(50, 100)
                        print(f"⏳ Sleeping {delay}s to stay under the radar...")
                        await asyncio.sleep(delay)

                except Exception as tweet_err:
                    print(f"⚠️ Skipping tweet due to UI error: {tweet_err}")
                    continue

        except Exception as e:
            print(f"❌ Critical error on list {list_url}: {e}")
            
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