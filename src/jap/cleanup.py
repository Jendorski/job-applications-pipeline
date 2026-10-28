"""Delete application folders older than KEEP_DAYS.

Age comes from the date the folder name starts with (the day the job ran), not from file
times, so opening or editing a folder doesn't keep it alive. Only folders named like
"2026-10-28_0001_..." are touched. A job's current folder is kept while you are still in
the process with that company (applied, interviewing, offer).
"""

import logging
import os
import re
import shutil
from datetime import date, timedelta

from jap import config
from jap.db import DB

log = logging.getLogger("jap.cleanup")

KEEP_DAYS = int(os.environ.get("APPLICATIONS_KEEP_DAYS") or 30)  # 0 turns cleanup off
KEEP_STATUSES = {"applied", "interviewing", "offer"}
FOLDER = re.compile(r"^(\d{4}-\d{2}-\d{2})_\d{4}_")


def expired_folders(db: DB, today: date, keep_days: int = KEEP_DAYS) -> list[str]:
    if keep_days <= 0 or not config.APPLICATIONS_DIR.is_dir():
        return []
    cutoff = today - timedelta(days=keep_days)
    kept = {j["folder"] for j in db.jobs() if j["folder"] and j["status"] in KEEP_STATUSES}
    old = []
    for path in sorted(config.APPLICATIONS_DIR.iterdir()):
        m = FOLDER.match(path.name)
        if not (m and path.is_dir()) or path.name in kept:
            continue
        try:
            ran = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if ran < cutoff:
            old.append(path.name)
    return old


def cleanup(db: DB, today: date, keep_days: int = KEEP_DAYS, dry_run: bool = False) -> list[str]:
    names = expired_folders(db, today, keep_days)
    for name in names:
        if not dry_run:
            shutil.rmtree(config.APPLICATIONS_DIR / name)
        log.info("%s %s (older than %d days)", "would delete" if dry_run else "deleted", name, keep_days)
    return names
