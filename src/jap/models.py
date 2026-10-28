import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["verified", "needs_confirmation"]
Source = Literal["code", "cv", "user"]


# --- Master profile -------------------------------------------------------

class Bullet(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    text: str
    status: Status
    source: Source
    priority: int = 2
    tags: list[str] = []
    skills: list[str] = []
    evidence: str | None = None


class Role(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    company: str
    title: str
    location: str | None = None
    start: str
    end: str | None = None
    date_display: str | None = None
    show_on_cv: bool = True
    bullets: list[Bullet]


class Project(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    text: str
    status: Status
    source: Source
    stack: list[str] = []
    repo: str | None = None
    hobby: bool = False


class Education(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    institution: str
    degree: str
    location: str | None = None
    end: str
    status: Status
    source: Source


class Person(BaseModel):
    name: str
    email: str
    links: list[str] = []
    location_line_template: str = "Open to relocating to {location}"
    career_start: str


class Candidate(BaseModel):
    """Facts and motivations for application-form questions (supplied by the candidate)."""
    model_config = ConfigDict(extra="allow")
    preferred_name: str
    website: str | None = None
    linkedin: str | None = None
    based_in: str
    relocation: dict = {}
    work_authorisation: str
    start: str
    salary: dict = {}
    why_looking: str
    proudest_project: dict = {}
    motivation: dict = {}


class Profile(BaseModel):
    model_config = ConfigDict(extra="allow")
    person: Person
    skills: dict[str, list[str]]
    aliases: dict[str, str] = {}
    experience: list[Role]
    projects: list[Project] = []
    education: list[Education] = []
    candidate: Candidate | None = None
    never_claim: list[str] = []

    @property
    def allow_list(self) -> set[str]:
        return {s for group in self.skills.values() for s in group}

    def role(self, role_id: str) -> Role | None:
        return next((r for r in self.experience if r.id == role_id), None)

    def project(self, project_id: str) -> Project | None:
        return next((p for p in self.projects if p.id == project_id), None)


# --- Tracks (base CV variants) --------------------------------------------

class SkillGroup(BaseModel):
    label: str
    skills: list[str]


class Track(BaseModel):
    id: str = ""
    label: str  # used in filenames, e.g. "Backend"
    headlines: list[str]
    summary: str
    skill_groups: list[SkillGroup]
    experience: dict[str, list[str]]
    projects: list[str] = []
    keywords: list[str] = []


# --- LLM output schemas ----------------------------------------------------
# Structured outputs require additionalProperties: false on every object, so these use lists
# rather than dicts, and every field is required (None where a value may be absent).

class JobPosting(BaseModel):
    company: str
    title: str
    location: str | None
    remote: Literal["remote", "hybrid", "onsite", "unknown"]
    seniority: Literal["junior", "mid", "senior", "lead", "principal", "unknown"]
    mission: str | None
    responsibilities: list[str]
    must_have: list[str]
    nice_to_have: list[str]
    keywords: list[str]
    visa_or_location_constraints: str | None
    salary: str | None
    asks_for_cover_letter: bool


class DraftBullet(BaseModel):
    bullet_id: str
    rephrased: str | None


class DraftRole(BaseModel):
    role_id: str
    bullets: list[DraftBullet]


class KeywordHit(BaseModel):
    keyword: str
    covered: bool
    evidence: list[str]  # bullet / project ids


class CoverLetterDraft(BaseModel):
    greeting: str
    paragraphs: list[str]
    sign_off: str


class TailorDraft(BaseModel):
    headline: str
    location: str | None
    summary: str
    skill_groups: list["SkillGroup"]
    experience: list[DraftRole]
    projects: list[str]
    cover_letter: CoverLetterDraft
    keyword_coverage: list[KeywordHit]
    gaps: list[str]


class FormQuestion(BaseModel):
    label: str
    required: bool
    options: list[str] = []  # for select questions; the answer must be one of these


class Answer(BaseModel):
    question: str
    source: Literal["form", "standard"]
    kind: Literal["motivation", "fact", "needs_input"]
    answer: str  # for needs_input: what the candidate has to supply


class AnswerSet(BaseModel):
    answers: list[Answer]


# --- Tailor plan (what the renderer consumes) -------------------------------

class BulletChoice(BaseModel):
    bullet_id: str
    rephrased: str | None = None


class CoverLetter(BaseModel):
    greeting: str
    paragraphs: list[str] = Field(min_length=1)
    sign_off: str = "Kind regards,"
    date: dt.date | None = None


class TailorPlan(BaseModel):
    company: str
    role: str
    track: str
    headline: str
    location: str | None = None  # fills person.location_line_template
    summary: str  # may contain {years}
    skill_groups: list[SkillGroup]
    experience: dict[str, list[BulletChoice]]
    projects: list[str] = []
    include_education: bool = True
    cover_letter: CoverLetter | None = None
    keyword_coverage: list[KeywordHit] = []
    gaps: list[str] = []
    prompt_version: str | None = None


TailorDraft.model_rebuild()
