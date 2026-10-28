"""The long-running loop: inbox files in, application folders out.

    inbox/links.txt     job links, one job per blank-line-separated block
    inbox/manual/*.md   pasted job descriptions (for sites that block scraping)
    inbox/status.txt    "<id> <status> [note]" lines to update the tracker
    inbox/processed.log what was picked up and what became of it

Entries go into the database queue before they are removed from the inbox files, and the
queue survives restarts, so nothing is lost if the container stops mid-job.
"""

import logging
import os
import re
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from jap import config
from jap.cleanup import cleanup
from jap.db import DB, USER_STATUSES, now
from jap.llm import LLMProvider
from jap.pipeline import JobInput, run_job
from jap.tracker import write_tracker

log = logging.getLogger("jap.watch")

SETTLE_SECONDS = 5  # a file must be this old before it's read, so half-typed edits are left alone
MIN_MANUAL_CHARS = 200
URL = re.compile(r"^(https?://\S+)")
DIRECTIVE = re.compile(r"^#\s*(track|note|force)\b\s*:?\s*(.*)$", re.IGNORECASE)
MANUAL_HEADER = re.compile(r"^(url|track|note|force)\s*:\s*(.*)$", re.IGNORECASE)
STATUS_LINE = re.compile(r"^\s*(\d+)\s+([a-z_]+)\s*(.*)$", re.IGNORECASE)
NOT_APPLIED = "  # ← not applied:"

LINKS_HEADER = """\
# Paste job links below. One job per block; leave a blank line between jobs.
# Extra URLs in a block are company or mission pages that help the cover letter.
# Optional lines in a block:  # track: backend | fullstack | industrial_iot | fintech_web3
#                             # note: referred by Ada
#                             # force          (redo a job that already exists)
# Picked-up blocks are removed from this file and logged in processed.log.
# Sites that block scraping (LinkedIn, Indeed): paste the description into manual/<name>.md instead,
# optionally starting with lines like "url: …", "track: …", "note: …".
"""

STATUS_HEADER = """\
# One update per line:  <job id> <status> [note]     e.g.  7 applied   or   0012 rejected no visa sponsorship
# Statuses: applied, interviewing, offer, rejected, withdrawn, skipped.
# Applied lines are removed and logged in processed.log; lines that can't be applied stay, with the reason.
"""


@dataclass
class Block:
    urls: list[str]
    track: str | None = None
    note: str | None = None
    force: bool = False
    ignored: list[str] = field(default_factory=list)


def parse_links(text: str) -> tuple[list[Block], set[int]]:
    """Blocks that contain at least one URL, and the line numbers they consume.

    Plain comment lines are never consumed, so the header and your own notes stay in the file.
    Blocks without a URL are left alone: you may still be typing.
    """
    lines = text.splitlines()
    groups: list[list[int]] = [[]]
    for i, line in enumerate(lines):
        if line.strip():
            groups[-1].append(i)
        elif groups[-1]:
            groups.append([])

    blocks, consumed = [], set()
    for group in groups:
        block, used = Block(urls=[]), []
        for i in group:
            s = lines[i].strip()
            if m := URL.match(s):
                block.urls.append(m.group(1))
                used.append(i)
            elif d := DIRECTIVE.match(s):
                key, value = d.group(1).lower(), d.group(2).strip()
                if key == "force":
                    block.force = True
                elif value:
                    setattr(block, key, value)
                used.append(i)
            elif not s.startswith("#"):
                block.ignored.append(s)
                used.append(i)
        if block.urls:
            blocks.append(block)
            consumed.update(used)
    return blocks, consumed


def without_lines(text: str, drop: set[int]) -> str:
    kept = [line for i, line in enumerate(text.splitlines()) if i not in drop]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip("\n")
    return out + "\n" if out else ""


def parse_manual(text: str) -> tuple[dict[str, str], str]:
    headers, lines = {}, text.splitlines()
    while lines and (m := MANUAL_HEADER.match(lines[0].strip())):
        headers[m.group(1).lower()] = m.group(2).strip()
        lines.pop(0)
    return headers, "\n".join(lines).strip()


def settled(path: Path) -> bool:
    return time.time() - path.stat().st_mtime >= SETTLE_SECONDS


def replace_if_unchanged(path: Path, expected: str, new: str) -> bool:
    """Atomic compare-and-swap, so an edit you saved a moment ago is never overwritten."""
    if path.read_text() != expected:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new)
    os.replace(tmp, path)
    return True


class Watcher:
    def __init__(self, settings: config.Settings, provider: LLMProvider, db: DB, pdf: bool = True):
        self.settings, self.provider, self.db, self.pdf = settings, provider, db, pdf
        self.inbox = config.INBOX_DIR
        self.warned: set[str] = set()
        self.stopping = False
        self.cleaned_on: date | None = None

    # --- inbox files ---

    def ensure_inbox(self) -> None:
        (self.inbox / "manual" / "processed").mkdir(parents=True, exist_ok=True)
        for name, header in (("links.txt", LINKS_HEADER), ("status.txt", STATUS_HEADER)):
            if not (self.inbox / name).exists():
                (self.inbox / name).write_text(header)

    def record(self, message: str) -> None:
        with open(self.inbox / "processed.log", "a") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")

    def ingest_links(self) -> int:
        path = self.inbox / "links.txt"
        if not path.exists() or not settled(path):
            return 0
        text = path.read_text()
        blocks, consumed = parse_links(text)
        if not blocks:
            return 0
        ids = [self.db.enqueue("links.txt", b.urls, None, b.track, b.note, b.force, commit=False) for b in blocks]
        new_text = without_lines(text, consumed) or LINKS_HEADER
        if not replace_if_unchanged(path, text, new_text):
            self.db.conn.rollback()
            log.info("links.txt changed while being read; trying again next poll")
            return 0
        self.db.conn.commit()
        for qid, b in zip(ids, blocks):
            opts = "".join(f" [{k}={v}]" for k, v in (("track", b.track), ("note", b.note)) if v) + (" [force]" if b.force else "")
            self.record(f"queued q{qid}  {' '.join(b.urls)}{opts}")
            if b.ignored:
                self.record(f"  q{qid}: ignored non-URL lines: {' | '.join(b.ignored)} (URLs need http:// or https://)")
        return len(blocks)

    def ingest_manual(self) -> int:
        folder = self.inbox / "manual"
        count = 0
        for path in sorted(folder.glob("*")):
            if not path.is_file() or path.suffix.lower() not in (".md", ".txt") or path.name.lower().startswith("readme"):
                continue
            if not settled(path):
                continue
            headers, body = parse_manual(path.read_text())
            if len(body) < MIN_MANUAL_CHARS:
                if path.name not in self.warned:
                    self.warned.add(path.name)
                    self.record(f"manual/{path.name}: waiting, only {len(body)} characters (need {MIN_MANUAL_CHARS})")
                continue
            urls = [headers["url"]] if headers.get("url") else []
            force = headers.get("force", "").lower() in ("1", "yes", "true")
            qid = self.db.enqueue(f"manual/{path.name}", urls, body, headers.get("track"), headers.get("note"), force)
            path.rename(folder / "processed" / f"{datetime.now():%Y%m%d-%H%M%S}_{path.name}")
            self.record(f"queued q{qid}  manual/{path.name}" + (f" ({urls[0]})" if urls else ""))
            count += 1
        return count

    def apply_status(self) -> int:
        path = self.inbox / "status.txt"
        if not path.exists() or not settled(path):
            return 0
        text = path.read_text()
        out, applied = [], []
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or NOT_APPLIED in line:
                out.append(line)
                continue
            m = STATUS_LINE.match(s)
            reason = None
            if not m:
                reason = 'expected "<job id> <status> [note]"'
            elif m.group(2).lower() not in USER_STATUSES:
                reason = f"status must be one of {', '.join(USER_STATUSES)}"
            elif self.db.get(int(m.group(1))) is None:
                reason = f"no job {int(m.group(1))}"
            if reason:
                out.append(f"{line}{NOT_APPLIED} {reason}")
                continue
            applied.append((int(m.group(1)), m.group(2).lower(), m.group(3).strip().strip('"') or None))
        new_text = "\n".join(out) + "\n"
        if new_text == text or not replace_if_unchanged(path, text, new_text):
            return 0
        for job_id, status, note in applied:
            self.db.update(job_id, status=status, **({"note": note} if note else {}))
            self.record(f"job {job_id:04d} marked {status}" + (f" ({note})" if note else ""))
        if applied:
            write_tracker(self.db, config.APPLICATIONS_DIR)
        return len(applied)

    # --- queue ---

    def drain(self, stop: Callable[[], bool]) -> int:
        done = 0
        while not stop() and (item := self.db.next_queued()):
            self.db.queue_update(item["id"], status="running")
            self.heartbeat()
            job = JobInput(urls=item["urls"], jd_text=item["jd_text"], track_hint=item["track"],
                           note=item["note"], force=bool(item["force"]))
            row = run_job(job, settings=self.settings, provider=self.provider, db=self.db, pdf=self.pdf)
            self.db.queue_update(item["id"], status="done", job_id=row["id"], error=row["error"])
            where = f"  → applications/{row['folder']}/" if row["folder"] else ""
            what = " at ".join(x for x in (row["title"], row["company"]) if x) or (item["urls"] or [item["source"]])[0]
            if row.get("skipped"):
                self.record(f"skipped q{item['id']}: already done as job {row['id']:04d} ({row['status']}, {what}); add '# force' to redo")
            else:
                self.record(f"job {row['id']:04d} {row['status']}  {what}{where}" + (f"  ({row['error']})" if row["error"] else ""))
            done += 1
        return done

    def tick(self, stop: Callable[[], bool] = lambda: False) -> None:
        self.ingest_links()
        self.ingest_manual()
        self.apply_status()
        self.drain(stop)
        self.daily_cleanup(date.today())

    def daily_cleanup(self, today: date) -> None:
        """Delete expired application folders, at most once a day."""
        if self.cleaned_on == today:
            return
        self.cleaned_on = today
        if names := cleanup(self.db, today):
            self.record(f"deleted {len(names)} folder(s) older than the keep period: {', '.join(names)}")

    def heartbeat(self) -> None:
        config.HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.HEARTBEAT_PATH.write_text(now())

    def run(self, poll: int) -> None:
        def stop(signum, _frame):
            log.info("got %s: finishing the current job, then stopping", signal.Signals(signum).name)
            self.stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        self.ensure_inbox()
        if n := self.db.requeue_interrupted():
            log.info("re-queued %d item(s) interrupted by the last shutdown", n)
        log.info("watching %s every %ds (%d queued)", self.inbox, poll, self.db.pending_count())
        while not self.stopping:
            self.heartbeat()
            try:
                self.tick(stop=lambda: self.stopping)
            except Exception:
                log.exception("watch loop error; continuing")
            for _ in range(poll * 2):
                if self.stopping:
                    break
                time.sleep(0.5)
        log.info("stopped")


def healthy(max_age: int) -> tuple[bool, str]:
    if not config.HEARTBEAT_PATH.exists():
        return False, "no heartbeat yet"
    age = time.time() - config.HEARTBEAT_PATH.stat().st_mtime
    return age <= max_age, f"last heartbeat {age:.0f}s ago"
