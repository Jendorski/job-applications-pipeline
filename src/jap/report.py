from jap.llm import Usage
from jap.models import JobPosting, TailorPlan
from jap.select_track import TrackScore


def match_report(*, job_id: int, urls: list[str], source: str, posting: JobPosting, track_id: str, reason: str,
                 scores: list[TrackScore], plan: TailorPlan, problems: list[str], attempts: int,
                 pages: dict[str, int | None], usages: list[tuple[str, Usage, float | None]], notes: list[str]) -> str:
    out = [f"# {posting.title} at {posting.company}", "",
           f"Job {job_id:04d} · scraped via {source} · " + " · ".join(f"<{u}>" for u in urls), ""]

    facts = [("Location", posting.location), ("Remote", posting.remote), ("Seniority", posting.seniority),
             ("Salary", posting.salary), ("Visa / location", posting.visa_or_location_constraints),
             ("Asks for a cover letter", "yes" if posting.asks_for_cover_letter else "no")]
    out += [f"- **{k}:** {v}" for k, v in facts if v] + [""]

    status = "passed" if not problems else f"**FAILED after {attempts} attempts** (review before sending)"
    out += ["## Validation", "", f"{status}.", ""] + [f"- {p}" for p in problems] + ([""] if problems else [])

    out += ["## Track", "", f"**{track_id}**: {reason}", "", "| track | score | matched |", "|---|---|---|"]
    out += [f"| {s.track_id} | {s.score:g} | {', '.join(s.keywords + s.skills)[:140]} |" for s in scores] + [""]

    if plan.keyword_coverage:
        out += ["## Keyword coverage", "", "| keyword | covered | evidence |", "|---|---|---|"]
        out += [f"| {k.keyword} | {'yes' if k.covered else '**no**'} | {', '.join(k.evidence)} |" for k in plan.keyword_coverage]
        out.append("")
    out += ["## Gaps", ""] + ([f"- {g}" for g in plan.gaps] or ["None identified."]) + [""]

    rephrased = [(rid, c) for rid, cs in plan.experience.items() for c in cs if c.rephrased]
    if rephrased:
        out += ["## Rephrased bullets", ""] + [f"- `{c.bullet_id}`: {c.rephrased}" for _, c in rephrased] + [""]

    out += ["## Output", ""] + [f"- {name}: {p} page(s)" if p else f"- {name}: no PDF" for name, p in pages.items()]
    out += [f"- {n}" for n in notes] + [""]

    total = sum(c for _, _, c in usages if c) if usages else 0
    out += ["## Cost", "", "| stage | model | in | out | cache read | cache write | USD |", "|---|---|---|---|---|---|---|"]
    for stage, u, cost in usages:
        out.append(f"| {stage} | {u.model} | {u.input_tokens} | {u.output_tokens} | {u.cache_read_tokens} | "
                   f"{u.cache_write_tokens} | {f'{cost:.4f}' if cost is not None else 'n/a'} |")
    out += ["", f"Total: ${total:.4f}", ""]
    return "\n".join(out)
