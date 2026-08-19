"""JSON persistence utility with incremental merge, deduplication, and atomic write operations."""
import json
import logging
import tempfile
import os
from pathlib import Path
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)


class RawStorage:
    """Handles reading, merging, deduplicating, and saving raw JSON collections."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def load(self, filename: str) -> List[Dict[str, Any]]:
        """Load existing JSON array from disk. Returns empty list if file is missing."""
        filepath = self.output_dir / filename
        if not filepath.exists():
            return []

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                logger.warning("Expected list in %s, got %s", filepath, type(data))
                return []
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load %s: %s", filepath, exc)
            return []

    def load_history(self, filename: str) -> List[Dict[str, Any]]:
        """Load the same file from every dated snapshot for global deduplication."""
        records: List[Dict[str, Any]] = []
        for path in sorted(self.output_dir.parent.glob(f"20*/{filename}")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    records.extend(data)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not read historical snapshot %s: %s", path, exc)
        return records

    def build_seen_ids(self, records: List[Dict[str, Any]], id_field: str) -> Set[str]:
        """Build a set of already-seen record IDs for deduplication purpose."""
        seen = set()
        for rec in records:
            val = rec.get(id_field)
            if val is not None:
                seen.add(str(val))
        return seen

    def save(self, filename: str, records: List[Dict[str, Any]]) -> None:
        """Persist records atomically to JSON file using temporary file swap."""
        filepath = self.output_dir / filename
        tmp_path = filepath.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False, default=str)
            os.replace(tmp_path, filepath)
            logger.info("Saved %d records to %s", len(records), filepath)
        except OSError as exc:
            logger.error("Failed to save %s: %s", filepath, exc)
            raise

    def merge_and_save(
        self,
        filename: str,
        existing: List[Dict[str, Any]],
        new_records: List[Dict[str, Any]],
        id_field: str,
    ) -> int:
        """Merge fresh records with existing ones, deduplicate, save to disk, and return count of newly added records."""
        seen = self.build_seen_ids(existing, id_field)
        added = 0
        for rec in new_records:
            rid = str(rec.get(id_field, ""))
            if rid and rid not in seen:
                existing.append(rec)
                seen.add(rid)
                added += 1
        self.save(filename, existing)
        return added
