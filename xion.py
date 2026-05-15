import os
import json
import asyncio
import aiohttp
import feedparser
from datetime import datetime, timezone, timedelta

import config.config as config

CACHE_FILE = "config/seen_articles.json"

class Xion:
    def __init__(self):
        # Use set for fast lookups
        self.seen_links = set()
        # Keep an ordered list to trim oldest links
        self.seen_order = []
        self._dirty = False

        print("[INIT] Starting Xion RSS Webhook bot...")
        self.load_cache()
        print(f"[INIT] Loaded {len(self.seen_links)} cached links")

    # ---------------------------
    # CACHE
    # ---------------------------
    def load_cache(self):
        print("[CACHE] Loading cache file...")
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r") as f:
                links = json.load(f)
                self.seen_links = set(links)
                self.seen_order = list(links)
            print(f"[CACHE] Loaded {len(self.seen_links)} seen links")
        else:
            print("[CACHE] No cache file found, starting fresh")

    def save_cache(self):
        print(f"[CACHE] Saving {len(self.seen_links)} links to disk...")
        # Trim the list to MAX_CACHE_SIZE
        trimmed_order = self.seen_order[-config.MAX_CACHE_SIZE:]
        with open(CACHE_FILE, "w") as f:
            json.dump(trimmed_order, f)

        # Update in-memory structures
        self.seen_order = trimmed_order
        self.seen_links = set(trimmed_order)
        print(f"[CACHE] Save complete, trimmed to {len(self.seen_links)} links")

    # ---------------------------
    # HELPERS
    # ---------------------------
    def normalize_link(self, link: str) -> str:
        fixed = (
            link.replace("https://x.com", "https://fxtwitter.com")
                .replace("http://x.com", "https://fxtwitter.com")
                .replace("https://twitter.com", "https://fxtwitter.com")
                .replace("http://twitter.com", "https://fxtwitter.com")
        )
        if fixed != link:
            print("[NORMALIZE] Converted X/Twitter link → fxtwitter")
        return fixed

    def is_recent(self, published_parsed) -> bool:
        if not published_parsed:
            print("[FILTER] Missing publish date → rejected")
            return False
        published = datetime(*published_parsed[:6], tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - published
        if age <= timedelta(days=config.RSS_LAST_ARTICLE_RANGE):
            return True
        print(f"[FILTER] Old article rejected (age: {age})")
        return False

    # ---------------------------
    # FETCH FEED
    # ---------------------------
    async def fetch_feed(self, session, feed_url):
        print(f"[FETCH] Downloading RSS feed: {feed_url}")
        try:
            async with session.get(feed_url, timeout=10) as resp:
                text = await resp.text()
                print(f"[FETCH] Received {len(text)} bytes from {feed_url}")
                return feedparser.parse(text)
        except Exception as e:
            print(f"[ERROR] Failed to fetch feed {feed_url}: {e}")
            return None

    # ---------------------------
    # PROCESS ONE FEED
    # ---------------------------
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

        for entry in feed.entries:
            total += 1
            link = entry.get("link")
            if not link:
                continue
            if link in self.seen_links:
                skipped_seen += 1
                continue
            if not self.is_recent(entry.get("published_parsed")):
                skipped_old += 1
                continue

            fixed_link = self.normalize_link(link)
            print(f"[NEW] Found new article: {fixed_link}")
            results.append((webhook_url, fixed_link, link))

        print(
            f"[SUMMARY] {url} → total={total}, "
            f"new={len(results)}, seen_skip={skipped_seen}, old_skip={skipped_old}"
        )
        return results

    # ---------------------------
    # SEND WEBHOOK
    # ---------------------------
    async def send_webhook(self, session, webhook_url, content, original_link):
        print(f"[SEND] Sending to webhook: {content}")
        try:
            async with session.post(
                webhook_url,
                json={"content": content},
                timeout=10
            ) as resp:
                if resp.status in (200, 204):
                    print("[SEND] Success")
                    # Mark as seen
                    self.mark_seen(original_link)
                    return True
                else:
                    text = await resp.text()
                    print(f"[SEND] Failed HTTP {resp.status}: {text}")
                    return False
        except Exception as e:
            print(f"[ERROR] Webhook error: {e}")
            return False

    # ---------------------------
    # MARK LINK AS SEEN WITH TRIM
    # ---------------------------
    def mark_seen(self, link):
        if link not in self.seen_links:
            self.seen_links.add(link)
            self.seen_order.append(link)
            self._dirty = True
            # Trim if exceeded MAX_CACHE_SIZE
            while len(self.seen_order) > config.MAX_CACHE_SIZE:
                oldest = self.seen_order.pop(0)
                self.seen_links.discard(oldest)

    # ---------------------------
    # MAIN LOOP
    # ---------------------------
    async def run(self):
        print("[RUN] RSS loop started")
        while True:
            start = datetime.now()
            print("\n==============================")
            print("[CYCLE] Starting new polling cycle")
            print("==============================")

            async with aiohttp.ClientSession() as session:
                print(f"[CYCLE] Processing {len(config.RSS_FEEDS)} feeds...")
                tasks = [self.process_feed(session, feed) for feed in config.RSS_FEEDS]
                results = await asyncio.gather(*tasks)
                messages = [item for sub in results for item in sub]
                print(f"[CYCLE] Total new messages: {len(messages)}")

                for webhook_url, link, original_link in messages:
                    await self.send_webhook(session, webhook_url, link, original_link)

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
