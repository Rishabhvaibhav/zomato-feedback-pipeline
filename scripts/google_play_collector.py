"""Google Play Store review collector for Zomato app — collects rating and customer feedback directly."""
import logging
from typing import Any, Dict, List

import config
from scripts.base_collector import BaseCollector

logger = logging.getLogger(__name__)

try:
    from google_play_scraper import reviews, Sort
except ImportError:
    reviews = None
    Sort = None
    logger.warning(
        "google-play-scraper package is not installed. Google Play collector will fail at runtime."
    )


class GooglePlayCollector(BaseCollector):
    """Collect Zomato customer reviews from Google Play Store without any hassle."""

    def __init__(self):
        super().__init__(
            source_name="GooglePlay",
            id_field="review_id",
            output_subdir="google_play",
            delay=config.GOOGLE_PLAY_DELAY,
        )

    @property
    def output_filename(self) -> str:
        return "reviews.json"

    def _collect(self) -> List[Dict[str, Any]]:
        if reviews is None or Sort is None:
            message = "google-play-scraper is required. Install requirements.txt before running the collector."
            logger.error(message)
            self.stats["errors"] += 1
            raise RuntimeError(message)

        records: List[Dict[str, Any]] = []
        try:
            result, _ = reviews(
                config.GOOGLE_PLAY_APP_ID,
                lang=config.GOOGLE_PLAY_LANG,
                country=config.GOOGLE_PLAY_COUNTRY,
                sort=Sort.NEWEST,
                count=config.GOOGLE_PLAY_MAX_REVIEWS,
            )
            self.stats["pages"] = 1
            for raw in result:
                rec = self._parse_review(raw)
                if rec and self.is_new(rec["review_id"]):
                    records.append(rec)
                    self.mark_seen(rec["review_id"])
        except Exception as exc:
            logger.exception("Google Play scrape failed: %s", exc)
            self.stats["errors"] += 1
        return records

    def _parse_review(self, raw: dict) -> Dict[str, Any]:
        """Normalize raw review data received from scraper into standard schema."""
        review_id = raw.get("reviewId", "")
        if not review_id:
            return {}
        
        at_val = raw.get("at")
        date_str = at_val.isoformat() if hasattr(at_val, "isoformat") else str(at_val or "")
        
        reply_val = raw.get("repliedAt")
        reply_date_str = reply_val.isoformat() if hasattr(reply_val, "isoformat") else str(reply_val or "")
        
        thumbs_up = raw.get("thumbsUpCount", 0) or 0
        version = raw.get("reviewCreatedVersion", "") or ""

        return {
            "review_id": review_id,
            "app_name": "Zomato",
            "rating": raw.get("score"),
            "review_text": raw.get("content", ""),
            "app_version": version,
            "version": version,
            "reviewer": raw.get("userName", ""),
            "review_date": date_str,
            "date": date_str,
            "thumbsUpCount": thumbs_up,
            "thumbs_up_count": thumbs_up,
            "helpful_count": thumbs_up,
            "developer_reply": raw.get("replyContent", ""),
            "reply_date": reply_date_str,
            "crawled_at": self.now(),
            "_raw_scraper": {
                k: (v.isoformat() if hasattr(v, "isoformat") else v)
                for k, v in raw.items()
            },
        }


if __name__ == "__main__":
    config.setup_logging()
    GooglePlayCollector().run()
