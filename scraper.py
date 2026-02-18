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

COOKIES_FILE = Path(".fb_session.json")


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

async def _save_cookies(context: BrowserContext) -> None:
    cookies = await context.cookies()
    COOKIES_FILE.write_text(json.dumps(cookies, indent=2))


async def _load_cookies(context: BrowserContext) -> bool:
    if not COOKIES_FILE.exists():
        return False
    try:
        cookies = json.loads(COOKIES_FILE.read_text())
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
    """Return an ElementHandle from either a Locator or an ElementHandle."""
    # ElementHandle has .evaluate() directly; Locator has .element_handle()
    if hasattr(el, 'element_handle'):
        return await el.element_handle()
    return el  # already an ElementHandle


async def _extract_reactions(post_el) -> int:
    """Extract total reaction count using JS evaluate on the element handle."""
    try:
        handle = await _get_handle(post_el)
        if not handle:
            return 0
        count = await handle.evaluate("""el => {
            // Strategy 1: sum counts from individual reaction emoji buttons.
            // FB renders each reaction type as a role=button inside a role=toolbar, with
            // aria-label like: "Lubię to!: 12 osób" / "Like: 12 people" / "Trzymaj się: 1 osoba"
            const toolbar = el.querySelector('[role="toolbar"]');
            if (toolbar) {
                const btns = toolbar.querySelectorAll('[role="button"]');
                let total = 0;
                for (const btn of btns) {
                    const label = btn.getAttribute('aria-label') || '';
                    // Match a number after ": " — covers both Polish and English labels
                    const m = label.match(/:\\s*(\\d[\\d\\s,.]*)/);
                    if (m) {
                        const n = parseInt(m[1].replace(/[\\s,.]/g, ''), 10);
                        if (!isNaN(n) && n > 0) total += n;
                    }
                }
                if (total > 0) return total;
            }
            // Strategy 2: span with aria-label containing reaction keywords
            // Facebook renders reaction summary as a span with aria-label like "42 people reacted"
            const reactionSelectors = [
                'span[aria-label*="reaction"]',
                'span[aria-label*="Reaction"]',
                'span[aria-label*="reakcj"]',
                'span[aria-label*="osób zareagowało"]',
                'span[aria-label*="people reacted"]',
                'span[aria-label*="zareagowało"]',
            ];
            for (const sel of reactionSelectors) {
                const el2 = el.querySelector(sel);
                if (el2) {
                    const label = el2.getAttribute('aria-label') || '';
                    const m = label.match(/(\\d[\\d\\s]*)/);
                    if (m) {
                        const n = parseInt(m[1].replace(/\\s/g, ''), 10);
                        if (!isNaN(n) && n > 0) return n;
                    }
                }
            }
            // Strategy 3: look for the reaction count number near the Like button area
            // In the real DOM, the reaction bar has a div[data-ad-rendering-role="like_button"]
            // and nearby there may be a span with just a number
            const likeArea = el.querySelector('[data-ad-rendering-role="like_button"]');
            if (likeArea) {
                // Walk up to the action bar container and look for a number span
                const bar = likeArea.closest('.x9f619');
                if (bar) {
                    const spans = bar.querySelectorAll('span');
                    for (const s of spans) {
                        const txt = (s.textContent || '').trim().replace(/[^0-9]/g, '');
                        if (/^\\d+$/.test(txt)) {
                            const n = parseInt(txt, 10);
                            if (n >= 1 && n <= 999999) return n;
                        }
                    }
                }
            }
            return 0;
        }""")
        return int(count) if count else 0
    except Exception:
        return 0


async def _extract_comment_count(post_el) -> int:
    """Extract comment count using JS evaluate on the element handle.

    In the real Facebook DOM the comment count appears as a role=button element
    containing a span with text like '11 komentarzy' or '3 comments'.
    """
    try:
        handle = await _get_handle(post_el)
        if not handle:
            return 0
        count = await handle.evaluate("""el => {
            // Pattern: "11 komentarzy", "3 komentarze", "1 komentarz", "5 comments"
            const pat = /^(\\d+)\\s*(komentarz|komentarze|komentarzy|comment|comments)$/i;

            // Look in role=button elements first (that's where FB puts the comment count link)
            const buttons = el.querySelectorAll('[role="button"] span, [role="link"] span');
            for (const s of buttons) {
                const txt = (s.textContent || '').trim();
                const m = txt.match(pat);
                if (m) return parseInt(m[1], 10);
            }

            // Fallback: scan all spans
            const allSpans = el.querySelectorAll('span');
            for (const s of allSpans) {
                const txt = (s.textContent || '').trim();
                const m = txt.match(pat);
                if (m) return parseInt(m[1], 10);
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
    scroll_wait_ms: int = 1500,
    per_post_timeout: float = 5.0,
    enrich_total_timeout: float = 60.0,
) -> list[dict]:
    posts: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="pl-PL",
        )
        page = await context.new_page()

        # Session
        logged_in = False
        if save_session and await _load_cookies(context):
            log("🍪 Loaded saved session, checking if still valid...")
            logged_in = await _is_logged_in(page)
            if logged_in:
                log("✅ Session still valid — skipping login!")
            else:
                log("⚠️ Saved session expired, logging in fresh...")

        if not logged_in:
            logged_in = await _do_login(page, email, password, log)
            if not logged_in:
                await browser.close()
                return []
            if save_session:
                await _save_cookies(context)
                log("💾 Session saved for next time.")

        # Navigate to group
        log(f"🌐 Navigating to group: {group_url}")
        try:
            await page.goto(group_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log(f"⚠️ Navigation warning (continuing): {e}")
        await page.wait_for_timeout(4000)

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
        # Get all article containers once (avoids repeated full-DOM queries)
        articles = await page.locator('div[role="article"]:has([data-ad-rendering-role="story_message"])').all()

        # Timeouts (from UI settings)
        PER_POST_TIMEOUT = per_post_timeout
        TOTAL_TIMEOUT    = enrich_total_timeout
        enrich_start = asyncio.get_event_loop().time()

        async def _get_article_for_story(story_el):
            """Walk up from story_el to find the enclosing article element."""
            try:
                handle = await story_el.element_handle()
                if not handle:
                    return story_el
                # Use JS to find the closest article ancestor
                article_handle = await handle.evaluate_handle(
                    "el => el.closest('div[role=\"article\"]') || el"
                )
                # Wrap back as a Playwright locator-like object via element_handle
                return article_handle
            except Exception:
                return story_el

        async def _enrich_one(text: str, story_el) -> dict:
            """Enrich a single post — wrapped so we can apply a timeout."""
            await _expand_see_more(story_el)
            full_text = (await story_el.inner_text()).strip() or text
            article = await _get_article_for_story(story_el)
            reactions = await _extract_reactions(article)
            comments  = await _extract_comment_count(article)
            return {"text": full_text, "reactions": reactions, "comments": comments}

        timed_out_total = False
        for i, (text, story_el) in enumerate(collected):
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
    return posts




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
    log_queue: "queue.Queue[str | None]",
    scroll_wait_ms: int = 1500,
    per_post_timeout: float = 5.0,
    enrich_total_timeout: float = 60.0,
) -> list[dict]:
    """
    Run the scraper in the current thread with its own event loop.
    Log messages are put into log_queue. Sends None sentinel when done.
    """
    def log(msg: str) -> None:
        log_queue.put(msg)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
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
                scroll_wait_ms=scroll_wait_ms,
                per_post_timeout=per_post_timeout,
                enrich_total_timeout=enrich_total_timeout,
            )
        )
        return result
    finally:
        loop.close()
        log_queue.put(None)  # sentinel
