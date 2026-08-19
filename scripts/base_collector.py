"""Base class defining standard interface and pipeline — each collector inherits from this only."""
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests

import config
from utils.retry import retry
from utils.storage import RawStorage
from utils.throttle import Throttler

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """Common framework: discover → fetch → parse → throttle → dedup → store."""

    def __init__(
        self,
        source_name: str,
        id_field: str,
        output_subdir: str,
        delay: float = config.DEFAULT_DELAY,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.source_name = source_name
        self.id_field = id_field
        self.output_subdir = output_subdir
        self.delay = delay
        self.headers = headers or config.DEFAULT_HEADERS.copy()

        self.output_dir = config.DATA_DIR / output_subdir / config.TODAY
        self.storage = RawStorage(self.output_dir)

        self.throttler = Throttler(delay)
        self.session = requests.Session()
        self.session.headers.update(self.headers)

        self.existing_records: List[Dict[str, Any]] = []
        self.seen_ids: Set[str] = set()
        self.stats = {"existing": 0, "new": 0, "errors": 0, "pages": 0}

    @retry(
        max_retries=config.MAX_RETRIES,
        base_delay=config.BASE_DELAY,
        max_delay=config.MAX_DELAY,
        backoff_factor=config.BACKOFF_FACTOR,
        jitter=config.JITTER,
    )
    def fetch(self, url: str, **kwargs) -> requests.Response:
        """Throttled GET with retry — first take pause to maintain spacing, then fire request directly."""
        self.throttler.sleep()
        logger.debug("Fetching %s", url)
        response = self.session.get(url, timeout=20, **kwargs)
        response.raise_for_status()
        return response

    def fetch_json(self, url: str, **kwargs) -> Dict[str, Any]:
        """Perform GET request and parse the JSON payload directly."""
        resp = self.fetch(url, **kwargs)
        return resp.json()

    def fetch_html(self, url: str, **kwargs):
        """Perform GET request and parse HTML tree via lxml properly."""
        from lxml import html as lh
        resp = self.fetch(url, **kwargs)
        return lh.fromstring(resp.content)

    def run(self) -> None:
        """Complete lifecycle execution — load old data, collect fresh records, merge, save and report status."""
        logger.info("=== Starting %s ===", self.source_name)
        self._pre_run()
        try:
            new_records = self._collect()
            added = self.storage.merge_and_save(
                self.output_filename,
                self.existing_records,
                new_records,
                self.id_field,
            )
            self.stats["new"] = added
        except Exception as exc:
            logger.exception("Collector %s has failed: %s", self.source_name, exc)
            self.stats["errors"] += 1
            raise
        finally:
            self._post_run()

    def _pre_run(self) -> None:
        """Load already collected records from disk and prepare seen IDs for deduplication."""
        self.existing_records = self.storage.load(self.output_filename)
        history = self.storage.load_history(self.output_filename)
        self.stats["existing"] = len(history)
        self.seen_ids = self.storage.build_seen_ids(
            history, self.id_field
        )
        logger.info(
            "%s: %d historical records loaded for global deduplication",
            self.source_name,
            len(history),
        )

    def _post_run(self) -> None:
        """Print final summary of collection work done."""
        total = self.stats["existing"] + self.stats["new"]
        logger.info(
            "=== %s finished | existing=%d new=%d total=%d errors=%d pages=%d ===",
            self.source_name,
            self.stats["existing"],
            self.stats["new"],
            total,
            self.stats["errors"],
            self.stats["pages"],
        )

    @property
    @abstractmethod
    def output_filename(self) -> str:
        """Target output JSON filename, such as 'articles.json'."""

    @abstractmethod
    def _collect(self) -> List[Dict[str, Any]]:
        """Fetch fresh raw records from source. Kindly do not tamper with existing_records directly."""

    def now(self) -> str:
        """Current ISO-8601 timestamp with UTC timezone for tracking."""
        return datetime.now(timezone.utc).isoformat()

    def is_new(self, record_id: str) -> bool:
        """Kindly check whether this record ID has already been seen or is brand new."""
        return str(record_id) not in self.seen_ids

    def mark_seen(self, record_id: str) -> None:
        """Mark ID in seen set so duplicate entry does not get created in the same run."""
        self.seen_ids.add(str(record_id))
