import pytest

from jap.models import JobPosting
from jap.select_track import choose_track


def posting(title, keywords, must_have=()):
    return JobPosting(company="Acme", title=title, location=None, remote="remote", seniority="senior", mission=None,
                      responsibilities=[], must_have=list(must_have), nice_to_have=[], keywords=keywords,
                      visa_or_location_constraints=None, salary=None, asks_for_cover_letter=False)


@pytest.mark.parametrize("p,expected", [
    (posting("Backend Engineer, Industrial IoT", ["machine-data ingestion", "OEE/TEEP KPIs", "industrial", "Python", "manufacturing"]), "industrial_iot"),
    (posting("Senior Backend Engineer, Payments", ["payments", "ledger", "USDC", "wallet", "Node.js", "fintech"]), "fintech_web3"),
    (posting("Full-Stack Engineer", ["React", "TypeScript", "frontend", "Node.js", "dashboard"]), "fullstack"),
    (posting("Backend Engineer (Python)", ["Python", "FastAPI", "PostgreSQL", "REST API design", "microservices"]), "backend"),
])
def test_picks_the_obvious_track(profile, tracks, p, expected):
    assert choose_track(p, tracks, profile)[0] == expected


def test_aliases_count_as_matches(profile, tracks):
    track_id, scores, _ = choose_track(posting("Backend Engineer", ["Postgres", "Node"]), tracks, profile)
    backend = next(s for s in scores if s.track_id == "backend")
    assert "PostgreSQL" in backend.skills and "Node.js" in backend.skills


def test_hint_overrides(profile, tracks):
    assert choose_track(posting("Backend Engineer", ["Python"]), tracks, profile, hint="fintech_web3")[0] == "fintech_web3"
    with pytest.raises(ValueError):
        choose_track(posting("x", []), tracks, profile, hint="devops")
