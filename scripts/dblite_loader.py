"""Load dated raw JSON snapshots into the SQLite database."""
import json
import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "db" / "Zomato.db"
DATA_DIR = PROJECT_ROOT / "data" / "raw"
ALLOWED_NEWS_QUERIES = {"zomato", "zomato complaint"}


def allowed_news_query(value: str) -> bool:
    return str(value or "").strip().lower() in ALLOWED_NEWS_QUERIES

def hash_text(text: str) -> str:
    """Generate SHA256 content hash."""
    return hashlib.sha256(str(text or "").strip().lower().encode("utf-8")).hexdigest()[:16]

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    tables = {
        "google_news": "(id INTEGER PRIMARY KEY, article_id TEXT, title TEXT, url TEXT, date TEXT, source_name TEXT, author TEXT, search_query TEXT, crawled_at TEXT, raw_json TEXT)",
        "trustpilot": "(id INTEGER PRIMARY KEY, review_id TEXT, rating REAL, review_text TEXT, date TEXT, review_title TEXT, reviewer TEXT, verified INTEGER, review_url TEXT, crawled_at TEXT, raw_json TEXT)",
        "reddit_posts": "(id INTEGER PRIMARY KEY, post_id TEXT, title TEXT, url TEXT, author TEXT, content TEXT, subreddit TEXT, score INTEGER, num_comments INTEGER, created_at TEXT, crawled_at TEXT, raw_json TEXT)",
        "reddit_comments": "(id INTEGER PRIMARY KEY, comment_id TEXT, post_id TEXT, author TEXT, content TEXT, score INTEGER, created_at TEXT, crawled_at TEXT, raw_json TEXT)",
        "google_play": "(id INTEGER PRIMARY KEY, review_id TEXT, app_name TEXT, rating REAL, review_text TEXT, reviewer TEXT, version TEXT, thumbs_up_count INTEGER, helpful_count INTEGER, date TEXT, crawled_at TEXT, raw_json TEXT)",
        "google_news_query_tracking": "(id INTEGER PRIMARY KEY AUTOINCREMENT, article_id TEXT, search_query TEXT, article_date TEXT, crawled_at TEXT)",
        "content_items": "(content_hash TEXT PRIMARY KEY, content_type TEXT, title TEXT, content TEXT, rating REAL, score INTEGER, views INTEGER, likes INTEGER, author TEXT, created_at TEXT, published_date TEXT, crawled_at TEXT, url TEXT)",
        "source_tracking": "(id INTEGER PRIMARY KEY AUTOINCREMENT, content_hash TEXT, source TEXT, raw_id TEXT, crawled_at TEXT)",
        "content_labels": "(content_hash TEXT PRIMARY KEY, sentiment TEXT NOT NULL, sentiment_score INTEGER NOT NULL, theme TEXT NOT NULL, ruleset_version TEXT NOT NULL, rule_strength REAL NOT NULL, labelled_at TEXT NOT NULL)",
        "run_metrics": "(run_id TEXT, stage TEXT, status TEXT, started_at TEXT, finished_at TEXT, metrics_json TEXT, PRIMARY KEY (run_id, stage))",
    }

    old_labels = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_enrichment'").fetchone()
    new_labels = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_labels'").fetchone()
    if old_labels and not new_labels:
        c.execute("ALTER TABLE content_enrichment RENAME TO content_labels")
        c.execute("ALTER TABLE content_labels RENAME COLUMN model_version TO ruleset_version")
        c.execute("ALTER TABLE content_labels RENAME COLUMN confidence TO rule_strength")
        c.execute("ALTER TABLE content_labels RENAME COLUMN enriched_at TO labelled_at")

    for table_name, schema in tables.items():
        c.execute(f"CREATE TABLE IF NOT EXISTS {table_name} {schema}")

    c.execute("DELETE FROM source_tracking WHERE source = 'youtube'")
    c.execute("DELETE FROM content_items WHERE NOT EXISTS (SELECT 1 FROM source_tracking s WHERE s.content_hash = content_items.content_hash)")
    c.execute("DELETE FROM content_labels WHERE NOT EXISTS (SELECT 1 FROM content_items c2 WHERE c2.content_hash = content_labels.content_hash)")
    c.execute("DROP TABLE IF EXISTS youtube_videos")
    c.execute("DROP TABLE IF EXISTS youtube_comments")
    c.execute(
        "DELETE FROM google_news_query_tracking WHERE lower(trim(search_query)) NOT IN (?, ?)",
        tuple(sorted(ALLOWED_NEWS_QUERIES)),
    )

    for table, key in {
        "google_news": "article_id", "trustpilot": "review_id",
        "reddit_posts": "post_id", "reddit_comments": "comment_id",
        "google_play": "review_id",
    }.items():
        c.execute(f"DELETE FROM {table} WHERE id NOT IN (SELECT MIN(id) FROM {table} WHERE {key} IS NOT NULL GROUP BY {key}) AND {key} IS NOT NULL")
        c.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_{key} ON {table} ({key})")

    c.execute("DELETE FROM source_tracking WHERE id NOT IN (SELECT MIN(id) FROM source_tracking GROUP BY content_hash, source, raw_id)")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_source_tracking_item ON source_tracking (content_hash, source, raw_id)")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_google_news_query ON google_news_query_tracking (article_id, search_query)")
    conn.commit()
    return conn

def load_json_files(conn):
    c = conn.cursor()
    
    for json_file in DATA_DIR.glob("google_news/*/articles.json"):
        with open(json_file, "r", encoding="utf-8", errors="replace") as f:
            articles = json.load(f)
            for art in articles:
                if allowed_news_query(art.get("search_query")):
                    c.execute(
                        "INSERT OR IGNORE INTO google_news_query_tracking (article_id, search_query, article_date, crawled_at) VALUES (?,?,?,?)",
                        (art.get("article_id"), art.get("search_query", ""), art.get("date", ""), art.get("crawled_at")),
                    )
                c.execute(
                    "INSERT OR IGNORE INTO google_news (article_id, title, url, date, source_name, author, search_query, crawled_at, raw_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    (art.get("article_id"), art.get("title"), art.get("url"), art.get("date"), 
                     art.get("source_name"), art.get("author"), art.get("search_query"), 
                     art.get("crawled_at"), json.dumps(art))
                )
                chash = hash_text(art.get("title", "") + art.get("url", ""))
                c.execute(
                    "INSERT OR IGNORE INTO content_items (content_hash, content_type, title, content, rating, score, views, likes, author, created_at, published_date, crawled_at, url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (chash, "article", art.get("title", ""), art.get("title", ""), None, None, None, None, art.get("source_name") or art.get("author") or "Google News", art.get("date"), art.get("date"), art.get("crawled_at"), art.get("url"))
                )
                c.execute(
                    "INSERT OR IGNORE INTO source_tracking (content_hash, source, raw_id, crawled_at) VALUES (?,?,?,?)",
                    (chash, "google_news", str(art.get("article_id")), art.get("crawled_at"))
                )
    
    for json_file in DATA_DIR.glob("google_news/*/query_matches.json"):
        with open(json_file, "r", encoding="utf-8", errors="replace") as f:
            for match in json.load(f):
                if not allowed_news_query(match.get("search_query")):
                    continue
                c.execute(
                    "INSERT OR IGNORE INTO google_news_query_tracking (article_id, search_query, article_date, crawled_at) VALUES (?,?,?,?)",
                    (match.get("article_id"), match.get("search_query", ""), match.get("article_date", ""), match.get("crawled_at")),
                )

    for json_file in DATA_DIR.glob("trustpilot/*/reviews.json"):
        with open(json_file, "r", encoding="utf-8", errors="replace") as f:
            reviews = json.load(f)
            for rev in reviews:
                c.execute(
                    "INSERT OR IGNORE INTO trustpilot (review_id, rating, review_text, date, review_title, reviewer, verified, review_url, crawled_at, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (rev.get("review_id"), rev.get("rating"), rev.get("review_text"), rev.get("date"),
                     rev.get("review_title"), rev.get("reviewer"), int(rev.get("verified", 0)), 
                     rev.get("review_url"), rev.get("crawled_at"), json.dumps(rev))
                )
                chash = hash_text(rev.get("review_id", "") + (rev.get("review_title") or "") + (rev.get("review_text") or ""))
                c.execute(
                    "INSERT OR IGNORE INTO content_items (content_hash, content_type, title, content, rating, score, views, likes, author, created_at, published_date, crawled_at, url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (chash, "review", rev.get("review_title", ""), rev.get("review_text", ""), rev.get("rating"), None, None, None, rev.get("reviewer") or "Trustpilot Reviewer", rev.get("date"), rev.get("date"), rev.get("crawled_at"), rev.get("review_url"))
                )
                c.execute(
                    "INSERT OR IGNORE INTO source_tracking (content_hash, source, raw_id, crawled_at) VALUES (?,?,?,?)",
                    (chash, "trustpilot", str(rev.get("review_id")), rev.get("crawled_at"))
                )
    
    for json_file in DATA_DIR.glob("reddit/*/posts.json"):
        with open(json_file, "r", encoding="utf-8", errors="replace") as f:
            posts = json.load(f)
            for post in posts:
                c.execute(
                    "INSERT OR IGNORE INTO reddit_posts (post_id, title, url, author, content, subreddit, score, num_comments, created_at, crawled_at, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (post.get("post_id"), post.get("title"), post.get("url"), post.get("author"),
                     post.get("text", post.get("content", "")), post.get("subreddit"), post.get("score", 0), 
                     post.get("num_comments", 0), post.get("created_at"), post.get("crawled_at"), json.dumps(post))
                )
                chash = hash_text(post.get("post_id", "") + post.get("title", ""))
                c.execute(
                    "INSERT OR IGNORE INTO content_items (content_hash, content_type, title, content, rating, score, views, likes, author, created_at, published_date, crawled_at, url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (chash, "post", post.get("title", ""), post.get("text", post.get("title", "")), None, post.get("score", 0), None, post.get("score", 0), post.get("author") or "Reddit User", str(post.get("created_at")), str(post.get("created_at")), post.get("crawled_at"), post.get("url"))
                )
                c.execute(
                    "INSERT OR IGNORE INTO source_tracking (content_hash, source, raw_id, crawled_at) VALUES (?,?,?,?)",
                    (chash, "reddit", str(post.get("post_id")), post.get("crawled_at"))
                )
    
    for json_file in DATA_DIR.glob("reddit/*/comments.json"):
        with open(json_file, "r", encoding="utf-8", errors="replace") as f:
            comments = json.load(f)
            for com in comments:
                c.execute(
                    "INSERT OR IGNORE INTO reddit_comments (comment_id, post_id, author, content, score, created_at, crawled_at, raw_json) VALUES (?,?,?,?,?,?,?,?)",
                    (com.get("comment_id"), com.get("post_id"), com.get("author"), com.get("text", com.get("content", "")),
                     com.get("score", 0), com.get("created_at"), com.get("crawled_at"), json.dumps(com))
                )
                chash = hash_text(com.get("comment_id", "") + (com.get("text") or ""))
                c.execute(
                    "INSERT OR IGNORE INTO content_items (content_hash, content_type, title, content, rating, score, views, likes, author, created_at, published_date, crawled_at, url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (chash, "comment", "Reddit Comment", com.get("text", ""), None, com.get("score", 0), None, com.get("score", 0), com.get("author") or "Reddit User", str(com.get("created_at")), str(com.get("created_at")), com.get("crawled_at"), None)
                )
                c.execute(
                    "INSERT OR IGNORE INTO source_tracking (content_hash, source, raw_id, crawled_at) VALUES (?,?,?,?)",
                    (chash, "reddit", str(com.get("comment_id")), com.get("crawled_at"))
                )
    
    for json_file in DATA_DIR.glob("google_play/*/reviews.json"):
        with open(json_file, "r", encoding="utf-8", errors="replace") as f:
            reviews = json.load(f)
            for rev in reviews:
                thumbs = rev.get("thumbsUpCount", rev.get("thumbs_up_count", rev.get("helpful_count", 0)))
                version = rev.get("app_version", rev.get("version", ""))
                date_val = rev.get("review_date", rev.get("date", ""))
                c.execute(
                    "INSERT OR IGNORE INTO google_play (review_id, app_name, rating, review_text, reviewer, version, thumbs_up_count, helpful_count, date, crawled_at, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (rev.get("review_id"), rev.get("app_name", "Zomato"), rev.get("rating"), rev.get("review_text"),
                     rev.get("reviewer"), version, thumbs, thumbs,
                     date_val, rev.get("crawled_at"), json.dumps(rev))
                )
                chash = hash_text(rev.get("review_id", "") + (rev.get("review_text") or ""))
                c.execute(
                    "INSERT OR IGNORE INTO content_items (content_hash, content_type, title, content, rating, score, views, likes, author, created_at, published_date, crawled_at, url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (chash, "review", "Google Play Review", rev.get("review_text", ""), rev.get("rating"), thumbs, None, thumbs, rev.get("reviewer") or "Google User", str(date_val), str(date_val), rev.get("crawled_at"), None)
                )
                c.execute(
                    "INSERT OR IGNORE INTO source_tracking (content_hash, source, raw_id, crawled_at) VALUES (?,?,?,?)",
                    (chash, "google_play", str(rev.get("review_id")), rev.get("crawled_at"))
                )

    conn.commit()
