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
    for selector in [
        '[data-testid="cookie-policy-manage-dialog-accept-button"]',
        'button:has-text("Allow all cookies")',
        'button:has-text("Zezwól na wszystkie pliki cookie")',
        'button:has-text("Accept All")',
        'button:has-text("Akceptuj wszystko")',
    ]:
        try:
            btn = page.locator(selector)
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(1000)
                break
        except Exception:
            pass

    log("✏️ Entering credentials...")
    await page.fill('input[name="email"]', email)
    await page.wait_for_timeout(600)
    await page.fill('input[name="pass"]', password)
    await page.wait_for_timeout(600)
    await page.click('button[name="login"]')

    log("⏳ Waiting for login to complete...")
    await page.wait_for_timeout(4000)

    url = page.url
    if "checkpoint" in url or "two_step" in url or "login/device" in url or "login/identify" in url:
        log("🔑 2FA detected! Please complete it manually in the browser window. Waiting up to 90 seconds...")
        for _ in range(90):
            await page.wait_for_timeout(1000)
            current = page.url
            if not any(x in current for x in ["checkpoint", "two_step", "login/device", "login/identify", "login"]):
                break
        else:
            log("❌ 2FA timeout — could not complete login.")
            return False

    if "login" in page.url or "recover" in page.url:
        log("❌ Login failed. Please check your credentials.")
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
             # Strategy 1: OG title
             og_title = await page.locator('meta[property="og:title"]').get_attribute("content")
             if og_title:
                 group_name = og_title.strip()
             
             if not group_name or "facebook" in group_name.lower():
                 # Strategy 2: h1
                 h1_text = await page.locator("h1").first.text_content()
                 if h1_text:
                     group_name = h1_text.strip()

             if not group_name or "facebook" in group_name.lower():
                 # Strategy 3: Look for a link to the group itself in the banner
                 # Many groups have the name in an anchor tag pointing to the group URL
                 # We look for an anchor whose href ends with the group ID/slug
                 # or contains the group ID/slug
                 try:
                     group_id_slug = group_url.rstrip("/").split("/")[-1]
                     # Find anchor with href containing this slug, but exclude post permalinks
                     # Usually the group name is in a large font or specific location
                     potential_name = await page.locator(f'a[href*="{group_id_slug}"][role="link"]').first.text_content()
                     if potential_name:
                         group_name = potential_name.strip()
                 except Exception:
                     pass

        except Exception:
            pass
        
        if group_name:
             # Clean up " | Facebook" if present
             group_name = group_name.replace(" | Facebook", "").replace("Facebook", "").strip()
        log(f"ℹ️ Group name: {group_name if group_name else 'Unknown'}")


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

        log(f"📜 Scrolling to collect up to {max_posts} posts...")

        seen_keys: set[str] = set()
        # Store (text, element_handle) for enrichment phase
        collected: list[tuple[str, object]] = []
        last_height = 0
        no_new_count = 0
        scroll_round = 0

        # ── Phase 1: Fast scroll — collect text only ──────────────────────
        while len(collected) < max_posts:
            if stop_event and stop_event.is_set():
                log("🛑 Scraping stopped by user.")
                break
            scroll_round += 1

            story_messages = await page.locator('[data-ad-rendering-role="story_message"]').all()

            new_this_round = 0
            for story_el in story_messages:
                if len(collected) >= max_posts:
                    break
                try:
                    text = (await story_el.inner_text()).strip()
                    if not text or len(text) < 15:
                        continue
                    # Normalize key: collapse whitespace + lowercase to catch
                    # truncated variants of the same post across scroll rounds
                    import re as _re
                    key = _re.sub(r'\s+', ' ', text).lower()[:200]
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    collected.append((text, story_el))
                    new_this_round += 1
                except Exception:
                    continue

            log(f"  → Round {scroll_round}: {new_this_round} new posts | Total: {len(collected)}/{max_posts}")

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(scroll_wait_ms)
            new_height = await page.evaluate("document.body.scrollHeight")

            if new_height <= last_height:
                no_new_count += 1
                log(f"  ⚠️ No new content (attempt {no_new_count}/3)")
                if no_new_count >= 3:
                    log("  ℹ️ Reached end of feed.")
                    break
            else:
                no_new_count = 0
            last_height = new_height

            if scroll_round >= 60:
                log("  ℹ️ Reached maximum scroll limit.")
                break

        log(f"✅ Collected {len(collected)} posts. Enriching with reactions & comments...")

        # ── Phase 2: Enrich — expand text + extract engagement ───────────
        # Timeouts (from UI settings)
        PER_POST_TIMEOUT = per_post_timeout
        TOTAL_TIMEOUT    = enrich_total_timeout
        enrich_start = asyncio.get_event_loop().time()

        async def _enrich_one(text: str, story_el) -> dict:
            """Enrich a single post — expand text and extract engagement.
            
            We pass story_el's ElementHandle to the extraction functions.
            The JS inside each function walks up to the article container,
            so we never need to resolve the article locator separately.
            """
            await _expand_see_more(story_el)
            full_text = (await story_el.inner_text()).strip() or text
            reactions = await _extract_reactions(story_el)
            comments  = await _extract_comment_count(story_el)
            return {"text": full_text, "reactions": reactions, "comments": comments}

        timed_out_total = False
        for i, (text, story_el) in enumerate(collected):
            if stop_event and stop_event.is_set():
                log("🛑 Scraping stopped by user during enrichment.")
                break

            # Check overall budget
            elapsed = asyncio.get_event_loop().time() - enrich_start
            if elapsed >= TOTAL_TIMEOUT:
                log(f"  ⏱️ Enrichment time limit reached ({TOTAL_TIMEOUT:.0f}s). "
                    f"Remaining {len(collected) - i} posts added without engagement data.")
                # Add remaining posts without enrichment
                for text2, _ in collected[i:]:
                    posts.append({"text": text2, "reactions": 0, "comments": 0})
                timed_out_total = True
                break

            try:
                result = await asyncio.wait_for(
                    _enrich_one(text, story_el),
                    timeout=PER_POST_TIMEOUT,
                )
                posts.append(result)
                # Debug: log first post's engagement to verify extraction works
                if i == 0:
                    log(f"  🔬 Post #1 engagement: reactions={result['reactions']}, comments={result['comments']}")
            except asyncio.TimeoutError:
                posts.append({"text": text, "reactions": 0, "comments": 0})
            except Exception as e:
                posts.append({"text": text, "reactions": 0, "comments": 0})


            # Progress log every 10 posts
            done = i + 1
            if done % 10 == 0 or done == len(collected):
                elapsed = asyncio.get_event_loop().time() - enrich_start
                log(f"  🔎 Enriched {done}/{len(collected)} posts... ({elapsed:.0f}s elapsed)")

        await browser.close()

    log(f"✅ Scraping complete. Total posts collected: {len(posts)}")
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
