"""Transparent rule-based labels for the unified content table."""
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "db" / "Zomato.db"
RULESET_VERSION = "rules-v1"

POSITIVE = ("good", "great", "excellent", "love", "fast", "easy", "best", "amazing")
NEGATIVE = ("bad", "worst", "poor", "late", "delay", "refund", "issue", "problem", "expensive")
THEMES = {
    "delivery delay": ("late", "delay", "slow", "waiting", "delivery time"),
    "refund and support": ("refund", "customer care", "support", "help", "cancel"),
    "price and charges": ("expensive", "price", "cost", "charge", "fee"),
    "food quality": ("cold", "stale", "quality", "taste", "fresh"),
    "app experience": ("app", "payment", "login", "bug", "crash"),
}


def has(text, word):
    return bool(re.search(r"\b" + re.escape(word) + r"\b", text))


def classify(text, rating):
    text = str(text or "").lower()
    score = sum(has(text, w) for w in POSITIVE) - sum(has(text, w) for w in NEGATIVE)
    if rating is not None:
        score += 1 if float(rating) >= 4 else -1 if float(rating) < 4 else 0
    sentiment = "positive" if score > 0 else "negative" if score < 0 else "neutral"
    found = [name for name, words in THEMES.items() if any(has(text, w) for w in words)]
    theme = ", ".join(found) if found else "other"
    rule_strength = 0.85 if abs(score) >= 2 else 0.65
    return sentiment, score, theme, rule_strength


def label_database(db_path=DB_PATH):
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as con:
        rows = con.execute("SELECT content_hash, title, content, rating FROM content_items").fetchall()
        for key, title, content, rating in rows:
            sentiment, score, theme, rule_strength = classify(f"{title or ''} {content or ''}", rating)
            con.execute(
                "INSERT OR REPLACE INTO content_labels VALUES (?,?,?,?,?,?,?)",
                (key, sentiment, score, theme, RULESET_VERSION, rule_strength, now),
            )
        con.commit()
    return {"items_seen": len(rows), "items_labelled": len(rows), "ruleset": RULESET_VERSION}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger.info(label_database())
