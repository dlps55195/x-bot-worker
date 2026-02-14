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
    Generates a reply using the Universal Human Framework.
    """
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    
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

    # Load memory to avoid double-replying
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
            # FIX: Use 'commit' + manual wait to avoid timeouts
            await page.goto(list_url, wait_until="commit", timeout=60000)
            print("⏳ Page committed. Waiting for tweets to render...")
            await asyncio.sleep(8) 

            # Check if cookies expired (Redirected to login)
            if "login" in page.url or "i/flow/login" in page.url:
                print(f"❌ AUTH FAILURE: Cookies for user {profile['id']} have expired.")
                break

            # Wait for tweets
            try:
                await page.wait_for_selector('article[data-testid="tweet"]', timeout=20000)
            except:
                print("⚠️ No tweets found (or timeout waiting for selector).")
                continue
            
            tweets = await page.locator('article[data-testid="tweet"]').all()
            print(f"👀 Found {len(tweets)} tweets. Checking top 5...")

            replies_count = 0
            for tweet in tweets:
                if replies_count >= 5: break

                try:
                    # Extract Data
                    tweet_text_el = tweet.locator('[data-testid="tweetText"]').first
                    author_handle_el = tweet.locator('[data-testid="User-Name"]').first
                    
                    if not await tweet_text_el.count() or not await author_handle_el.count():
                        continue

                    tweet_text = await tweet_text_el.inner_text()
                    author_handle = await author_handle_el.inner_text()
                    
                    # Create ID
                    post_id = str(hash(f"{author_handle}_{tweet_text[:50]}"))
                    
                    if post_id in seen_posts:
                        print(f"⏭️ Skipping: Already replied to {author_handle.split()[-1]}")
                        continue

                    print(f"🎯 New post from {author_handle.split()[-1]}...")

                    # Generate Reply
                    reply_content = get_ai_reply({
                        "author": author_handle,
                        "text": tweet_text,
                        "media_desc": "none"
                    })

                    if reply_content:
                        # Click Reply
                        await tweet.locator('[data-testid="reply"]').first.click()
                        await asyncio.sleep(2)
                        
                        # Type & Send
                        await page.locator('[data-testid="tweetTextarea_0"]').fill(reply_content)
                        await asyncio.sleep(1)
                        await page.keyboard.press("Control+Enter")
                        
                        print(f"✅ REPLIED: {reply_content}")
                        
                        # Update Memory
                        seen_posts.append(post_id)
                        with open(SEEN_POSTS_FILE, 'w') as f:
                            json.dump(seen_posts, f)
                        
                        replies_count += 1
                        
                        # Random Delay
                        delay = random.randint(40, 90)
                        print(f"⏳ Sleeping {delay}s...")
                        await asyncio.sleep(delay)

                except Exception as e:
                    print(f"⚠️ Error on tweet: {e}")
                    continue

        except Exception as e:
            print(f"⚠️ Error on list {list_url}: {e}")
            
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