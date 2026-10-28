import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from jap.models import Profile, Track

YM = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@dataclass
class Issue:
    level: Literal["error", "warning"]
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():7} {self.where}: {self.message}"


def load_profile(path: Path) -> Profile:
    return Profile.model_validate(yaml.safe_load(path.read_text()))


def load_tracks(path: Path) -> dict[str, Track]:
    raw = yaml.safe_load(path.read_text())
    return {key: Track.model_validate({**value, "id": key}) for key, value in raw.items()}


def find_never_claim(text: str, terms: list[str]) -> list[str]:
    return [t for t in terms if re.search(rf"(?<!\w){re.escape(t)}(?!\w)", text, re.IGNORECASE)]


def lint(profile: Profile, tracks: dict[str, Track] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    allow = profile.allow_list

    def err(where: str, msg: str) -> None:
        issues.append(Issue("error", where, msg))

    def warn(where: str, msg: str) -> None:
        issues.append(Issue("warning", where, msg))

    seen: set[str] = set()

    def unique(item_id: str) -> None:
        if item_id in seen:
            err(item_id, "duplicate id")
        seen.add(item_id)

    group_of: dict[str, str] = {}
    for group, skills in profile.skills.items():
        for s in skills:
            if s in group_of:
                warn(f"skills.{group}", f"'{s}' also listed under {group_of[s]}")
            group_of[s] = group

    for alias, target in profile.aliases.items():
        if target not in allow:
            err(f"aliases.{alias}", f"target '{target}' is not a canonical skill")

    if not YM.match(profile.person.career_start):
        err("person.career_start", "must be YYYY-MM")

    for role in profile.experience:
        unique(role.id)
        for field in ("start", "end"):
            value = getattr(role, field)
            if value is not None and not YM.match(value):
                err(f"{role.id}.{field}", f"'{value}' must be YYYY-MM")
        if role.end and role.end < role.start:
            err(role.id, "end is before start")
        for b in role.bullets:
            unique(b.id)
            if not b.id.startswith(f"{role.id}."):
                warn(b.id, f"bullet id should start with '{role.id}.'")
            if b.status != "verified":
                continue
            for s in b.skills:
                if s not in allow:
                    err(b.id, f"skill '{s}' is not in the allow-list")
            for term in find_never_claim(b.text, profile.never_claim):
                err(b.id, f"text contains never_claim term '{term}'")

    for p in profile.projects:
        unique(p.id)
        if p.status != "verified":
            continue
        for s in p.stack:
            if s not in allow:
                err(p.id, f"stack item '{s}' is not in the allow-list")
        for term in find_never_claim(p.text, profile.never_claim):
            err(p.id, f"text contains never_claim term '{term}'")

    for e in profile.education:
        unique(e.id)
        if not YM.match(e.end):
            err(f"{e.id}.end", f"'{e.end}' must be YYYY-MM")

    for track in (tracks or {}).values():
        issues += lint_track(profile, track)
    return issues


def lint_track(profile: Profile, track: Track) -> list[Issue]:
    issues: list[Issue] = []
    where = f"tracks.{track.id}"
    allow = profile.allow_list

    if not track.headlines:
        issues.append(Issue("error", where, "needs at least one headline"))
    for term in find_never_claim(track.summary, profile.never_claim):
        issues.append(Issue("error", where, f"summary contains never_claim term '{term}'"))
    for group in track.skill_groups:
        for s in group.skills:
            if s not in allow:
                issues.append(Issue("error", f"{where}.{group.label}", f"skill '{s}' is not in the allow-list"))
    for role_id, bullet_ids in track.experience.items():
        role = profile.role(role_id)
        if role is None:
            issues.append(Issue("error", where, f"unknown role '{role_id}'"))
            continue
        if not role.show_on_cv:
            issues.append(Issue("error", where, f"role '{role_id}' is hidden (show_on_cv: false)"))
        verified = {b.id for b in role.bullets if b.status == "verified"}
        for bid in bullet_ids:
            if bid not in verified:
                issues.append(Issue("error", where, f"'{bid}' is not a verified bullet of {role_id}"))
    for pid in track.projects:
        p = profile.project(pid)
        if p is None or p.status != "verified":
            issues.append(Issue("error", where, f"'{pid}' is not a verified project"))
    return issues
