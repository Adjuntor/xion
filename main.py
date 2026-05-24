import os
import json
import asyncio
import aiohttp
import feedparser

from urllib.parse import urlparse, urlunparse
from datetime import datetime, timezone, timedelta

import config.config as config

CACHE_FILE = "config/seen_articles.json"


class Xion:
    def __init__(self):
        # Fast lookup
        self.seen_links = set()

        # Ordered cache for trimming
        self.seen_order = []

        self._dirty = False

        # Prevent concurrent duplicate processing
        self._seen_lock = asyncio.Lock()

        print("[INIT] Starting Xion RSS Webhook bot...")
        self.load_cache()
        print(f"[INIT] Loaded {len(self.seen_links)} cached links")

    # =========================================================
    # CACHE
    # =========================================================

    def load_cache(self):
        print("[CACHE] Loading cache file...")

        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    links = json.load(f)

                normalized = [
                    self.normalize_link(link)
                    for link in links
                    if link
                ]

                # Remove duplicates while preserving order
                deduped = list(dict.fromkeys(normalized))

                self.seen_links = set(deduped)
                self.seen_order = deduped

                print(f"[CACHE] Loaded {len(self.seen_links)} seen links")

            except Exception as e:
                print(f"[CACHE] Failed to load cache: {e}")
                self.seen_links = set()
                self.seen_order = []

        else:
            print("[CACHE] No cache file found, starting fresh")

    def save_cache(self):
        trimmed_order = self.seen_order[-config.MAX_CACHE_SIZE:]

        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(trimmed_order, f)

            self.seen_order = trimmed_order
            self.seen_links = set(trimmed_order)

            print(f"[CACHE] Saved {len(self.seen_links)} links")

        except Exception as e:
            print(f"[CACHE] Failed to save cache: {e}")

    # =========================================================
    # HELPERS
    # =========================================================

    def normalize_link(self, link: str) -> str:
        """
        Normalize links to avoid duplicates:
        - force https
        - remove query params
        - remove fragments
        - remove trailing slash
        - lowercase hostname
        - remove www
        - convert twitter/x -> fxtwitter
        """

        if not link:
            return ""

        try:
            parsed = urlparse(link.strip())

            netloc = parsed.netloc.lower()

            if netloc.startswith("www."):
                netloc = netloc[4:]

            cleaned = parsed._replace(
                scheme="https",
                netloc=netloc,
                query="",
                fragment=""
            )

            fixed = urlunparse(cleaned)

            fixed = (
                fixed.replace("x.com", "fxtwitter.com")
                     .replace("twitter.com", "fxtwitter.com")
            )

            return fixed.rstrip("/")

        except Exception:
            return link.rstrip("/")

    def get_entry_identifier(self, entry):
        """
        Prefer stable RSS GUID/id.
        Fallback to link.
        """

        raw = (
            entry.get("id")
            or entry.get("guid")
            or entry.get("link")
        )

        return self.normalize_link(raw)

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

    async def try_mark_seen(self, identifier):
        """
        Atomically:
        - check if seen
        - mark as seen

        Prevents race conditions between feeds.
        """

        async with self._seen_lock:

            if identifier in self.seen_links:
                return False

            self.seen_links.add(identifier)
            self.seen_order.append(identifier)

            self._dirty = True

            # Trim oldest entries
            while len(self.seen_order) > config.MAX_CACHE_SIZE:
                oldest = self.seen_order.pop(0)
                self.seen_links.discard(oldest)

            return True

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
                timeout=10
            ) as resp:

                if resp.status != 200:
                    print(f"[FETCH] HTTP {resp.status}")
                    return None

                text = await resp.text()

                print(f"[FETCH] Received {len(text)} bytes")

                return feedparser.parse(text)

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

        # Process newest entries only
        entries = feed.entries[:20]

        for entry in entries:
            total += 1

            raw_link = entry.get("link")

            if not raw_link:
                continue

            identifier = self.get_entry_identifier(entry)

            if not identifier:
                continue

            # Validate timestamp
            published = self.get_entry_date(entry)

            if not self.is_recent(published):
                skipped_old += 1
                continue

            # Atomic dedupe
            was_new = await self.try_mark_seen(identifier)

            if not was_new:
                skipped_seen += 1
                continue

            link = self.normalize_link(raw_link)

            print(f"[NEW] Found new article: {link}")

            results.append(
                (
                    webhook_url,
                    link,
                )
            )

        print(
            f"[SUMMARY] {url} → "
            f"total={total}, "
            f"new={len(results)}, "
            f"seen_skip={skipped_seen}, "
            f"old_skip={skipped_old}"
        )

        return results

    # =========================================================
    # SEND WEBHOOK
    # =========================================================

    async def send_webhook(self, session, webhook_url, content):
        print(f"[SEND] Sending to webhook: {content}")

        try:
            async with session.post(
                webhook_url,
                json={"content": content},
                timeout=10
            ) as resp:

                if resp.status in (200, 204):
                    print("[SEND] Success")
                    return True

                text = await resp.text()

                print(f"[SEND] Failed HTTP {resp.status}: {text}")

                return False

        except Exception as e:
            print(f"[ERROR] Webhook error: {e}")
            return False

    # =========================================================
    # MAIN LOOP
    # =========================================================

    async def run(self):
        print("[RUN] RSS loop started")

        while True:
            start = datetime.now()

            print("\n==============================")
            print("[CYCLE] Starting new polling cycle")
            print("==============================")

            async with aiohttp.ClientSession() as session:

                print(f"[CYCLE] Processing {len(config.RSS_FEEDS)} feeds...")

                tasks = [
                    self.process_feed(session, feed)
                    for feed in config.RSS_FEEDS
                ]

                results = await asyncio.gather(*tasks)

                messages = [
                    item
                    for sub in results
                    for item in sub
                ]

                print(f"[CYCLE] Total new messages: {len(messages)}")

                for webhook_url, link in messages:

                    success = await self.send_webhook(
                        session,
                        webhook_url,
                        link
                    )

                    if not success:
                        print(f"[WARN] Failed to send: {link}")

                # Save cache after cycle
                if self._dirty:
                    self.save_cache()
                    self._dirty = False
                else:
                    print("[CACHE] No changes, skipping save")

            elapsed = (datetime.now() - start).total_seconds()

            sleep_time = config.RSS_UPDATE_INTERVAL

            print(f"[CYCLE] Done in {elapsed:.2f}s")
            print(f"[SLEEP] Sleeping for {sleep_time}s...\n")

            await asyncio.sleep(sleep_time)


if __name__ == "__main__":
    bot = Xion()
    asyncio.run(bot.run())
