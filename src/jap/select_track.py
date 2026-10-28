"""Pick the base CV (track) whose skills and keywords best overlap the posting. Deterministic."""

import re
from dataclasses import dataclass, field

from jap.models import JobPosting, Profile, Track


@dataclass
class TrackScore:
    track_id: str
    score: float
    skills: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


def _found(term: str, text: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(term.lower())}(?![\w])", text) is not None


def posting_text(posting: JobPosting) -> str:
    parts = [posting.title, posting.mission or "", *posting.responsibilities, *posting.must_have,
             *posting.nice_to_have, *posting.keywords]
    return " \n".join(parts).lower()


def score_tracks(posting: JobPosting, tracks: dict[str, Track], profile: Profile) -> list[TrackScore]:
    text = posting_text(posting)
    title = posting.title.lower()
    aliases_of: dict[str, list[str]] = {}
    for alias, canonical in profile.aliases.items():
        aliases_of.setdefault(canonical, []).append(alias)

    scores = []
    for track in tracks.values():
        skills = {s for g in track.skill_groups for s in g.skills}
        hit_skills = sorted(s for s in skills if any(_found(t, text) for t in [s, *aliases_of.get(s, [])]))
        hit_keywords = sorted(k for k in track.keywords if _found(k, text))
        in_title = sum(_found(k, title) for k in track.keywords)
        score = len(hit_skills) + 2 * len(hit_keywords) + 3 * in_title
        scores.append(TrackScore(track.id, score, hit_skills, hit_keywords))
    return sorted(scores, key=lambda s: -s.score)


def choose_track(posting: JobPosting, tracks: dict[str, Track], profile: Profile,
                 hint: str | None = None) -> tuple[str, list[TrackScore], str]:
    scores = score_tracks(posting, tracks, profile)
    if hint:
        if hint not in tracks:
            raise ValueError(f"unknown track hint '{hint}' (have: {', '.join(tracks)})")
        return hint, scores, f"set by '# track: {hint}' in the inbox"
    best = scores[0]
    runner_up = scores[1] if len(scores) > 1 else None
    reason = f"{best.score:g} points: {len(best.skills)} skills and {len(best.keywords)} keywords matched"
    if runner_up and runner_up.score >= best.score * 0.85:
        reason += f"; close call with {runner_up.track_id} ({runner_up.score:g})"
    return best.track_id, scores, reason
