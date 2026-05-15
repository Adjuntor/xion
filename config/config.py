RSS_UPDATE_INTERVAL = 60  # seconds
RSS_LAST_ARTICLE_RANGE = 7  # days
MAX_CACHE_SIZE = 5000 # Maximum number of links to store

RSS_FEEDS = [
    {
        "url": "https://example.com/rss",
        "webhook": "https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN"
    },
    {
        "url": "https://example2.com/rss",
        "webhook": "https://discord.com/api/webhooks/YOUR_WEBHOOK_ID2/YOUR_WEBHOOK_TOKEN2"
    },
]
