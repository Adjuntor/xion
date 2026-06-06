RSS_UPDATE_INTERVAL = 60  # seconds
RSS_LAST_ARTICLE_RANGE = 7  # days
MAX_CACHE_SIZE = 5000 # Maximum number of links to store

DAILY_WEBHOOK_URL = "https://discord.com/api/webhooks/159999999999999928/asdasdgffhggdfhertsdzxvretwsvfxcrhvcxrtg" # Daily Message Webhook
DAILY_MESSAGE = "This is your daily warning." # Daily Message
DAILY_HOUR = 12 # Hour to send the message
DAILY_MINUTE = 0 # Minute to send the message
DAILY_SECOND = 0 # Second to send the message
DAILY_MICROSECOND = 0 # Microsecond to send the message


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
