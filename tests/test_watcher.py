import os
import time
from datetime import date
from pathlib import Path

import pytest

from jap import config, watcher
from jap.db import DB
from jap.llm.fake import FakeProvider
from jap.models import JobPosting, TailorDraft
from jap.pipeline import JobInput, primary_key
from jap.watcher import LINKS_HEADER, Watcher, healthy, parse_links, parse_manual, without_lines
from tests.test_pipeline import POSTING, fake, good_draft

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APPLICATIONS_DIR", tmp_path / "applications")
    monkeypatch.setattr(config, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(config, "HEARTBEAT_PATH", tmp_path / "data" / "heartbeat")
    monkeypatch.setattr(config, "PROFILE_PATH", ROOT / "examples" / "profile" / "master_profile.yaml")
    monkeypatch.setattr(config, "TRACKS_PATH", ROOT / "examples" / "profile" / "tracks.yaml")
    monkeypatch.setattr(config, "TEMPLATES_DIR", ROOT / "templates")
    monkeypatch.setattr(watcher, "SETTLE_SECONDS", 0)


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "data" / "pipeline.db")


def make_watcher(db, provider=None):
    w = Watcher(config.Settings(), provider or FakeProvider({}), db, pdf=False)
    w.ensure_inbox()
    return w


def write(path: Path, text: str, age: float = 60) -> None:
    path.write_text(text)
    t = time.time() - age
    os.utime(path, (t, t))


LINKS = """\
# my own note: stays

https://job-boards.greenhouse.io/acme/jobs/1?gh_src=x
https://acme.com/about     # mission page
# track: fintech_web3
# note: referred by Ada

https://jobs.lever.co/foo/10dfc8bc-99ad-4ca2-ab76-853cb90a92c2
acme.com/no-scheme
# force

# track: backend
"""


def test_parse_links_blocks_directives_and_what_stays():
    blocks, consumed = parse_links(LINKS)
    assert [b.urls for b in blocks] == [
        ["https://job-boards.greenhouse.io/acme/jobs/1?gh_src=x", "https://acme.com/about"],
        ["https://jobs.lever.co/foo/10dfc8bc-99ad-4ca2-ab76-853cb90a92c2"],
    ]
    assert (blocks[0].track, blocks[0].note, blocks[0].force) == ("fintech_web3", "referred by Ada", False)
    assert blocks[1].force and blocks[1].ignored == ["acme.com/no-scheme"]
    # Comments and the URL-less block (still being typed) remain.
    assert without_lines(LINKS, consumed) == "# my own note: stays\n\n# track: backend\n"


def test_parse_manual_headers():
    headers, body = parse_manual("url: https://www.linkedin.com/jobs/view/1\ntrack: backend\n\nWe are hiring...\n")
    assert headers == {"url": "https://www.linkedin.com/jobs/view/1", "track": "backend"} and body == "We are hiring..."


def test_links_are_queued_logged_and_removed(db):
    w = make_watcher(db)
    write(w.inbox / "links.txt", LINKS_HEADER + "\nhttps://job-boards.greenhouse.io/acme/jobs/1\n# note: first\n")
    assert w.ingest_links() == 1
    assert (w.inbox / "links.txt").read_text() == LINKS_HEADER
    item = db.next_queued()
    assert item["urls"] == ["https://job-boards.greenhouse.io/acme/jobs/1"] and item["note"] == "first"
    assert "queued q1  https://job-boards.greenhouse.io/acme/jobs/1 [note=first]" in (w.inbox / "processed.log").read_text()


def test_recent_or_concurrently_edited_files_are_left_alone(db, monkeypatch):
    w = make_watcher(db)
    monkeypatch.setattr(watcher, "SETTLE_SECONDS", 30)
    write(w.inbox / "links.txt", "https://example.com/job/1\n", age=1)
    assert w.ingest_links() == 0 and db.next_queued() is None

    monkeypatch.setattr(watcher, "SETTLE_SECONDS", 0)
    real = watcher.replace_if_unchanged
    monkeypatch.setattr(watcher, "replace_if_unchanged", lambda p, old, new: (p.write_text(old + "https://x.com/2\n"), real(p, old, new))[1])
    assert w.ingest_links() == 0 and db.next_queued() is None  # rolled back, nothing lost
    assert "https://example.com/job/1" in (w.inbox / "links.txt").read_text()


def test_manual_description_is_queued_and_moved(db):
    w = make_watcher(db)
    write(w.inbox / "manual" / "short.md", "too short")
    write(w.inbox / "manual" / "acme.md", "url: https://www.linkedin.com/jobs/view/9\nnote: from LinkedIn\n\n" + "Backend role. " * 30)
    assert w.ingest_manual() == 1
    item = db.next_queued()
    assert item["urls"] == ["https://www.linkedin.com/jobs/view/9"] and item["jd_text"].startswith("Backend role.")
    assert not (w.inbox / "manual" / "acme.md").exists()
    assert len(list((w.inbox / "manual" / "processed").glob("*_acme.md"))) == 1
    assert (w.inbox / "manual" / "short.md").exists() and "waiting" in (w.inbox / "processed.log").read_text()


def test_status_updates(db):
    w = make_watcher(db)
    job_id = db.create_job("https://a.com/1", [], None)
    write(w.inbox / "status.txt", f"# header\n{job_id} applied\n{job_id:04d} rejected \"no visa sponsorship\"\n99 applied\n1 hired\n")
    assert w.apply_status() == 2
    row = db.get(job_id)
    assert row["status"] == "rejected" and row["note"] == "no visa sponsorship" and row["applied_at"]
    left = (w.inbox / "status.txt").read_text()
    assert "# header" in left and "99 applied  # ← not applied: no job 99" in left and "1 hired  # ← not applied: status" in left
    write(w.inbox / "status.txt", left)
    assert w.apply_status() == 0 and (w.inbox / "status.txt").read_text() == left  # annotated once, not again


def test_tick_runs_the_queue_end_to_end(db):
    provider = fake({JobPosting: [POSTING], TailorDraft: [good_draft()]})
    w = make_watcher(db, provider)
    write(w.inbox / "manual" / "job.md", "note: test run\n\n" + "Backend Software Engineer at Company F. " * 20)
    w.tick()
    row = db.get(1)
    assert row["status"] == "ready" and row["note"] == "test run"
    log = (w.inbox / "processed.log").read_text()
    assert "job 0001 ready  Backend Software Engineer at Company F  → applications/" in log
    assert (config.APPLICATIONS_DIR / "tracker.md").exists()


def test_duplicate_links_are_logged_as_skipped(db):
    provider = fake({JobPosting: [POSTING], TailorDraft: [good_draft()]})
    w = make_watcher(db, provider)
    text = "Backend Software Engineer at Company F. " * 20
    db.enqueue("links.txt", [], text, None, None, False)
    db.enqueue("links.txt", [], text, None, None, False)
    assert w.drain(lambda: False) == 2 and len(provider.calls) == 3  # second one cost nothing
    assert "skipped q2: already done as job 0001 (ready" in (w.inbox / "processed.log").read_text()


def test_interrupted_items_are_requeued_and_unfinished_jobs_rerun(db):
    provider = fake({JobPosting: [POSTING], TailorDraft: [good_draft()]})
    w = make_watcher(db, provider)
    text = "Backend Software Engineer at Company F. " * 20
    qid = db.enqueue("links.txt", [], text, None, None, False)
    db.queue_update(qid, status="running")
    job_id = db.create_job(primary_key(JobInput(jd_text=text)), [], None)
    db.update(job_id, status="tailoring")  # the watcher was stopped mid-job

    assert db.requeue_interrupted() == 1
    assert w.drain(lambda: False) == 1
    assert db.get(job_id)["status"] == "ready"  # same job row redone without '# force'


def test_health(db):
    ok, detail = healthy(60)
    assert not ok and "no heartbeat" in detail
    make_watcher(db).heartbeat()
    assert healthy(60)[0]
    old = time.time() - 600
    os.utime(config.HEARTBEAT_PATH, (old, old))
    assert not healthy(60)[0]


def test_cleanup_deletes_expired_folders_but_keeps_active_applications(db):
    from jap.cleanup import cleanup

    apps = config.APPLICATIONS_DIR
    for name in ("2026-09-01_0001_old_x", "2026-09-02_0002_applied_x", "2026-10-20_0003_recent_x", "notes"):
        (apps / name).mkdir(parents=True)
    (apps / "tracker.md").write_text("t")
    applied = db.create_job("https://a.example/2", [], None)
    db.update(applied, folder="2026-09-02_0002_applied_x", status="applied")

    today = date(2026, 10, 28)
    assert cleanup(db, today, 30, dry_run=True) == ["2026-09-01_0001_old_x"]
    assert (apps / "2026-09-01_0001_old_x").exists()
    assert cleanup(db, today, 30) == ["2026-09-01_0001_old_x"]
    assert sorted(p.name for p in apps.iterdir()) == [
        "2026-09-02_0002_applied_x", "2026-10-20_0003_recent_x", "notes", "tracker.md"]
    assert cleanup(db, today, 0) == []


def test_watcher_cleans_up_once_a_day(db, monkeypatch):
    calls = []
    monkeypatch.setattr(watcher, "cleanup", lambda _db, today: calls.append(today) or [])
    w = make_watcher(db)
    w.daily_cleanup(date(2026, 10, 28))
    w.daily_cleanup(date(2026, 10, 28))
    w.daily_cleanup(date(2026, 10, 29))
    assert calls == [date(2026, 10, 28), date(2026, 10, 29)]
