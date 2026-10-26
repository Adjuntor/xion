import os
import json
import time
import asyncio
import aiohttp
import feedparser

from urllib.parse import urlparse, urlunparse
from datetime import datetime, timezone, timedelta

import config.config as config

CACHE_FILE = "config/seen_articles.json"

# Extra days to keep a link in the cache beyond the recency filter
CACHE_MARGIN_DAYS = 2

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)


class Xion:

    def __init__(self):

        # {normalized_link: unix timestamp when first seen}
        # Dicts keep insertion order, so oldest entries are first.
        self.seen = {}

        self._dirty = False

        # Prevent concurrent duplicate processing
        self._seen_lock = asyncio.Lock()

        print("[INIT] Starting Xion RSS Webhook bot...")

        self.load_cache()

        print(f"[INIT] Loaded {len(self.seen)} cached links")

    # =========================================================
    # CACHE
    # =========================================================

    def load_cache(self):

        print("[CACHE] Loading cache file...")

        if not os.path.exists(CACHE_FILE):
            print("[CACHE] No cache file found, starting fresh")
            return

        try:

            with open(CACHE_FILE, "r") as f:
                data = json.load(f)

            now = time.time()

            if isinstance(data, list):

                # Old format (plain list, no timestamps).
                # Stamp everything "now"; it will age out normally.
                print("[CACHE] Migrating old list format")

                self.seen = {
                    self.normalize_link(link): now
                    for link in data
                    if link
                }

                self._dirty = True

            else:

                self.seen = {
                    self.normalize_link(link): float(ts)
                    for link, ts in data.items()
                    if link
                }

            # Drop empty keys that normalization may have produced
            self.seen.pop("", None)

            self.prune()

            print(f"[CACHE] Loaded {len(self.seen)} seen links")

        except Exception as e:

            print(f"[CACHE] Failed to load cache: {e}")

            self.seen = {}

    def prune(self):
        """
        Age-based pruning, with MAX_CACHE_SIZE as a hard safety cap.
        Retention is longer than the recency filter so a link can never
        leave the cache while it could still pass is_recent().
        """

        before = len(self.seen)

        retention = (config.RSS_LAST_ARTICLE_RANGE + CACHE_MARGIN_DAYS) * 86400
        cutoff = time.time() - retention

        self.seen = {
            link: ts
            for link, ts in self.seen.items()
            if ts >= cutoff
        }

        # Hard cap (keeps the newest entries)
        max_size = getattr(config, "MAX_CACHE_SIZE", None)

        if max_size and len(self.seen) > max_size:
            self.seen = dict(list(self.seen.items())[-max_size:])

        removed = before - len(self.seen)

        if removed:
            self._dirty = True
            print(f"[CACHE] Pruned {removed} old links")

    def save_cache(self):

        self.prune()

        tmp = CACHE_FILE + ".tmp"

        try:

            os.makedirs(os.path.dirname(CACHE_FILE) or ".", exist_ok=True)

            with open(tmp, "w") as f:
                json.dump(self.seen, f)

            # Atomic replace: a crash can't leave a half-written file
            os.replace(tmp, CACHE_FILE)

            print(f"[CACHE] Saved {len(self.seen)} links")

        except Exception as e:

            print(f"[CACHE] Failed to save cache: {e}")

    # =========================================================
    # HELPERS
    # =========================================================

    def normalize_link(self, link: str) -> str:
        """
        Normalize links:
        - force https
        - remove query params
        - remove fragments
        - remove trailing slash
        - lowercase hostname
        - remove www/mobile
        - normalize twitter/x URLs
        """

        if not link:
            return ""

        try:

            parsed = urlparse(link.strip())

            netloc = parsed.netloc.lower()

            # Remove prefixes
            for prefix in ("www.", "mobile."):

                if netloc.startswith(prefix):
                    netloc = netloc[len(prefix):]

            path = parsed.path.rstrip("/")

            # Normalize Twitter/X links
            if netloc in ("x.com", "twitter.com"):

                path = path.replace("/i/web/status/", "/status/")

                netloc = "fxtwitter.com"

            cleaned = parsed._replace(
                scheme="https",
                netloc=netloc,
                path=path,
                query="",
                fragment=""
            )

            return urlunparse(cleaned)

        except Exception:
            return link.strip().rstrip("/")

    def get_entry_identifier(self, entry):
        """
        Always use normalized link as stable identifier.
        RSS GUIDs are often inconsistent.
        """

        return self.normalize_link(entry.get("link", ""))

    def get_entry_date(self, entry):
        """
        Prefer published date.
        Fallback to updated date.
        """

        return (
            entry.get("published_parsed")
            or entry.get("updated_parsed")
        )

    def is_recent(self, published_parsed) -> bool:

        if not published_parsed:
            print("[FILTER] Missing publish date → rejected")
            return False

        try:

            published = datetime(
                *published_parsed[:6],
                tzinfo=timezone.utc
            )

        except Exception as e:

            print(f"[FILTER] Invalid publish date: {e}")
            return False

        now = datetime.now(timezone.utc)

        # Reject future timestamps
        if published > now + timedelta(minutes=5):

            print(f"[FILTER] Future article rejected ({published})")
            return False

        age = now - published

        if age <= timedelta(days=config.RSS_LAST_ARTICLE_RANGE):
            return True

        print(f"[FILTER] Old article rejected (age: {age})")

        return False

    # =========================================================
    # SEEN TRACKING
    # =========================================================

    async def is_seen(self, identifier):

        async with self._seen_lock:
            return identifier in self.seen

    async def mark_seen(self, identifier):

        async with self._seen_lock:

            if identifier in self.seen:
                return

            self.seen[identifier] = time.time()

            self._dirty = True

    # =========================================================
    # FETCH FEED
    # =========================================================

    async def fetch_feed(self, session, feed_url):

        print(f"[FETCH] Downloading RSS feed: {feed_url}")

        headers = {
            "User-Agent": "Mozilla/5.0 XionRSSBot/1.0"
        }

        try:

            async with session.get(
                feed_url,
                headers=headers,
                timeout=HTTP_TIMEOUT
            ) as resp:

                if resp.status != 200:

                    print(f"[FETCH] HTTP {resp.status}")

                    return None

                text = await resp.text()

                print(f"[FETCH] Received {len(text)} bytes")

                # feedparser is blocking; keep it off the event loop
                return await asyncio.to_thread(feedparser.parse, text)

        except Exception as e:

            print(f"[ERROR] Failed to fetch feed {feed_url}: {e}")

            return None

    # =========================================================
    # PROCESS FEED
    # =========================================================

    async def process_feed(self, session, rss_feed):

        url = rss_feed["url"]
        webhook_url = rss_feed["webhook"]

        print(f"[PROCESS] Processing feed: {url}")

        feed = await self.fetch_feed(session, url)

        if not feed:

            print(f"[PROCESS] Feed failed: {url}")

            return []

        results = []

        total = 0
        skipped_seen = 0
        skipped_old = 0
        skipped_undefined = 0

        # Sort newest first
        entries = sorted(
            feed.entries,
            key=lambda e: tuple(self.get_entry_date(e) or (0,)),
            reverse=True
        )[:20]

        for entry in entries:

            total += 1

            link = self.get_entry_identifier(entry)

            if not link:
                continue

            # =====================================================
            # Ignore posts from user "undefined"
            # =====================================================

            author = (
                entry.get("author")
                or entry.get("creator")
                or ""
            ).strip().lower()

            path_parts = urlparse(link).path.strip("/").split("/")

            username = path_parts[0].lower() if path_parts else ""

            if author == "undefined" or username == "undefined":

                skipped_undefined += 1

                print(f"[FILTER] Ignoring undefined user post: {link}")

                continue

            # =====================================================

            # Validate timestamp
            published = self.get_entry_date(entry)

            if not self.is_recent(published):

                skipped_old += 1
                continue

            # Check duplicate
            if await self.is_seen(link):

                skipped_seen += 1
                continue

            print(f"[NEW] Found new article: {link}")

            results.append((webhook_url, link, link))

        print(
            f"[SUMMARY] {url} → "
            f"total={total}, "
            f"new={len(results)}, "
            f"seen_skip={skipped_seen}, "
            f"old_skip={skipped_old}, "
            f"undefined_skip={skipped_undefined}"
        )

        return results

    # =========================================================
    # SEND WEBHOOK
    # =========================================================

    async def send_webhook(self, session, webhook_url, content, retries=2):

        print(f"[SEND] Sending to webhook: {content}")

        for attempt in range(retries + 1):

            try:

                async with session.post(
                    webhook_url,
                    json={"content": content},
                    timeout=HTTP_TIMEOUT
                ) as resp:

                    if resp.status in (200, 204):

                        print("[SEND] Success")

                        return True

                    # Rate limited: wait as long as the server asks
                    if resp.status == 429 and attempt < retries:

                        try:
                            data = await resp.json()
                            wait = float(data.get("retry_after", 1))
                        except Exception:
                            wait = 1.0

                        print(f"[SEND] Rate limited, waiting {wait}s")

                        await asyncio.sleep(wait)

                        continue

                    text = await resp.text()

                    print(f"[SEND] Failed HTTP {resp.status}: {text}")

                    return False

            except Exception as e:

                print(f"[ERROR] Webhook error: {e}")

                return False

        return False

    # =========================================================
    # DAILY WEBHOOK SCHEDULER
    # =========================================================

    async def daily_webhook_loop(self):

        print("[DAILY] Scheduler started")

        while True:

            now = datetime.now()

            next_run = now.replace(
                hour=config.DAILY_HOUR,
                minute=config.DAILY_MINUTE,
                second=config.DAILY_SECOND,
                microsecond=config.DAILY_MICROSECOND
            )

            if now >= next_run:
                next_run += timedelta(days=1)

            sleep_time = (next_run - now).total_seconds()

            print(
                f"[DAILY] Next run: "
                f"{next_run.strftime('%Y-%m-%d %H:%M:%S')}"
            )

            await asyncio.sleep(sleep_time)

            try:

                async with aiohttp.ClientSession() as session:

                    message = (
                        f"{config.DAILY_MESSAGE}\n"
                        f"Date: {datetime.now().strftime('%Y-%m-%d')}"
                    )

                    success = await self.send_webhook(
                        session,
                        config.DAILY_WEBHOOK_URL,
                        message
                    )

                    if success:
                        print("[DAILY] Message sent")

                    else:
                        print("[DAILY] Message failed")

            except Exception as e:

                print(f"[DAILY] Error: {e}")

    # =========================================================
    # MAIN LOOP
    # =========================================================

    async def rss_cycle(self):

        async with aiohttp.ClientSession() as session:

            print(f"[CYCLE] Processing {len(config.RSS_FEEDS)} feeds...")

            results = await asyncio.gather(
                *(
                    self.process_feed(session, feed)
                    for feed in config.RSS_FEEDS
                ),
                return_exceptions=True
            )

            messages = []
            queued = set()

            for feed, res in zip(config.RSS_FEEDS, results):

                if isinstance(res, Exception):
                    print(f"[ERROR] Feed {feed['url']} crashed: {res}")
                    continue

                for webhook_url, link, identifier in res:

                    # Same link from two feeds in one cycle: send once
                    if identifier in queued:
                        continue

                    queued.add(identifier)
                    messages.append((webhook_url, link, identifier))

            print(f"[CYCLE] Total new messages: {len(messages)}")

            for webhook_url, link, identifier in messages:

                success = await self.send_webhook(
                    session,
                    webhook_url,
                    link
                )

                if success:

                    await self.mark_seen(identifier)

                else:

                    print(f"[WARN] Failed to send: {link}")

        # Expire old entries even on quiet cycles
        self.prune()

        if self._dirty:

            self.save_cache()

            self._dirty = False

        else:
            print("[CACHE] No changes, skipping save")

    async def rss_loop(self):

        print("[RUN] RSS loop started")

        while True:

            start = datetime.now()

            print("\n==============================")
            print("[CYCLE] Starting new polling cycle")
            print("==============================")

            try:
                await self.rss_cycle()

            except Exception as e:
                # Never let one bad cycle kill the bot
                print(f"[ERROR] Cycle failed: {e}")

            elapsed = (datetime.now() - start).total_seconds()

            sleep_time = config.RSS_UPDATE_INTERVAL

            print(f"[CYCLE] Done in {elapsed:.2f}s")
            print(f"[SLEEP] Sleeping for {sleep_time}s...\n")

            await asyncio.sleep(sleep_time)

    # =========================================================
    # START TASKS
    # =========================================================

    async def run(self):

        print("[RUN] Starting tasks")

        await asyncio.gather(
            self.rss_loop(),
            self.daily_webhook_loop()
        )


if __name__ == "__main__":

    bot = Xion()

    asyncio.run(bot.run())
