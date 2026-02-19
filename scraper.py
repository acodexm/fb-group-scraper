"""
scraper.py — Playwright-based Facebook group post scraper.

Key design decisions:
  - Uses data-ad-rendering-role="story_message" to find post bodies (not comments)
  - Clicks "See more" / "Wyświetl więcej" to expand truncated text
  - Extracts reactions and comment count for engagement-based ranking
  - Runs in its own thread with a dedicated asyncio event loop
  - Communicates progress via queue.Queue for live Gradio streaming
"""

import asyncio
import json
import queue
from pathlib import Path
from typing import Callable

from playwright.async_api import async_playwright, Page, BrowserContext

# COOKIES_FILE = Path(".fb_session.json")  # Moved to arg


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

async def _save_cookies(context: BrowserContext, file_path: Path) -> None:
    cookies = await context.cookies()
    file_path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")


async def _load_cookies(context: BrowserContext, file_path: Path) -> bool:
    if not file_path.exists():
        return False
    try:
        cookies = json.loads(file_path.read_text(encoding="utf-8"))
        await context.add_cookies(cookies)
        return True
    except Exception:
        return False


async def _is_logged_in(page: Page) -> bool:
    try:
        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(2000)
        if await page.locator('input[name="email"]').count() > 0:
            return False
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def _do_login(page: Page, email: str, password: str, log: Callable) -> bool:
    log("🔐 Navigating to Facebook login page...")
    await page.goto("https://www.facebook.com/login", wait_until="domcontentloaded", timeout=20000)
    await page.wait_for_timeout(2000)

    # Accept cookie consent
    # Try various selectors for different regions/versions
    cookie_selectors = [
        '[data-testid="cookie-policy-manage-dialog-accept-button"]',
        'button[data-cookiebanner="accept_button"]',
        'button:has-text("Allow all cookies")',
        'button:has-text("Zezwól na wszystkie pliki cookie")',
        'button:has-text("Accept All")',
        'button:has-text("Akceptuj wszystko")',
        '[aria-label="Allow all cookies"]',
        '[aria-label="Zezwól na wszystkie pliki cookie"]',
        '[title="Allow all cookies"]',
        '[title="Zezwól na wszystkie pliki cookie"]',
        # Fallback for generic "Allow" in a dialog
        'div[role="dialog"] button:has-text("Zezwól")',
        'div[role="dialog"] button:has-text("Allow")',
    ]
    
    for selector in cookie_selectors:
        try:
            btn = page.locator(selector)
            count = await btn.count()
            if count > 0:
                # Iterate in case there are multiple (e.g. hidden ones), try likely visible one
                for i in range(count):
                    if await btn.nth(i).is_visible():
                        await btn.nth(i).click()
                        log("🍪 Cookie consent accepted.")
                        await page.wait_for_timeout(1000)
                        break
                break
        except Exception:
            pass

    log("✏️ Entering credentials...")
    await page.fill('input[name="email"]', email)
    await page.wait_for_timeout(600)
    await page.fill('input[name="pass"]', password)
    await page.wait_for_timeout(600)
    
    # Click login - try multiple selectors including Polish
    login_btn_selectors = [
        'button[name="login"]',
        'button:has-text("Log In")',
        'button:has-text("Zaloguj się")',
        'div[role="button"]:has-text("Zaloguj się")',
        '#loginbutton',
        '[data-testid="royal_login_button"]'
    ]
    
    clicked = False
    for sel in login_btn_selectors:
        try:
            if await page.locator(sel).count() > 0:
                if await page.locator(sel).first.is_visible():
                    await page.click(sel)
                    clicked = True
                    break
        except Exception:
            pass
            
    if not clicked:
        log("⚠️ Could not find explicit login button, trying Enter key...")
        await page.keyboard.press("Enter")

    log("⏳ Waiting for login/2FA redirect...")
    
    # Wait for navigation or URL change
    # Check repeatedly for 2FA indicators or success
    two_factor_detected = False
    
    for _ in range(15): # Check for 15 seconds
        await page.wait_for_timeout(1000)
        url = page.url.lower()
        if any(x in url for x in ["checkpoint", "challenge", "two_step", "login/device", "login/identify", "approval"]):
            two_factor_detected = True
            break
        # If we are effectively home (no login/recover/challenge in URL)
        if "facebook.com" in url and not any(x in url for x in ["login", "recover", "checkpoint", "challenge"]):
             # Double check existence of search or feed to be sure?
             # For now, URL check is usually enough.
             break

    if two_factor_detected:
        log("🔑 2FA/Checkpoint detected! Please approve in app or enter code. Waiting up to 90s...")
        for _ in range(90):
            await page.wait_for_timeout(1000)
            url = page.url.lower()
            if not any(x in url for x in ["checkpoint", "challenge", "two_step", "login/device", "login/identify", "approval"]):
                log("✅ 2FA passed!")
                break
        else:
            log("❌ 2FA timeout — could not complete login.")
            return False

    # Final check
    url = page.url.lower()
    if "login" in url or "recover" in url or "checkpoint" in url:
        log(f"❌ Login failed or stuck (URL: {url}). Please check credentials/2FA.")
        return False

    log("✅ Logged in successfully!")
    return True


# ---------------------------------------------------------------------------
# Post extraction
# ---------------------------------------------------------------------------

async def _expand_see_more(post_el) -> None:
    """Click 'See more' / 'Wyświetl więcej' inside a post element."""
    for label in ["See more", "Wyświetl więcej", "Więcej", "More"]:
        try:
            btn = post_el.locator(f'div[role="button"]:has-text("{label}"), span:has-text("{label}")')
            if await btn.count() > 0:
                await btn.first.click(timeout=2000)
                await post_el.page().wait_for_timeout(500)
                break
        except Exception:
            pass


async def _extract_post_text(post_el) -> str:
    """
    Extract the main text of a post using data-ad-rendering-role="story_message".
    Falls back to other known selectors.
    """
    selectors = [
        '[data-ad-rendering-role="story_message"]',
        '[data-ad-comet-preview="message"]',
        '[data-ad-preview="message"]',
    ]
    for sel in selectors:
        try:
            el = post_el.locator(sel).first
            if await el.count() > 0:
                text = (await el.inner_text()).strip()
                if text and len(text) > 10:
                    return text
        except Exception:
            pass
    return ""


async def _get_handle(el):
    """Return an ElementHandle from either a Playwright Locator or ElementHandle."""
    # Both Locator and ElementHandle have element_handle as an attribute,
    # so we distinguish by class name instead.
    cls = type(el).__name__
    if cls == "Locator":
        return await el.element_handle()
    # Already an ElementHandle (or JSHandle) — use directly
    return el


async def _extract_reactions(post_el) -> int:
    """Extract total reaction count using JS evaluate on the element handle."""
    try:
        handle = await _get_handle(post_el)
        if not handle:
            return 0
        count = await handle.evaluate("""el => {
            const root = el.closest('div[role="article"]') || el;
            
            // Strategy 1: Look for the specific reaction count text (e.g. "123", "1.2K")
            // usually next to the reaction icons.
            // Selectors for the reaction/comment bar usually involve x9f619 and other obfuscated classes,
            // so we look for the toolbar or specific aria-labels.
            
            // Try finding the toolbar first
            const toolbar = root.querySelector('[role="toolbar"]');
            if (toolbar) {
                 // The aggregate count is often in a div or span that is a sibling or child of the toolbar items
                 // But most reliably, it is in an aria-label of the button that opens the reaction list
                 const reactionBtn = toolbar.querySelector('[role="button"][aria-label*="ka"], [role="button"][aria-label*="ct"], [role="button"][aria-label*="osób"], [role="button"][aria-label*="people"]');
                 if (reactionBtn) {
                     const label = reactionBtn.getAttribute('aria-label');
                     const m = label.match(/(\\d+[\\d\\s,.]*)/);
                     if (m) {
                         // Fix 1.2K -> 1200 if necessary, but usually raw number for small counts
                         return parseInt(m[1].replace(/[\\s,.]/g, ''), 10);
                     }
                 }
            }

            // Strategy 2: Broad search for numbers in the "status" checks top-left of action bar
            // We look for the standard reaction icons container
            const reactionIcons = root.querySelectorAll('span[role="img"][aria-label], img[role="presentation"]');
            if (reactionIcons.length > 0) {
                 // The count is usually in a span next to these icons
                 // We find the container of icons and look for the text node/span next to it
                 // This is fuzzy but often works when aria-labels fail
                 const iconContainer = reactionIcons[0].closest('span')?.parentElement || reactionIcons[0].parentElement;
                 if (iconContainer) {
                     const txt = iconContainer.textContent.trim();
                     // Look for a standalone number at the start or end
                     const m = txt.match(/^(\\d+[\\d\\s,.]*[KkMm]?)/) || txt.match(/(\\d+[\\d\\s,.]*[KkMm]?)$/);
                     if (m) {
                         let valStr = m[1].replace(/,/g, '.').replace(/\\s/g, ''); # 1.2K
                         let mult = 1;
                         if (valStr.toLowerCase().includes('k')) { mult = 1000; valStr = valStr.replace(/[kK]/, ''); }
                         else if (valStr.toLowerCase().includes('m')) { mult = 1000000; valStr = valStr.replace(/[mM]/, ''); }
                         return Math.floor(parseFloat(valStr) * mult);
                     }
                 }
            }
            
            // Strategy 3: Just find the text that looks like a reaction count (digit) not followed by "comments"
            // This is risky but useful as fallback.
            // We restrict to the bottom section of the article
            const fullText = (root.innerText || root.textContent || '').trim();
            // Look for a line that starts with a number and is likely the reactions count
            // usually it appears before "comments" in the text dump
            // e.g. "12\n3 comments"
            const lines = fullText.split('\n').map(l => l.trim()).filter(l => l);
            for (let i = lines.length - 1; i >= 0; i--) {
                const line = lines[i];
                // If line is just a number (possibly with K/M), and the NEXT line or nearby line is "Like" or "Comment"
                // it is likely reactions.
                // Or if it matches "X others" or "X people"
                if (/^\\d+[\\d\\s,.]*[KkMm]?$/.test(line)) {
                     // Check if it looks like a reaction count.
                     // It should NOT be followed immediately by "comments" in the same line (handled by comment extractor)
                     // If it's pure number, we assume reactions if we are in the footer area.
                     // But date strings also look like numbers sometimes "2h".
                     if (!line.match(/\\d+[hmwdys]$/)) { 
                         let valStr = line.replace(/,/g, '.').replace(/\\s/g, '');
                         let mult = 1;
                         if (valStr.toLowerCase().includes('k')) { mult = 1000; valStr = valStr.replace(/[kK]/, ''); }
                         else if (valStr.toLowerCase().includes('m')) { mult = 1000000; valStr = valStr.replace(/[mM]/, ''); }
                         return Math.floor(parseFloat(valStr) * mult);
                     }
                }
            }

            return 0;
        }""")
        return int(count) if count else 0
    except Exception:
        return 0


async def _extract_comment_count(post_el) -> int:
    """Extract comment count using JS evaluate on the element handle."""
    try:
        handle = await _get_handle(post_el)
        if not handle:
            return 0
        count = await handle.evaluate("""el => {
            const root = el.closest('div[role="article"]') || el;
            
            // Regex for "2 comments", "16 komentarzy", "1 komentarz"
            const commentRegex = /(\\d+[\\d\\s,.]*[KkMm]?)\\s*(komentarz|comment)/i;
            
            // 1. Check all elements with role="button" or "link" as they are clickable
            const clickables = root.querySelectorAll('[role="button"], [role="link"]');
            for (const el of clickables) {
                const txt = el.textContent.trim();
                const m = txt.match(commentRegex);
                if (m) {
                     let valStr = m[1].replace(/,/g, '.').replace(/\\s/g, '');
                     let mult = 1;
                     if (valStr.toLowerCase().includes('k')) { mult = 1000; valStr = valStr.replace(/[kK]/, ''); }
                     else if (valStr.toLowerCase().includes('m')) { mult = 1000000; valStr = valStr.replace(/[mM]/, ''); }
                     return Math.floor(parseFloat(valStr) * mult);
                }
            }
            
            // 2. Fallback: Check text content of possible status info areas
                         return Math.floor(parseFloat(valStr) * mult);
                     }
                 }
            }
            
            return 0;
        }""")
        return int(count) if count else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Main async scrape function
# ---------------------------------------------------------------------------

async def _scrape_async(
    group_url: str,
    email: str,
    password: str,
    max_posts: int,
    save_session: bool,
    log: Callable,
    headless: bool,
    session_file_path: Path,
    scroll_wait_ms: int = 1500,
    per_post_timeout: float = 5.0,
    enrich_total_timeout: float = 60.0,
    stop_event: "threading.Event | None" = None,
) -> tuple[list[dict], str]:
    posts: list[dict] = []
    group_name = ""

    async with async_playwright() as pw:
        # Check stop before launch
        if stop_event and stop_event.is_set():
            log("🛑 Scraping stopped by user.")
            return [], ""
        
        browser = await pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="pl-PL",
        )

        # Load session if exists
        if save_session and session_file_path.exists():
            try:
                cookies = json.loads(session_file_path.read_text(encoding="utf-8"))
                await context.add_cookies(cookies)
                log("🍪 Loaded saved session, checking if still valid...")
            except Exception:
                log("⚠️ Failed to load cookies, starting fresh.")

        page = await context.new_page()

        # Check login status
        is_logged_in = False
        try:
            # Navigate to a known Facebook page to check login status
            await page.goto("https://www.facebook.com/", timeout=60000)
            # Look for something that indicates logged in state, e.g. account menu
            await page.wait_for_selector('div[role="navigation"]', timeout=5000)
            is_logged_in = True
            log("✅ Session still valid — skipping login!")
        except Exception:
            pass

        if not is_logged_in:
            if not email or not password:
                log("❌ Not logged in and no credentials provided. Exiting.")
                await browser.close()
                return [], ""
            
            log("🔑 Logging in...")
            logged_in = await _do_login(page, email, password, log)
            if not logged_in:
                await browser.close()
                return [], ""

        if save_session:
            cookies = await context.cookies()
            session_file_path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
            log("💾 Session saved for next time.")

        # Navigate to group
        log(f"🌐 Navigating to group: {group_url}")
        try:
            await page.goto(group_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"⚠️ Navigation warning (continuing): {e}")
        await page.wait_for_timeout(4000)

        # Attempt to extract group name
        try:
             # Strategy 1: Open Graph Title (most reliable)
             og_title = await page.locator('meta[property="og:title"]').get_attribute("content")
             if og_title:
                 group_name = og_title.strip()
             
             # Strategy 2: Determine if it's "Facebook" or just generic, then try H1
             if not group_name or group_name.lower() == "facebook":
                 h1 = page.locator("h1").first
                 if await h1.count() > 0:
                     group_name = (await h1.text_content()).strip()

             # Strategy 3: JSON-LD metadata
             if not group_name or group_name.lower() == "facebook":
                 try:
                    ld_json = await page.locator('script[type="application/ld+json"]').all_inner_texts()
                    for script in ld_json:
                        data = json.loads(script)
                        if "name" in data and ("Group" in data.get("@type", "") or "Place" in data.get("@type", "")):
                            group_name = data["name"]
                            break
                 except:
                     pass
        except Exception:
            pass
        
        if group_name:
             group_name = re.sub(r'\s*\|\s*Facebook$', '', group_name)
             group_name = re.sub(r'^Facebook\s*-\s*', '', group_name)
             group_name = group_name.strip()
             
        log(f"ℹ️ Group name: {group_name if group_name else 'Unknown'} (ID/Slug: {group_url.rstrip('/').split('/')[-1]})")


        # Dismiss popups
        for selector in [
            '[aria-label="Close"]',
            '[aria-label="Zamknij"]',
            'div[role="dialog"] button:has-text("Not Now")',
            'div[role="dialog"] button:has-text("Nie teraz")',
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    await btn.click(timeout=2000)
                    await page.wait_for_timeout(500)
            except Exception:
                pass

        log(f"📜 Scrolling to collect {max_posts} unique posts...")

        import hashlib
        import re as _re

        seen_hashes: set[str] = set()
        posts: list[dict] = []
        
        last_height = 0
        no_new_count = 0
        scroll_round = 0

        # We need a clean_text helper here for strict deduplication
        def _clean_for_hash(t: str) -> str:
            # Remove HTML tags (simple regex)
            t = _re.sub(r'<[^>]+>', '', t)
            # Remove whitespace etc
            t = _re.sub(r'\s+', ' ', t).lower()
            return t.strip()

        # ── Fast scroll & collect ─────────────────────────────────────────────
        while len(posts) < max_posts:
            if stop_event and stop_event.is_set():
                log("🛑 Scraping stopped by user.")
                break
            scroll_round += 1

            # Get all story messages
            story_messages = await page.locator('[data-ad-rendering-role="story_message"]').all()

            new_this_round = 0
            for story_el in story_messages:
                if len(posts) >= max_posts:
                    break
                
                try:
                    # Expand "See more" quickly if present (optional - might be slow?)
                    # If we don't, we get truncated text. For reliable LLM analysis, full text is better.
                    # But user wanted speed/no enrichment phase. 
                    # Let's try to grab text first; if it ends in "..." do we click?
                    # For now, let's JUST grab text to be fast.
                    
                    text = (await story_el.inner_text()).strip()
                    if not text: 
                        continue
                    
                    # Strict deduplication
                    norm = _clean_for_hash(text)
                    if not norm: 
                        continue

                    h = hashlib.md5(norm.encode("utf-8")).hexdigest()
                    if h in seen_hashes:
                        continue
                        
                    seen_hashes.add(h)
                    
                    # Store
                    # We create the post dict immediately.
                    # Note: We are NOT fetching comments/reactions to save time.
                    # User instructions: "remove enrichment part"
                    posts.append({
                        "text": text,
                        "reactions": 0,
                        "comments": 0
                    })
                    new_this_round += 1
                except Exception:
                    continue

            log(f"  → Round {scroll_round}: {new_this_round} new unique posts | Total: {len(posts)}/{max_posts}")

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(scroll_wait_ms)
            new_height = await page.evaluate("document.body.scrollHeight")

            if new_height <= last_height:
                no_new_count += 1
                log(f"  ⚠️ No new content (attempt {no_new_count}/3)")
                if no_new_count >= 5: # increased from 3
                    log("  ℹ️ Reached end of feed or scroll stuck.")
                    break
            else:
                no_new_count = 0
            last_height = new_height

            if scroll_round >= 100: # allow more rounds
                log("  ℹ️ Reached maximum scroll limit.")
                break

        await browser.close()

    log(f"✅ Scraping complete. Total unique posts collected: {len(posts)}")
    return posts, group_name




# ---------------------------------------------------------------------------
# Thread-safe public API
# ---------------------------------------------------------------------------

def scrape_group_threaded(
    group_url: str,
    email: str,
    password: str,
    max_posts: int,
    save_session: bool,
    headless: bool,
    session_file_path: Path,
    log_queue: "queue.Queue[str | None]",
    scroll_wait_ms: int = 1500,
    per_post_timeout: float = 5.0,
    enrich_total_timeout: float = 60.0,
    stop_event: "threading.Event | None" = None,
) -> tuple[list[dict], str]:  # Returns (posts, group_name)
    """
    Run the scraper in the current thread with its own event loop.
    Log messages are put into log_queue. Sends None sentinel when done.
    """
    def log(msg: str) -> None:
        log_queue.put(msg)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        try:
            result = loop.run_until_complete(
                _scrape_async(
                    group_url=group_url,
                    email=email,
                    password=password,
                    max_posts=max_posts,
                    save_session=save_session,
                    log=log,
                    headless=headless,
                    session_file_path=session_file_path,
                    scroll_wait_ms=scroll_wait_ms,
                    per_post_timeout=per_post_timeout,
                    enrich_total_timeout=enrich_total_timeout,
                    stop_event=stop_event,
                )
            )
            return result
        except Exception as e:
            log(f"❌ Critical error in scraper thread: {e}")
            return [], ""
    finally:
        loop.close()
        log_queue.put(None)  # sentinel
