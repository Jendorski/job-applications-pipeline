from dataclasses import dataclass, field


class ScrapeError(RuntimeError):
    pass


class NeedsManualJD(ScrapeError):
    pass


@dataclass
class Scraped:
    url: str | None
    source: str  # greenhouse | lever | ashby | jsonld | readable | manual
    text: str
    raw: str
    raw_ext: str  # json | html | md
    company_hint: str | None = None
    title_hint: str | None = None
    questions: list = field(default_factory=list)  # FormQuestion, when the ATS exposes the application form
