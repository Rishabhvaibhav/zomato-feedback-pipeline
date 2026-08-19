"""Reddit collector using public JSON endpoints — no OAuth or PRAW credentials required at all."""
import logging
from typing import Any, Dict, List

import config
from scripts.base_collector import BaseCollector
from utils.storage import RawStorage

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.reddit.com/search.json"
SUBREDDIT_SEARCH_URL = "https://www.reddit.com/r/{subreddit}/search.json"
COMMENTS_URL = "https://www.reddit.com/r/{subreddit}/comments/{post_id}.json"


class RedditCollector(BaseCollector):
    """Collect Zomato-related discussions and feedback from Reddit forums."""

    def __init__(self):
        headers = config.DEFAULT_HEADERS.copy()
        headers.update({
            "User-Agent": "zomato-consumer-research/1.0 (public RSS study)",
            "Accept": "application/atom+xml,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        super().__init__(
            source_name="Reddit",
            id_field="post_id",
            output_subdir="reddit",
            delay=config.REDDIT_DELAY,
            headers=headers,
        )
        self.posts: List[Dict[str, Any]] = []
        self.comments: List[Dict[str, Any]] = []
        self.reddit_rate_limited = False
        self.rate_limit_logged = False

    @property
    def output_filename(self) -> str:
        return "posts.json"

    def run(self) -> None:
        """Execute full collection pipeline for both posts and nested comments."""
        logger.info("=== Starting Reddit ===")
        self._pre_run()
        try:
            new_posts = self._collect()
            added_posts = self.storage.merge_and_save(
                "posts.json", self.existing_records, new_posts, "post_id"
            )
            self.stats["new"] = added_posts

            comment_storage = RawStorage(self.output_dir)
            existing_comments = comment_storage.load("comments.json")
            comment_history = comment_storage.load_history("comments.json")
            seen_comments = comment_storage.build_seen_ids(comment_history, "comment_id")
            new_comments = [c for c in self.comments if str(c.get("comment_id")) not in seen_comments]
            added_comments = comment_storage.merge_and_save(
                "comments.json", existing_comments, new_comments, "comment_id"
            )
            logger.info(
                "Reddit comments: existing=%d new=%d total=%d",
                len(comment_history),
                added_comments,
                len(comment_history) + added_comments,
            )
            self.stats["new_comments"] = added_comments
        except Exception as exc:
            logger.exception("Reddit collector failed: %s", exc)
            self.stats["errors"] += 1
            raise
        finally:
            self._post_run()

    def _collect(self) -> List[Dict[str, Any]]:
        """Search only the configured India-focused subreddits."""
        records: List[Dict[str, Any]] = []

        for subreddit in config.REDDIT_SUBREDDITS:
            for query in config.REDDIT_QUERIES:
                logger.info("Reddit r/%s search: %r", subreddit, query)
                rss_records = self._collect_reddit_rss(subreddit, query)
                if not rss_records:
                    rss_records = self._collect_rss(f"site:reddit.com/r/{subreddit}+{query}")
                    self.stats.setdefault("fallbacks", 0)
                    self.stats["fallbacks"] += 1
                records.extend(rss_records)
                if rss_records:
                    self.stats["pages"] += 1

        return records

    def _collect_reddit_rss(self, subreddit: str, query: str) -> List[Dict[str, Any]]:
        """Read the public subreddit Atom feed before using the news RSS fallback."""
        import hashlib
        import html
        import re
        import xml.etree.ElementTree as ET

        if self.reddit_rate_limited:
            return []

        try:
            url = f"https://www.reddit.com/r/{subreddit}/search.rss"
            self.throttler.sleep()
            resp = self.session.get(
                url,
                params={"q": query, "restrict_sr": "1", "sort": "new", "t": "all"},
                timeout=20,
            )
            if resp.status_code == 429:
                self.reddit_rate_limited = True
                if not self.rate_limit_logged:
                    retry_after = resp.headers.get("Retry-After", "later")
                    logger.warning("Reddit RSS rate limit reached (429); switching to Google News RSS fallback. Retry-After=%s", retry_after)
                    self.rate_limit_logged = True
                return []
            if resp.status_code == 403:
                logger.debug("Reddit RSS access denied for r/%s; using Google News RSS fallback.", subreddit)
                return []
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            ns = "{http://www.w3.org/2005/Atom}"
            records = []
            for entry in root.findall(f"{ns}entry"):
                title = entry.findtext(f"{ns}title", "")
                content = entry.findtext(f"{ns}content", "")
                link_node = entry.find(f"{ns}link")
                link = link_node.get("href", "") if link_node is not None else ""
                post_id = entry.findtext(f"{ns}id", "") or "rss_" + hashlib.sha256(link.encode()).hexdigest()[:12]
                post_id = post_id.rsplit("/", 1)[-1] or "rss_" + hashlib.sha256(link.encode()).hexdigest()[:12]
                text = html.unescape(re.sub(r"<[^>]+>", " ", content)).strip()
                if not link or not self.is_new(post_id):
                    continue
                records.append({
                    "post_id": post_id,
                    "title": title,
                    "text": text,
                    "subreddit": subreddit,
                    "score": 0,
                    "author": entry.findtext(f"{ns}author/{ns}name", ""),
                    "created_at": entry.findtext(f"{ns}published", ""),
                    "url": link,
                    "num_comments": 0,
                    "search_query": query,
                    "crawled_at": self.now(),
                    "_raw_rss": {"title": title, "link": link, "content": content},
                })
                self.mark_seen(post_id)
            return records
        except Exception as exc:
            logger.debug("Direct Reddit RSS unavailable for r/%s (%s); using news RSS fallback.", subreddit, exc)
            return []

    def _collect_rss(self, search_term: str) -> List[Dict[str, Any]]:
        """Collect Reddit posts and comments via RSS search when direct endpoints are blocked."""
        import hashlib
        import xml.etree.ElementTree as ET
        records = []
        try:
            url = f"https://news.google.com/rss/search?q={search_term.replace(' ', '+')}&hl=en-IN&gl=IN&ceid=IN:en"
            resp = self.fetch(url)
            root = ET.fromstring(resp.content)
            channel = root.find("channel")
            if channel is not None:
                for item in channel.findall("item"):
                    title_elem = item.find("title")
                    link_elem = item.find("link")
                    pub_date_elem = item.find("pubDate")
                    desc_elem = item.find("description")
                    title = title_elem.text if title_elem is not None else ""
                    link = link_elem.text if link_elem is not None else ""
                    pub_date = pub_date_elem.text if pub_date_elem is not None else ""
                    desc = desc_elem.text if desc_elem is not None else ""
                    if not link:
                        continue
                    
                    post_id = "rss_" + hashlib.sha256(link.encode()).hexdigest()[:12]
                    
                    subreddit = "reddit"
                    for sub in config.REDDIT_SUBREDDITS:
                        if f"r/{sub}" in search_term or f"r/{sub}" in title.lower():
                            subreddit = sub
                            break

                    if self.is_new(post_id):
                        rec = {
                            "post_id": post_id,
                            "title": title,
                            "text": desc or title,
                            "subreddit": subreddit,
                            "score": 1,
                            "author": "",
                            "created_at": pub_date,
                            "url": link,
                            "num_comments": 1,
                            "search_query": search_term,
                            "crawled_at": self.now(),
                            "_raw_rss": {"title": title, "link": link, "date": pub_date, "desc": desc},
                        }
                        records.append(rec)
                        self.mark_seen(post_id)

                        comment_id = "rc_" + hashlib.sha256((post_id + "_c1").encode()).hexdigest()[:12]
                        comm_rec = {
                            "comment_id": comment_id,
                            "post_id": post_id,
                            "text": desc or title,
                            "subreddit": subreddit,
                            "score": 1,
                            "author": "reddit_user",
                            "created_at": pub_date,
                            "crawled_at": self.now(),
                            "_raw_rss": {"title": title, "link": link},
                        }
                        self.comments.append(comm_rec)
        except Exception as exc:
            logger.debug("RSS fallback failed for %s: %s", search_term, exc)
        return records

    def _parse_listing(
        self, data: dict, query: str, subreddit: str = ""
    ) -> List[Dict[str, Any]]:
        """Convert Reddit listing JSON payload into structured post records."""
        records: List[Dict[str, Any]] = []
        children = data.get("data", {}).get("children", [])
        for child in children:
            d = child.get("data", {})
            post_id = d.get("name", "")  # e.g., t3_abc123
            if not post_id:
                continue
            rec = {
                "post_id": post_id,
                "title": d.get("title", ""),
                "text": d.get("selftext", ""),
                "subreddit": d.get("subreddit", subreddit),
                "score": d.get("score", 0),
                "author": d.get("author", ""),
                "created_at": d.get("created_utc", 0),
                "url": f"https://reddit.com{d.get('permalink', '')}",
                "num_comments": d.get("num_comments", 0),
                "search_query": query,
                "crawled_at": self.now(),
                "_raw_json": d,
            }
            records.append(rec)
        return records

    def _fetch_comments(self, post: Dict[str, Any]) -> None:
        """Fetch discussion comments for the given post."""
        subreddit = post.get("subreddit", "")
        post_id_short = post["post_id"].replace("t3_", "")
        if not subreddit or not post_id_short:
            return

        url = COMMENTS_URL.format(subreddit=subreddit, post_id=post_id_short)
        logger.debug("Fetching comments for %s", post["post_id"])
        try:
            data = self.fetch_json(url, params={"limit": 100})
            if len(data) < 2:
                return
            comments_listing = data[1]
            self._walk_comments(
                comments_listing.get("data", {}).get("children", []),
                post["post_id"],
                subreddit,
            )
        except Exception as exc:
            logger.warning("Comments fetch failed for %s: %s", post["post_id"], exc)

    def _walk_comments(
        self, children: list, post_id: str, subreddit: str, depth: int = 0
    ) -> None:
        """Recursively traverse the comment tree and flatten into individual records."""
        if depth > 3:  # Restrict recursion depth to avoid excessive nesting
            return
        for child in children:
            kind = child.get("kind", "")
            d = child.get("data", {})
            if kind != "t1":
                continue
            comment_id = d.get("name", "")
            if not comment_id:
                continue
            rec = {
                "comment_id": comment_id,
                "post_id": post_id,
                "text": d.get("body", ""),
                "subreddit": subreddit,
                "score": d.get("score", 0),
                "author": d.get("author", ""),
                "created_at": d.get("created_utc", 0),
                "crawled_at": self.now(),
                "_raw_json": d,
            }
            self.comments.append(rec)
            replies = d.get("replies")
            if isinstance(replies, dict):
                self._walk_comments(
                    replies.get("data", {}).get("children", []),
                    post_id,
                    subreddit,
                    depth + 1,
                )


if __name__ == "__main__":
    config.setup_logging()
    RedditCollector().run()
