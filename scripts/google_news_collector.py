"""Collect Zomato news articles from Google News RSS feeds — no API key required at all."""
import hashlib
import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

import config
from scripts.base_collector import BaseCollector

logger = logging.getLogger(__name__)

RSS_URL = (
    "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
)


class GoogleNewsCollector(BaseCollector):
    """Fetch Zomato news articles directly from Google News RSS feeds."""

    def __init__(self):
        super().__init__(
            source_name="GoogleNews",
            id_field="article_id",
            output_subdir="google_news",
            delay=0.8,
        )
        self.query_matches: List[Dict[str, Any]] = []

    @property
    def output_filename(self) -> str:
        return "articles.json"

    def run(self) -> None:
        logger.info("=== Starting %s ===", self.source_name)
        self._pre_run()
        try:
            new_records = self._collect()
            self.stats["new"] = self.storage.merge_and_save(
                self.output_filename, self.existing_records, new_records, self.id_field
            )
            existing = self.storage.load("query_matches.json")
            history = self.storage.load_history("query_matches.json")
            seen = self.storage.build_seen_ids(history, "match_id")
            fresh_matches = [m for m in self.query_matches if m["match_id"] not in seen]
            self.storage.merge_and_save("query_matches.json", existing, fresh_matches, "match_id")
        except Exception:
            self.stats["errors"] += 1
            raise
        finally:
            self._post_run()

    def _collect(self) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for query in config.GOOGLE_NEWS_QUERIES:
            logger.info("Google News query: %r", query)
            try:
                url = RSS_URL.format(query=query.replace(" ", "+"))
                resp = self.fetch(url)
                root = ET.fromstring(resp.content)
                ns = {"dc": "http://purl.org/dc/elements/1.1/"}
                channel = root.find("channel")
                if channel is None:
                    logger.warning("No channel found in RSS payload for query %r", query)
                    continue

                for item in channel.findall("item"):
                    record = self._parse_item(item, query, ns)
                    if not record:
                        continue
                    self.query_matches.append({
                        "match_id": f"{record['article_id']}::{query.lower()}",
                        "article_id": record["article_id"],
                        "search_query": query,
                        "article_date": record.get("date", ""),
                        "crawled_at": record.get("crawled_at", ""),
                    })
                    if self.is_new(record["article_id"]):
                        records.append(record)
                        self.mark_seen(record["article_id"])
                self.stats["pages"] += 1
            except Exception as exc:
                logger.error("Google news query %r failed: %s", query, exc)
                self.stats["errors"] += 1
        return records

    def _parse_item(
        self, item: ET.Element, query: str, ns: dict
    ) -> Dict[str, Any]:
        """Construct normalized article record from single RSS <item> element."""
        title_elem = item.find("title")
        link_elem = item.find("link")
        pub_date_elem = item.find("pubDate")
        source_elem = item.find("source")
        desc_elem = item.find("description")
        author_elem = item.find("dc:creator", ns)

        title = title_elem.text if title_elem is not None else ""
        url = link_elem.text if link_elem is not None else ""
        pub_date = pub_date_elem.text if pub_date_elem is not None else ""
        source_name = source_elem.text if source_elem is not None else ""
        description = desc_elem.text if desc_elem is not None else ""
        author = author_elem.text if author_elem is not None else ""

        if not url:
            return {}

        article_id = hashlib.sha256(url.encode()).hexdigest()[:16]

        raw_item = {
            "title": title,
            "link": url,
            "pubDate": pub_date,
            "source": source_name,
            "description": description,
            "dc:creator": author,
        }

        return {
            "article_id": article_id,
            "title": title,
            "description": description,
            "date": pub_date,
            "url": url,
            "source_name": source_name,
            "author": author,
            "search_query": query,
            "crawled_at": self.now(),
            "_raw_rss_item": raw_item,
        }


if __name__ == "__main__":
    config.setup_logging()
    GoogleNewsCollector().run()
