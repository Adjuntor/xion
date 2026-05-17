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

                normalized = [self.normalize_link(link) for link in links]

                self.seen_links = set(normalized)
                self.seen_order = normalized

                print(f"[CACHE] Loaded {len(self.seen_links)} seen links")

            except Exception as e:
                print(f"[CACHE] Failed to load cache: {e}")
                self.seen_links = set()
                self.seen_order = []

        else:
            print("[CACHE] No cache file found, starting fresh")

    def save_cache(self):
        print(f"[CACHE] Saving {len(self.seen_links)} links to disk...")

        trimmed_order = self.seen_order[-config.MAX_CACHE_SIZE:]

        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(trimmed_order, f)

            self.seen_order = trimmed_order
            self.seen_links = set(trimmed_order)

            print(f"[CACHE] Save complete ({len(self.seen_links)} links)")

        except Exception as e:
            print(f"[CACHE] Failed to save cache: {e}")

    # =========================================================
    # HELPERS
    # =========================================================

    def normalize_link(self, link: str) -> str:
        """
        Normalize links to avoid duplicates:
        - remove query params
        - remove fragments
        - convert twitter/x -> fxtwitter
        - remove trailing slash
        """

        if not link:
            return ""

        try:
            parsed = urlparse(link)

            # Remove query + fragment
            cleaned = parsed._replace(query="", fragment="")

            fixed = urlunparse(cleaned)

            # Convert X/Twitter links
            fixed = (
                fixed.replace("https://x.com", "https://fxtwitter.com")
                .replace("http://x.com", "https://fxtwitter.com")
                .replace("https://twitter.com", "https://fxtwitter.com")
                .replace("http://twitter.com", "https://fxtwitter.com")
            )

            return fixed.rstrip("/")

        except Exception:
            return link.rstrip("/")

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
        """
        Validate article timestamp.
        """

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

        # Only process newest entries
        entries = feed.entries[:20]

        for entry in entries:
            total += 1

            raw_link = entry.get("link")

            if not raw_link:
                continue

            link = self.normalize_link(raw_link)

            # Already seen
            if link in self.seen_links:
                skipped_seen += 1
                continue

            # Validate timestamp
            published = self.get_entry_date(entry)

            if not self.is_recent(published):
                skipped_old += 1
                continue

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
    # MARK AS SEEN
    # =========================================================

    def mark_seen(self, link):
        if link in self.seen_links:
            return

        self.seen_links.add(link)
        self.seen_order.append(link)

        self._dirty = True

        # Trim oldest entries
        while len(self.seen_order) > config.MAX_CACHE_SIZE:
            oldest = self.seen_order.pop(0)
            self.seen_links.discard(oldest)

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

                    # MARK SEEN BEFORE WEBHOOK
                    # Prevent retries of old items
                    self.mark_seen(link)

                    await self.send_webhook(
                        session,
                        webhook_url,
                        link
                    )

                # Save cache only if changed
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
