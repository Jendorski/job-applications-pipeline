import json
import logging
from pathlib import Path

import typer

from jap import config
from jap.db import DB, USER_STATUSES
from jap.models import TailorPlan
from jap.profile import lint, load_profile, load_tracks
from jap.render.context import PlanError, plan_from_track

app = typer.Typer(no_args_is_help=True, help="Job application pipeline")
profile_app = typer.Typer(no_args_is_help=True, help="Master profile tools")
app.add_typer(profile_app, name="profile")


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(message)s")
    for noisy in ("httpx", "httpx2", "httpcore", "anthropic", "openai", "trafilatura"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@app.command()
def init() -> None:
    """Create profile/ from examples/profile/ (never overwrites) and the inbox, applications and data folders."""
    import shutil

    target = config.PROFILE_PATH.parent
    if target.exists() and any(target.iterdir()):
        typer.echo(f"{target} already exists; left as is")
    else:
        # An empty folder is filled in place: Docker creates bind-mount folders before the command runs.
        shutil.copytree(config.EXAMPLE_PROFILE_DIR, target, dirs_exist_ok=True)
        typer.echo(f"created {target} from {config.EXAMPLE_PROFILE_DIR}")
        typer.echo("  replace the example candidate (John Doe) with your own facts, then run: jap profile check")
    for folder in (config.INBOX_DIR, config.APPLICATIONS_DIR, config.DATA_DIR):
        folder.mkdir(parents=True, exist_ok=True)


@profile_app.command("check")
def profile_check(
    profile: Path = typer.Option(config.PROFILE_PATH),
    tracks: Path = typer.Option(config.TRACKS_PATH),
) -> None:
    """Lint master_profile.yaml and tracks.yaml."""
    _require_profile(profile)
    p = load_profile(profile)
    issues = lint(p, load_tracks(tracks) if tracks.exists() else None)
    for issue in issues:
        typer.echo(str(issue))
    errors = sum(i.level == "error" for i in issues)
    bullets = [b for r in p.experience for b in r.bullets]
    typer.echo(
        f"{len(p.experience)} roles, {len(bullets)} bullets "
        f"({sum(b.status == 'verified' for b in bullets)} verified), {len(p.allow_list)} skills; "
        f"{errors} errors, {len(issues) - errors} warnings"
    )
    raise typer.Exit(1 if errors else 0)


@app.command()
def apply(
    urls: list[str] = typer.Argument(None, help="Posting URL first, then optional company/mission pages"),
    jd_file: Path = typer.Option(None, help="Pasted job description (skips scraping)"),
    track: str = typer.Option(None, help="Force a track instead of auto-selecting"),
    note: str = typer.Option(None),
    force: bool = typer.Option(False, help="Redo a job that already exists"),
    pdf: bool = typer.Option(True, help="Convert to PDF and enforce page budgets (needs LibreOffice)"),
) -> None:
    """Run one job end to end and print where its application folder is."""
    from jap.llm import get_provider
    from jap.pipeline import JobInput, run_job

    _require_profile()
    if not urls and not jd_file:
        raise typer.BadParameter("give a URL, --jd-file, or both")
    settings = _settings()
    try:
        provider = get_provider(settings)
    except config.ConfigError as e:
        typer.echo(f"config error: {e}", err=True)
        raise typer.Exit(2)
    job = JobInput(urls=urls or [], jd_text=jd_file.read_text() if jd_file else None, track_hint=track, note=note, force=force)
    row = run_job(job, settings=settings, provider=provider, db=DB(config.DB_PATH), pdf=pdf)
    typer.echo(f"job {row['id']:04d}: {row['status']}" + (f": {row['error']}" if row["error"] else ""))
    if row["folder"]:
        typer.echo(f"  {config.APPLICATIONS_DIR / row['folder']}")
    raise typer.Exit(0 if row["status"] in ("ready", "needs_review") else 1)


@app.command()
def watch(
    poll: int = typer.Option(config.POLL_SECONDS, help="Seconds between inbox checks"),
    pdf: bool = typer.Option(True, help="Convert to PDF and enforce page budgets (needs LibreOffice)"),
) -> None:
    """Run forever: pick up inbox/links.txt, inbox/manual/*.md and inbox/status.txt, process the queue."""
    from logging.handlers import RotatingFileHandler

    from jap.llm import get_provider
    from jap.watcher import Watcher

    _require_profile()
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    for handler in root.handlers:
        handler.setFormatter(fmt)
    file_handler = RotatingFileHandler(config.LOG_DIR / "jap.log", maxBytes=1_000_000, backupCount=5)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    settings = _settings()
    try:
        provider = get_provider(settings)
    except config.ConfigError as e:
        typer.echo(f"config error: {e}", err=True)
        raise typer.Exit(2)
    Watcher(settings, provider, DB(config.DB_PATH), pdf=pdf).run(poll)


@app.command()
def health(max_age: int = typer.Option(600, help="Seconds since the last heartbeat before unhealthy")) -> None:
    """Exit 0 if the watcher has checked in recently (used by the Docker health check)."""
    from jap.watcher import healthy

    ok, detail = healthy(max_age)
    typer.echo(("healthy: " if ok else "unhealthy: ") + detail)
    raise typer.Exit(0 if ok else 1)


@app.command()
def mark(job_id: int, status: str, note: str = typer.Option(None)) -> None:
    """Record what happened to an application: applied, interviewing, offer, rejected, withdrawn, skipped."""
    from jap.tracker import write_tracker

    if status not in USER_STATUSES:
        raise typer.BadParameter(f"status must be one of: {', '.join(USER_STATUSES)}")
    db = DB(config.DB_PATH)
    if db.get(job_id) is None:
        raise typer.BadParameter(f"no job {job_id}")
    db.update(job_id, status=status, **({"note": note} if note else {}))
    write_tracker(db, config.APPLICATIONS_DIR)
    typer.echo(f"job {job_id:04d}: {status}")


@app.command()
def tracker() -> None:
    """Regenerate and print applications/tracker.md."""
    from jap.tracker import write_tracker

    write_tracker(DB(config.DB_PATH), config.APPLICATIONS_DIR)
    typer.echo((config.APPLICATIONS_DIR / "tracker.md").read_text())


@app.command("cleanup")
def cleanup_cmd(
    days: int = typer.Option(None, help="Keep period in days (default: APPLICATIONS_KEEP_DAYS or 30)"),
    dry_run: bool = typer.Option(False, help="List what would be deleted without deleting"),
) -> None:
    """Delete application folders older than the keep period (the watcher also does this daily)."""
    from datetime import date

    from jap.cleanup import KEEP_DAYS, cleanup

    names = cleanup(DB(config.DB_PATH), date.today(), days if days is not None else KEEP_DAYS, dry_run)
    typer.echo(f"{'would delete' if dry_run else 'deleted'} {len(names)} folder(s)")


@app.command()
def usage() -> None:
    """LLM token use and cost per month and model."""
    settings = _settings()
    db = DB(config.DB_PATH)
    for row in db.usage_summary():
        typer.echo(json.dumps(row))
    typer.echo(f"this month: ${db.month_cost():.4f} of ${settings.monthly_budget_usd:.2f} budget")


@app.command()
def render(
    plan_path: Path = typer.Argument(..., help="TailorPlan JSON"),
    out: Path = typer.Option(..., help="Output folder"),
    pdf: bool = typer.Option(True, help="Also convert to PDF (needs LibreOffice)"),
) -> None:
    """Render a TailorPlan into CV and cover letter (docx, and PDF when available)."""
    _render(TailorPlan.model_validate(json.loads(plan_path.read_text())), out, pdf)


@app.command("render-track")
def render_track(
    track_ids: list[str] = typer.Argument(None, help="Track ids (default: all)"),
    out: Path = typer.Option(..., help="Output folder"),
    pdf: bool = typer.Option(True, help="Also convert to PDF (needs LibreOffice)"),
) -> None:
    """Render the untailored base CV of each track."""
    _require_profile()
    tracks = load_tracks(config.TRACKS_PATH)
    for track_id in track_ids or list(tracks):
        if track_id not in tracks:
            raise typer.BadParameter(f"unknown track '{track_id}' (have: {', '.join(tracks)})")
        _render(plan_from_track(tracks[track_id]), out, pdf)


def _settings() -> config.Settings:
    try:
        return config.load_settings()
    except config.ConfigError as e:
        typer.echo(f"config error: {e}", err=True)
        raise typer.Exit(2)


def _require_profile(path: Path | None = None) -> None:
    path = path or config.PROFILE_PATH
    if not path.exists():
        typer.echo(f"no profile at {path}: run `jap init` to start from the example profile", err=True)
        raise typer.Exit(2)


def _render(plan: TailorPlan, out: Path, pdf: bool) -> None:
    from jap.pipeline import render_application

    _require_profile()
    profile = load_profile(config.PROFILE_PATH)
    tracks = load_tracks(config.TRACKS_PATH)
    track = tracks.get(plan.track)
    if track is None:
        raise typer.BadParameter(f"unknown track '{plan.track}' (have: {', '.join(tracks)})")
    try:
        rendered = render_application(profile, track, plan, out, pdf)
    except PlanError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    for f in rendered.files:
        typer.echo(f"wrote {f}")
    for name, pages in rendered.pages.items():
        if pages:
            typer.echo(f"  {name}: {pages} page(s)")
    for n in rendered.notes:
        typer.echo(f"  note: {n}")
