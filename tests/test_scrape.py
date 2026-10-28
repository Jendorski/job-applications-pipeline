from pathlib import Path

import httpx
import pytest

from jap.scrape import NeedsManualJD, ats, scrape
from jap.scrape.html import from_jsonld, from_readable, html_to_text
from jap.urls import normalize_url

FIX = Path(__file__).parent / "fixtures" / "scrape"


def client_serving(routes: dict[str, tuple[str, str]]) -> httpx.Client:
    """routes: url prefix → (content-type, body). Anything else is a 404."""
    def handler(request: httpx.Request) -> httpx.Response:
        for prefix, (ctype, body) in routes.items():
            if str(request.url).startswith(prefix):
                return httpx.Response(200, text=body, headers={"content-type": ctype})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_normalize_url_drops_tracking_but_keeps_job_ids():
    assert normalize_url("https://Stripe.com/jobs/search?gh_jid=6176758&utm_source=x&gh_src=y#apply") == \
        "https://stripe.com/jobs/search?gh_jid=6176758"
    assert normalize_url("https://jobs.lever.co/a/b/?lever-source=LinkedIn") == "https://jobs.lever.co/a/b"


@pytest.mark.parametrize("url,adapter", [
    ("https://job-boards.greenhouse.io/company-g/jobs/8704363002", "greenhouse"),
    ("https://boards.greenhouse.io/embed/job_app?for=company-g&token=8704363002", "greenhouse"),
    ("https://jobs.lever.co/company-i/10dfc8bc-99ad-4ca2-ab76-853cb90a92c2", "lever"),
    ("https://jobs.eu.lever.co/acme/10dfc8bc-99ad-4ca2-ab76-853cb90a92c2", "lever"),
    ("https://jobs.ashbyhq.com/company-h/7bfa613e-151c-469b-9973-c89ee3d14838", "ashby"),
    ("https://www.company-j.com/company/careers/roles/7520397?gh_jid=7520397", "greenhouse"),
    ("https://example.com/careers/backend", None),
])
def test_ats_detection(url, adapter):
    found = ats.match(url)
    assert (found[0] if found else None) == adapter


def test_gh_jid_on_company_site_guesses_the_board():
    assert ats.match("https://www.company-j.com/company/careers/roles/7520397?gh_jid=7520397")[1] == \
        "https://boards-api.greenhouse.io/v1/boards/company-j/jobs/7520397?questions=true"
    assert ats.match("https://careers.acme.io/openings?gh_jid=42")[1].endswith("/boards/acme/jobs/42?questions=true")


def test_api_miss_falls_back_to_the_page():
    page = (FIX / "jsonld_ashby_company_h_page.html").read_text()
    c = client_serving({"https://www.company-j.com/": ("text/html", page)})  # the API guess 404s
    assert scrape("https://www.company-j.com/company/careers/roles/7520397?gh_jid=7520397", c).source == "jsonld"


def test_greenhouse_api():
    url = "https://job-boards.greenhouse.io/company-g/jobs/8704363002"
    c = client_serving({"https://boards-api.greenhouse.io/": ("application/json", (FIX / "greenhouse_company_g_8704363002.json").read_text())})
    s = scrape(url, c)
    assert s.source == "greenhouse" and s.company_hint == "Company G"
    assert s.text.startswith("# Backend Engineer (Python)") and "<p>" not in s.text and "&lt;" not in s.text


def test_lever_api():
    url = "https://jobs.lever.co/company-i/10dfc8bc-99ad-4ca2-ab76-853cb90a92c2"
    c = client_serving({"https://api.lever.co/": ("application/json", (FIX / "lever_company_i_10dfc8bc.json").read_text())})
    s = scrape(url, c)
    assert s.source == "lever" and s.text.startswith("# Backend Software Engineer")
    assert "## " in s.text  # Lever's requirement lists become sections


def test_ashby_api_and_missing_job():
    board = (FIX / "ashby_company_h_board.json").read_text()
    c = client_serving({"https://api.ashbyhq.com/": ("application/json", board)})
    s = scrape("https://jobs.ashbyhq.com/company-h/7bfa613e-151c-469b-9973-c89ee3d14838", c)
    assert s.source == "ashby" and "Backend Engineer" in s.text
    with pytest.raises(NeedsManualJD):
        scrape("https://jobs.ashbyhq.com/company-h/00000000-0000-0000-0000-000000000000", c)


def test_jsonld_from_real_page():
    s = from_jsonld((FIX / "jsonld_ashby_company_h_page.html").read_text(), "https://x")
    assert s and s.source == "jsonld" and s.title_hint and len(s.text) > 400


def test_readable_fallback_when_no_jsonld():
    page = (FIX / "html_greenhouse_company_g_page.html").read_text()
    assert from_jsonld(page, "https://x") is None
    s = from_readable(page, "https://x")
    assert s and "Agent Core" in s.text


def test_generic_page_goes_through_jsonld_then_readable():
    c = client_serving({"https://careers.example.com/": ("text/html; charset=utf-8", (FIX / "jsonld_ashby_company_h_page.html").read_text())})
    assert scrape("https://careers.example.com/jobs/1", c).source == "jsonld"


def test_blocked_and_empty_pages_need_a_manual_description():
    with pytest.raises(NeedsManualJD):
        scrape("https://www.linkedin.com/jobs/view/123", client_serving({}))
    c = client_serving({"https://spa.example.com/": ("text/html", "<html><body><div id=root></div></body></html>")})
    with pytest.raises(NeedsManualJD):
        scrape("https://spa.example.com/job", c)


def test_html_to_text_keeps_list_structure():
    assert html_to_text("<p>Intro</p><ul><li>One</li><li>Two</li></ul>") == "Intro\n\n- One\n- Two"
