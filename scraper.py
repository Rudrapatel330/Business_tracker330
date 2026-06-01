"""
scraper.py — Core Playwright scraping engine for Google Maps.

Navigates to Google Maps, searches for a business type + city,
scrolls the sidebar to load all results, and extracts structured
data (Name, Phone, Address, Website, Rating, Reviews, Category)
from each listing.

Features:
  • playwright-stealth for anti-detection
  • Human-like random delays between actions
  • Realistic viewport & user-agent rotation
  • Progress callback for real-time UI updates
  • Returns a list[dict] of business records
"""

from __future__ import annotations

import asyncio
import random
import re
import logging
from typing import Callable, Optional

from playwright.async_api import async_playwright, Page, BrowserContext

# Optional stealth import — gracefully degrade if not installed
try:
    from playwright_stealth import stealth_async  # type: ignore
except ImportError:
    stealth_async = None  # type: ignore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) "
    "Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]

VIEWPORT_SIZES = [
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
]

GOOGLE_MAPS_URL = "https://www.google.com/maps"

# Maximum number of sidebar scroll attempts before giving up
MAX_SCROLL_ATTEMPTS = 80

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _human_delay(lo: float = 0.4, hi: float = 1.2) -> None:
    """Sleep for a random human-like duration."""
    await asyncio.sleep(random.uniform(lo, hi))


async def _slow_type(page: Page, selector: str, text: str) -> None:
    """Type text character-by-character with random inter-key delays."""
    await page.click(selector)
    await _human_delay(0.2, 0.5)
    for ch in text:
        await page.keyboard.type(ch, delay=random.randint(30, 120))
    await _human_delay(0.3, 0.6)


def _clean(text: str | None) -> str:
    """Strip and normalise whitespace in extracted text."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip())


# ---------------------------------------------------------------------------
# Browser / context factory
# ---------------------------------------------------------------------------

async def _create_context(playwright) -> tuple:
    """Launch Chromium and return (browser, context, page)."""
    viewport = random.choice(VIEWPORT_SIZES)
    user_agent = random.choice(USER_AGENTS)

    browser = await playwright.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--start-maximized",
        ],
    )

    context = await browser.new_context(
        viewport=viewport,
        user_agent=user_agent,
        locale="en-US",
        timezone_id="America/New_York",
        permissions=["geolocation"],
    )

    page = await context.new_page()
    return browser, context, page


# ---------------------------------------------------------------------------
# Consent / cookie banner dismissal
# ---------------------------------------------------------------------------

async def _dismiss_consent(page: Page) -> None:
    """Try to dismiss Google cookie-consent or 'Before you continue' dialogs."""
    selectors = [
        'button:has-text("Accept all")',
        'button:has-text("Reject all")',
        'button:has-text("I agree")',
        'form[action*="consent"] button',
        '[aria-label="Accept all"]',
        'button:has-text("Alle akzeptieren")',
        'button:has-text("Tout accepter")',
    ]
    for _ in range(3):  # Try multiple times — consent can take a moment to render
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    await _human_delay(1.0, 2.0)
                    return
            except Exception:
                continue
        await _human_delay(0.5, 1.0)


# ---------------------------------------------------------------------------
# Sidebar scrolling — loads all result cards
# ---------------------------------------------------------------------------

async def _scroll_sidebar(page: Page, on_progress: Optional[Callable] = None) -> None:
    """
    Scroll the results sidebar until the "end of list" marker appears
    or we hit MAX_SCROLL_ATTEMPTS.
    Uses multiple scroll strategies to ensure Google Maps' lazy loader triggers.
    """
    feed_selector = 'div[role="feed"]'

    try:
        await page.wait_for_selector(feed_selector, timeout=10_000)
    except Exception:
        logger.warning("Could not find results feed container.")
        return

    last_count = 0
    stale_rounds = 0

    for attempt in range(MAX_SCROLL_ATTEMPTS):
        # Check for "end of list" text
        try:
            end_marker = page.locator('p.fontBodyMedium span:has-text("You\'ve reached the end of the list")')
            if await end_marker.count() > 0:
                logger.info("Reached end of results list.")
                break
        except Exception:
            pass

        # --- Multi-strategy scrolling ---
        strategy = attempt % 3  # Rotate between 3 strategies

        try:
            if strategy == 0:
                # Strategy 1: JavaScript scrollTop
                await page.evaluate("""
                    const feed = document.querySelector('div[role="feed"]');
                    if (feed) feed.scrollTop = feed.scrollHeight;
                """)
            elif strategy == 1:
                # Strategy 2: Mouse wheel over the feed
                await page.locator(feed_selector).hover(timeout=2000)
                await page.mouse.wheel(0, 5000)
            else:
                # Strategy 3: Focus feed and press End key
                await page.locator(feed_selector).click(timeout=2000)
                await page.keyboard.press("End")
        except Exception:
            pass

        # Wait for new results to load (Google Maps needs time on slow networks)
        await _human_delay(2.5, 4.0)

        # Count actual result links (not skeleton loaders)
        current_count = await page.locator('div[role="feed"] a[href*="/maps/place/"]').count()

        if on_progress:
            on_progress({"phase": "scrolling", "results_loaded": current_count})

        if current_count == last_count:
            stale_rounds += 1
            # After 5 stale rounds, try one big "shake" scroll before giving up
            if stale_rounds == 5:
                logger.info("Results stalled at %d. Trying aggressive re-scroll...", current_count)
                try:
                    await page.evaluate("""
                        const feed = document.querySelector('div[role="feed"]');
                        if (feed) {
                            feed.scrollTop = 0;
                            setTimeout(() => { feed.scrollTop = feed.scrollHeight; }, 500);
                        }
                    """)
                    await _human_delay(3.0, 5.0)
                except Exception:
                    pass
            if stale_rounds >= 15:
                logger.info("No new results after %d scroll attempts — stopping.", stale_rounds)
                break
        else:
            stale_rounds = 0
            last_count = current_count

    logger.info("Finished scrolling. ~%d result links visible.", last_count)


# ---------------------------------------------------------------------------
# Data extraction — from a single listing panel
# ---------------------------------------------------------------------------

async def _extract_listing_from_panel(page: Page) -> dict:
    """
    Extract business details from the currently open listing side-panel.
    Returns a dict with keys:
      name, phone, address, website, rating, reviews, category
    """
    data: dict = {
        "name": "",
        "phone": "",
        "address": "",
        "website": "",
        "rating": "",
        "reviews": "",
        "category": "",
    }

    # --- Name ---
    try:
        name_el = page.locator('h1.DUwDvf, h1.fontHeadlineLarge').first
        data["name"] = _clean(await name_el.inner_text(timeout=3000))
    except Exception:
        pass

    # --- Rating ---
    try:
        rating_el = page.locator('div.F7nice span[aria-hidden="true"]').first
        data["rating"] = _clean(await rating_el.inner_text(timeout=2000))
    except Exception:
        pass

    # --- Reviews count ---
    try:
        reviews_el = page.locator('div.F7nice span[aria-label*="reviews"]').first
        label = await reviews_el.get_attribute("aria-label", timeout=2000) or ""
        match = re.search(r"([\d,]+)", label)
        if match:
            data["reviews"] = match.group(1).replace(",", "")
    except Exception:
        pass

    # --- Category ---
    try:
        cat_el = page.locator('button.DkEaL, span.DkEaL').first
        data["category"] = _clean(await cat_el.inner_text(timeout=2000))
    except Exception:
        pass

    # --- Address, Phone, Website from info buttons ---
    info_buttons = page.locator('button[data-item-id]')
    count = await info_buttons.count()

    for i in range(count):
        btn = info_buttons.nth(i)
        try:
            item_id = await btn.get_attribute("data-item-id", timeout=1000) or ""
            aria_label = await btn.get_attribute("aria-label", timeout=1000) or ""

            if item_id.startswith("address") or "address" in item_id:
                data["address"] = _clean(aria_label.replace("Address:", "").strip())
            elif item_id.startswith("phone") or item_id.startswith("tel"):
                data["phone"] = _clean(aria_label.replace("Phone:", "").strip())
            elif item_id == "authority":
                data["website"] = _clean(aria_label.replace("Website:", "").strip())
        except Exception:
            continue

    # Fallback: try to grab address from the info section text
    if not data["address"]:
        try:
            addr_el = page.locator('[data-item-id="address"] .Io6YTe, [data-item-id^="address"] .Io6YTe').first
            data["address"] = _clean(await addr_el.inner_text(timeout=2000))
        except Exception:
            pass

    # Fallback: phone
    if not data["phone"]:
        try:
            phone_el = page.locator(
                '[data-item-id^="phone"] .Io6YTe, '
                '[data-item-id^="tel"] .Io6YTe'
            ).first
            data["phone"] = _clean(await phone_el.inner_text(timeout=2000))
        except Exception:
            pass

    # Fallback: website
    if not data["website"]:
        try:
            web_el = page.locator('[data-item-id="authority"] .Io6YTe').first
            data["website"] = _clean(await web_el.inner_text(timeout=2000))
        except Exception:
            pass

    return data


# ---------------------------------------------------------------------------
# Extract data from the result cards (quick extraction without clicking)
# ---------------------------------------------------------------------------

async def _extract_from_cards(page: Page, on_progress: Optional[Callable] = None) -> list[dict]:
    """
    Click each result card, extract details from the listing panel.
    We visit each profile to ensure we get Phone, Website, and accurate Address,
    but we do it rapidly to save time.
    """
    results: list[dict] = []

    # Collect all result links in the feed
    links = page.locator('div[role="feed"] a[href*="/maps/place/"]')
    total = await links.count()
    logger.info("Found %d listing links to process.", total)

    if total == 0:
        return results

    # De-duplicate hrefs
    seen_hrefs: set[str] = set()
    href_list: list[str] = []
    for i in range(total):
        try:
            href = await links.nth(i).get_attribute("href", timeout=2000) or ""
            if href and href not in seen_hrefs:
                seen_hrefs.add(href)
                href_list.append(href)
        except Exception:
            continue

    total_unique = len(href_list)
    logger.info("Unique listings to scrape: %d", total_unique)

    for idx, href in enumerate(href_list):
        try:
            if on_progress:
                on_progress({
                    "phase": "extracting",
                    "current": idx + 1,
                    "total": total_unique,
                })

            # Navigate directly to the listing URL
            await page.goto(href, wait_until="domcontentloaded", timeout=15_000)
            
            # Wait for the listing panel to appear (fast fail if not)
            try:
                await page.wait_for_selector('h1.DUwDvf, h1.fontHeadlineLarge', timeout=4000)
            except Exception:
                logger.warning("Listing panel didn't load for link %d, skipping.", idx)
                continue

            data = await _extract_listing_from_panel(page)
            data["profile_link"] = href  # Add the profile link!

            if data["name"]:
                results.append(data)
                logger.info(
                    "[%d/%d] Extracted: %s", idx + 1, total_unique, data["name"]
                )
            else:
                logger.warning("[%d/%d] No name found — skipping.", idx + 1, total_unique)

        except Exception as exc:
            logger.error("[%d/%d] Error processing listing: %s", idx + 1, total_unique, exc)
            continue

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def scrape_google_maps(
    business_type: str,
    location: str,
    on_progress: Optional[Callable] = None,
) -> list[dict]:
    """
    Main entry point.  Scrapes Google Maps for *business_type* in *location*.

    Parameters
    ----------
    business_type : str
        e.g. "restaurants", "dentists", "plumbers"
    location : str
        e.g. "New York", "Los Angeles, CA"
    on_progress : callable, optional
        Called with a dict describing current progress, e.g.
        {"phase": "scrolling", "results_loaded": 42}

    Returns
    -------
    list[dict]
        Each dict has keys: name, phone, address, website, rating, reviews, category
    """
    query = f"{business_type} in {location}"
    logger.info("Starting scrape for: %s", query)

    if on_progress:
        on_progress({"phase": "starting", "query": query})

    async with async_playwright() as pw:
        browser, context, page = await _create_context(pw)

        try:
            # 1. Navigate to Google Maps
            if on_progress:
                on_progress({"phase": "navigating"})

            await page.goto(GOOGLE_MAPS_URL, wait_until="domcontentloaded", timeout=45_000)
            await _human_delay(3.0, 5.0)

            # 2. Dismiss any consent dialogs
            await _dismiss_consent(page)
            await _human_delay(1.0, 2.0)

            # 3. Take a screenshot for debugging (saved temporarily)
            try:
                await page.screenshot(path="debug_screenshot.png")
                logger.info("Debug screenshot saved to debug_screenshot.png")
            except Exception:
                pass

            if on_progress:
                on_progress({"phase": "searching", "query": query})

            search_box_selectors = [
                'input#searchboxinput',
                'input[name="q"]',
                'input[aria-label="Search Google Maps"]',
                'input[placeholder="Search Google Maps"]'
            ]
            
            search_box = None
            for sel in search_box_selectors:
                try:
                    if await page.locator(sel).count() > 0:
                        search_box = sel
                        break
                except Exception:
                    continue

            if not search_box:
                logger.error("Search box not found — Google Maps may have changed its UI.")
                return []

            await _slow_type(page, search_box, query)
            await _human_delay(0.3, 0.8)

            # Press Enter to search
            await page.keyboard.press("Enter")
            await _human_delay(4.0, 6.0)

            # 5. Wait for results feed to appear
            try:
                await page.wait_for_selector(
                    'div[role="feed"]', timeout=20_000
                )
            except Exception:
                logger.warning("Results feed did not appear — may be a single result or no results.")
                # Check if we landed directly on a single listing
                try:
                    await page.wait_for_selector('h1.DUwDvf, h1.fontHeadlineLarge', timeout=5000)
                    data = await _extract_listing_from_panel(page)
                    if data["name"]:
                        return [data]
                except Exception:
                    pass

                # Save a debug screenshot and log page details
                try:
                    await page.screenshot(path="debug_screenshot.png")
                    logger.info("Debug screenshot saved to debug_screenshot.png")
                except Exception:
                    pass
                title = await page.title()
                logger.error("Page title: %s | URL: %s", title, page.url)
                return []

            # 6. Scroll sidebar to load all results
            if on_progress:
                on_progress({"phase": "scrolling", "results_loaded": 0})

            await _scroll_sidebar(page, on_progress)

            # 7. Extract data from each listing
            if on_progress:
                on_progress({"phase": "extracting", "current": 0, "total": 0})

            results = await _extract_from_cards(page, on_progress)

            if on_progress:
                on_progress({
                    "phase": "complete",
                    "total_results": len(results),
                })

            logger.info("Scrape complete. %d businesses extracted.", len(results))
            return results

        except Exception as exc:
            logger.error("Scrape failed: %s", exc, exc_info=True)
            if on_progress:
                on_progress({"phase": "error", "message": str(exc)})
            return []

        finally:
            await context.close()
            await browser.close()


# ---------------------------------------------------------------------------
# CLI entry point for quick testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    btype = sys.argv[1] if len(sys.argv) > 1 else "restaurants"
    loc = sys.argv[2] if len(sys.argv) > 2 else "New York"

    def _print_progress(info: dict):
        phase = info.get("phase", "")
        if phase == "scrolling":
            print(f"  ↻ Scrolling... {info.get('results_loaded', '?')} results loaded", end="\r")
        elif phase == "extracting":
            print(f"  ⚙ Extracting {info.get('current', '?')}/{info.get('total', '?')}", end="\r")
        elif phase == "complete":
            print(f"\n  ✓ Done! {info.get('total_results', 0)} businesses found.")
        else:
            print(f"  [{phase}] {info}")

    data = asyncio.run(scrape_google_maps(btype, loc, on_progress=_print_progress))
    print(json.dumps(data, indent=2, ensure_ascii=False))
