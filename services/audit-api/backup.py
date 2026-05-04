"""JSONL backup writer for audit events that fail to flush to SQLite."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_backup_dir: str = "./backup"


def configure(backup_dir: str) -> None:
    """Set the backup directory (called once at startup)."""
    global _backup_dir
    _backup_dir = backup_dir
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    logger.info("audit backup dir: %s", backup_dir)


def write_backup(events: list[dict]) -> None:
    """Append events to an hourly JSONL file. Called on flush failure or queue overflow."""
    if not events:
        return
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-00")
    path = Path(_backup_dir) / f"audit_backup_{hour}.jsonl"
    try:
        with open(path, "a", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        logger.warning("backed up %d events to %s", len(events), path)
    except OSError as exc:
        logger.critical(
            "backup write failed, %d events permanently lost: %s", len(events), exc
        )
