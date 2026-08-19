"""Zomato data collection pipeline — crawl, load DB, label, or validate."""
import json
import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import config

config.setup_logging()
logger = logging.getLogger(__name__)

HISTORY_PATH = config.PROJECT_ROOT / "output" / "collection_history.json"
SOURCE_FILES = {
    "google_news": [("google_news", "articles.json", "article_id")],
    "trustpilot": [("trustpilot", "reviews.json", "review_id")],
    "reddit_posts": [("reddit", "posts.json", "post_id")],
    "reddit_comments": [("reddit", "comments.json", "comment_id")],
    "google_play": [("google_play", "reviews.json", "review_id")],
}
RESULT_KEYS = {
    "google_news": "GoogleNews",
    "trustpilot": "Trustpilot",
    "google_play": "GooglePlay",
}

def collectors():
    from scripts.google_news_collector import GoogleNewsCollector
    from scripts.trustpilot_collector import TrustpilotCollector
    from scripts.reddit_collector import RedditCollector
    from scripts.google_play_collector import GooglePlayCollector
    return {
        "google_news": GoogleNewsCollector,
        "trustpilot": TrustpilotCollector,
        "reddit": RedditCollector,
        "google_play": GooglePlayCollector,
    }


def run(names=None):
    available = collectors()
    targets = [available[n] for n in names if n in available] if names else list(available.values())
    def collect(cls):
        collector = cls()
        try:
            collector.run()
            return collector.source_name, collector.stats.copy()
        except Exception as exc:
            logger.error("%s failed: %s", collector.source_name, exc)
            return collector.source_name, {"errors": 1, "message": str(exc)}

    results = {}
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(targets)))) as pool:
        futures = [pool.submit(collect, cls) for cls in targets]
        for future in as_completed(futures):
            source, stats = future.result()
            results[source] = stats
    return {source: results[source] for source in sorted(results)}


def run_notebook():
    notebook = config.PROJECT_ROOT / "data_exploration.ipynb"
    executed = notebook.with_name("data_exploration_executed.ipynb")
    command = [
        sys.executable, "-m", "jupyter", "nbconvert", "--to", "notebook", "--execute",
        str(notebook), "--output", executed.name,
        "--ExecutePreprocessor.timeout=600", "--ExecutePreprocessor.kernel_name=python3",
    ]
    result = subprocess.run(command, cwd=config.PROJECT_ROOT, capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr or result.stdout)[-2000:]
        logger.error("Notebook execution failed: %s", detail)
        raise RuntimeError("Notebook execution failed")
    executed.replace(notebook)
    logger.info("Notebook executed after database load.")
    return {"status": "success", "path": str(notebook)}


def validate():
    checks = {
        "google_news": ("google_news", "articles.json", ["url", "title", "date"], True),
        "trustpilot": ("trustpilot", "reviews.json", ["review_id"], True),
        "reddit_posts": ("reddit", "posts.json", ["post_id"], True),
        "reddit_comments": ("reddit", "comments.json", ["comment_id"], False),
        "google_play": ("google_play", "reviews.json", ["review_id", "rating"], True),
    }
    all_ok = True
    for label, (subdir, filename, fields, required) in checks.items():
        base = config.DATA_DIR / subdir
        path = base / config.TODAY / filename
        if not path.exists():
            dirs = sorted(d for d in base.glob("20*") if (d / filename).exists())
            path = (dirs[-1] / filename) if dirs else path
        if not path.exists():
            if required:
                logger.warning("[FAIL] %s — not found", label)
                all_ok = False
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            missing = [f for f in fields if data and f not in data[0]]
            if missing:
                logger.warning("[FAIL] %s — missing fields: %s", label, missing)
                all_ok = False
            else:
                logger.info("[OK] %s — %d records (%s)", label, len(data), path.parent.name)
        except Exception as exc:
            logger.warning("[FAIL] %s — %s", label, exc)
            all_ok = False
    logger.info("Validation complete — %s", "all OK!" if all_ok else "some checks failed.")
    return all_ok


def load_db(run_id=None):
    from scripts.dblite_loader import DB_PATH, init_db, load_json_files

    logger.info("Loading JSON data into %s", DB_PATH)
    conn = init_db()
    tables = ["google_news", "google_news_query_tracking", "trustpilot", "reddit_posts", "reddit_comments", "google_play", "content_items", "source_tracking"]
    before = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}
    load_json_files(conn)
    after = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}
    for table in tables:
        logger.info("  %-22s %s rows", table, f"{after[table]:,}")
    metrics = {"before": before, "after": after, "new_content_items": after["content_items"] - before["content_items"]}
    if run_id:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT OR REPLACE INTO run_metrics VALUES (?,?,?,?,?,?)", (run_id, "load", "success", now, now, json.dumps(metrics)))
        conn.commit()
    conn.close()
    logger.info("Done — database saved to %s", DB_PATH)
    return metrics


def raw_source_history(results=None):
    results = results or {}
    history = {}
    for source, files in SOURCE_FILES.items():
        records = []
        snapshots = []
        for folder, filename, id_field in files:
            for path in sorted((config.DATA_DIR / folder).glob(f"20*/{filename}")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                except (OSError, json.JSONDecodeError):
                    continue
                ids = [str(row.get(id_field)) for row in data if row.get(id_field)]
                unique_count = len(set(ids))
                records.extend(ids)
                snapshots.append({
                    "file": str(path.relative_to(config.PROJECT_ROOT)),
                    "count": len(data),
                    "unique_count": unique_count,
                    "duplicate_count": len(ids) - unique_count,
                })
        unique_ids = set(records)
        latest = snapshots[-1] if snapshots else {"file": "", "count": 0}
        result_key = "Reddit" if source.startswith("reddit_") else RESULT_KEYS[source]
        source_result = results.get(result_key, {})
        new_count = source_result.get("new", 0)
        if source == "reddit_comments":
            new_count = source_result.get("new_comments", 0)
        history[source] = {
            "raw_record_count": len(records),
            "unique_id_count": len(unique_ids),
            "duplicate_count": len(records) - len(unique_ids),
            "snapshot_count": len(snapshots),
            "snapshots": snapshots,
            "last_output_count": latest.get("count", 0),
            "new_output_count": new_count,
            "last_file": latest.get("file", ""),
        }
    return history


def save_collection_history(run_id, results, load_metrics=None):
    HISTORY_PATH.parent.mkdir(exist_ok=True)
    try:
        history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        history = {"runs": []}
    entry = {
        "run_id": run_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "sources": raw_source_history(results),
    }
    if load_metrics:
        entry["database_counts"] = load_metrics.get("after", {})
    history.setdefault("runs", []).append(entry)
    history["latest"] = entry
    HISTORY_PATH.write_text(json.dumps(history, indent=2), encoding="utf-8")
    return entry


def label_db(run_id=None):
    from scripts.dblite_loader import DB_PATH, init_db
    from scripts.rule_labels import label_database

    started = datetime.now(timezone.utc).isoformat()
    metrics = label_database(DB_PATH)
    with init_db() as conn:
        conn.execute("INSERT OR REPLACE INTO run_metrics VALUES (?,?,?,?,?,?)", (run_id or started, "rule_labels", "success", started, datetime.now(timezone.utc).isoformat(), json.dumps(metrics)))
    logger.info("Rule-based labels complete — %s", metrics)
    return metrics


def pipeline():
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results = run()
    if any(v.get("errors") for v in results.values()):
        raise RuntimeError("One or more collectors failed; review the log before loading.")
    if not validate():
        raise RuntimeError("Raw-data validation failed; database load stopped.")
    load = load_db(run_id)
    labels = label_db(run_id)
    save_collection_history(run_id, results, load)
    notebook = run_notebook()
    output = {"run_id": run_id, "collectors": results, "load": load, "labels": labels, "notebook": notebook}
    output_dir = config.PROJECT_ROOT / "output"
    output_dir.mkdir(exist_ok=True)
    (output_dir / "last_pipeline_run.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--validate" in args:
        validate()
    elif "--pipeline" in args:
        pipeline()
    elif "--label-data" in args:
        label_db()
    elif "--load-db" in args or "--db" in args:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        load = load_db(run_id)
        save_collection_history(run_id, {}, load)
        run_notebook()
    else:
        results = run(args or None)
        if any(value.get("errors") for value in results.values()):
            raise SystemExit("Collection failed; database load skipped. Review the log.")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        load = load_db(run_id)
        label_db(run_id)
        save_collection_history(run_id, results, load)
        run_notebook()
