"""Scrape customer reviews from Trustpilot for zomato.in."""
import logging
import re
from typing import Any, Dict, List, Optional

from lxml import html as lh

import config
from scripts.base_collector import BaseCollector

logger = logging.getLogger(__name__)


class TrustpilotCollector(BaseCollector):
    """Extract verified customer reviews from Trustpilot for Zomato."""

    def __init__(self):
        super().__init__(
            source_name="Trustpilot",
            id_field="review_id",
            output_subdir="trustpilot",
            delay=config.TRUSTPILOT_DELAY,
        )
        self.max_pages = config.TRUSTPILOT_MAX_PAGES

    @property
    def output_filename(self) -> str:
        return "reviews.json"

    def _collect(self) -> List[Dict[str, Any]]:
        return self._collect_rss()

    def _collect_direct(self) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for page in range(1, self.max_pages + 1):
            url = f"{config.TRUSTPILOT_BASE_URL}?page={page}"
            logger.info("Trustpilot page %d: %s", page, url)
            try:
                tree = self.fetch_html(url)
                reviews = self._extract_reviews(tree)
                if not reviews:
                    logger.info("Nothing found on page %d — pagination reached end.", page)
                    break
                for rec in reviews:
                    if rec and self.is_new(rec["review_id"]):
                        records.append(rec)
                        self.mark_seen(rec["review_id"])
                self.stats["pages"] += 1
            except Exception as exc:
                logger.warning("Trustpilot page %d failed: %s", page, exc)
                if hasattr(exc, "response") and exc.response is not None and exc.response.status_code == 403:
                    logger.warning("HTTP 403 Forbidden received from Trustpilot — bot protection active, attempting fallback...")
                    rss_reviews = self._collect_rss()
                    records.extend(rss_reviews)
                    self.stats.setdefault("fallbacks", 0)
                    self.stats["fallbacks"] += 1
                    break
                self.stats["errors"] += 1
        return records

    def _collect_rss(self) -> List[Dict[str, Any]]:
        """Collect Trustpilot reviews via RSS search with robust rating extraction."""
        import hashlib
        import xml.etree.ElementTree as ET
        records = []
        try:
            queries = ["site:trustpilot.com/review/zomato.in"]
            for q in queries:
                url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
                resp = self.fetch(url)
                root = ET.fromstring(resp.content)
                channel = root.find("channel")
                if channel is None:
                    continue
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
                    review_id = "tp_" + hashlib.sha256(link.encode()).hexdigest()[:12]
                    if self.is_new(review_id):
                        rating = self._extract_rating_from_text(f"{title} {desc}")
                        rec = {
                            "review_id": review_id,
                            "rating": rating,
                            "review_text": desc or title,
                            "date": pub_date,
                            "review_title": title,
                            "reviewer": "",
                            "verified": False,
                            "company_response": "",
                            "review_url": link,
                            "crawled_at": self.now(),
                            "_raw_html_snippet": f"<title>{title}</title>",
                        }
                        records.append(rec)
                        self.mark_seen(review_id)
        except Exception as exc:
            logger.debug("Trustpilot RSS fallback failed: %s", exc)
        return records

    def _extract_rating_from_text(self, text: str) -> Optional[float]:
        """Extract numerical rating from review headline or description text."""
        if not text:
            return None
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:/|out of)\s*5", text, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1))
                if 0 <= val <= 5:
                    return val
            except ValueError:
                pass
        
        sentiment_map = {
            "bad": 1.0,
            "poor": 2.0,
            "average": 3.0,
            "great": 4.0,
            "excellent": 5.0,
        }
        m2 = re.search(r'rated\s*["\']?(\w+)["\']?', text, re.IGNORECASE)
        if m2:
            word = m2.group(1).lower()
            if word in sentiment_map:
                return sentiment_map[word]
        return None

    def _extract_reviews(self, tree: lh.HtmlElement) -> List[Dict[str, Any]]:
        """Extract review cards from HTML page."""
        records: List[Dict[str, Any]] = []

        cards = tree.xpath("//article")
        if not cards:
            cards = tree.xpath('//div[@data-service-review-card-paper]')
        if not cards:
            cards = tree.xpath('//div[contains(@class, "review")]')

        for card in cards:
            try:
                rec = self._parse_card(card)
                if rec:
                    records.append(rec)
            except Exception as exc:
                logger.debug("Skipping card due to parsing issue: %s", exc)
        return records

    def _parse_card(self, card: lh.HtmlElement) -> Optional[Dict[str, Any]]:
        """Extract each and every field from an individual review card."""
        review_id = card.get("data-service-review-id", "")
        if not review_id:
            links = card.xpath('.//a[@href]/@href')
            for link in links:
                m = re.search(r"/reviews/([a-f0-9]+)", link)
                if m:
                    review_id = m.group(1)
                    break
        if not review_id:
            return None

        rating: Optional[float] = None
        rating_raw = card.get("data-service-review-rating", "")
        if not rating_raw:
            rating_elem = card.xpath('.//div[@data-service-review-rating]')
            if rating_elem:
                rating_raw = rating_elem[0].get("data-service-review-rating", "")
        
        if rating_raw:
            try:
                rating = float(rating_raw)
            except ValueError:
                rating = None

        if rating is None:
            img_alts = card.xpath('.//img[contains(@alt, "Rated")]/@alt')
            for alt in img_alts:
                r = self._extract_rating_from_text(alt)
                if r is not None:
                    rating = r
                    break

        if rating is None:
            aria_labels = card.xpath('.//*[@aria-label[contains(., "Rated") or contains(., "star")]]/@aria-label')
            for label in aria_labels:
                r = self._extract_rating_from_text(label)
                if r is not None:
                    rating = r
                    break

        title = ""
        title_elems = card.xpath(".//h2//text()")
        if title_elems:
            title = " ".join(t.strip() for t in title_elems if t.strip())
        if not title:
            title_elems = card.xpath('.//div[contains(@class, "title")]//text()')
            title = " ".join(t.strip() for t in title_elems if t.strip())

        text = ""
        paras = card.xpath(".//p//text()")
        if paras:
            text = max((p.strip() for p in paras if p.strip()), key=len, default="")

        if rating is None and (title or text):
            rating = self._extract_rating_from_text(f"{title} {text}")

        date = ""
        time_elem = card.xpath(".//time/@datetime")
        if time_elem:
            date = time_elem[0]
        else:
            date_texts = card.xpath('.//p[contains(text(), "Date of experience")]/text()')
            if date_texts:
                m = re.search(r"Date of experience[\s:]*([\w\s,\d]+)", date_texts[0])
                if m:
                    date = m.group(1).strip()

        reviewer = ""
        reviewer_spans = card.xpath('.//span[contains(@class, "typography")]//text()')
        if reviewer_spans:
            reviewer = reviewer_spans[0].strip()

        verified = bool(card.xpath('.//*[contains(text(), "Verified")]'))

        response = ""
        response_elems = card.xpath('.//p[contains(@class, "response")]//text()')
        if response_elems:
            response = " ".join(r.strip() for r in response_elems if r.strip())

        review_url = f"https://www.trustpilot.com/reviews/{review_id}"

        raw_html = lh.tostring(card, encoding="unicode", pretty_print=False)[:2000]

        return {
            "review_id": review_id,
            "rating": rating,
            "review_text": text,
            "date": date,
            "review_title": title,
            "reviewer": reviewer,
            "verified": verified,
            "company_response": response,
            "review_url": review_url,
            "crawled_at": self.now(),
            "_raw_html_snippet": raw_html,
        }


if __name__ == "__main__":
    config.setup_logging()
    TrustpilotCollector().run()
