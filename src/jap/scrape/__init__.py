"""Fetch a job posting as plain text.

Adapters are tried in order: ATS APIs (stable JSON), schema.org JSON-LD, readable HTML.
A page that yields too little text, or a site that blocks scraping, raises NeedsManualJD so
the job waits for a pasted description instead of being tailored against garbage.
"""

import logging
from urllib.parse import urlsplit

import httpx

from jap.scrape import ats, html
from jap.scrape.base import NeedsManualJD, ScrapeError, Scraped

__all__ = ["NeedsManualJD", "ScrapeError", "Scraped", "make_client", "scrape", "scrape_context"]

log = logging.getLogger("jap.scrape")
MIN_TEXT_CHARS = 400
BLOCKED_HOSTS = ("linkedin.com", "indeed.com", "glassdoor.com", "glassdoor.co.uk")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


def make_client() -> httpx.Client:
    return httpx.Client(follow_redirects=True, timeout=20, headers={"User-Agent": USER_AGENT})


def fetch(client: httpx.Client, url: str) -> httpx.Response:
    last: Exception | None = None
    for _ in range(3):
        try:
            response = client.get(url)
        except httpx.TransportError as e:
            last = e
            continue
        if response.status_code >= 500:
            last = ScrapeError(f"{url}: HTTP {response.status_code}")
            continue
        if response.status_code >= 400:
            raise ScrapeError(f"{url}: HTTP {response.status_code}")
        return response
    raise ScrapeError(f"{url}: {last}")


def is_blocked(url: str) -> bool:
    host = urlsplit(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in BLOCKED_HOSTS)


def scrape(url: str, client: httpx.Client) -> Scraped:
    if is_blocked(url):
        raise NeedsManualJD(f"{urlsplit(url).netloc} blocks scraping; paste the description into inbox/manual/")

    if api := ats.match(url):
        name, api_url, parse = api
        try:
            result = parse(fetch(client, api_url).text, url)
        except NeedsManualJD:
            raise
        except (ScrapeError, ValueError, KeyError) as e:
            log.info("%s API didn't return the job (%s); trying the page itself", name, e)
        else:
            if len(result.text) >= MIN_TEXT_CHARS:
                return result

    page = fetch(client, url)
    if "html" not in page.headers.get("content-type", "html"):
        raise NeedsManualJD(f"{url} is not an HTML page ({page.headers.get('content-type')})")
    for extractor in (html.from_jsonld, html.from_readable):
        result = extractor(page.text, url)
        if result and len(result.text) >= MIN_TEXT_CHARS:
            return result
    raise NeedsManualJD(f"{url}: could not find a job description on the page (JavaScript-only site?)")


def scrape_context(url: str, client: httpx.Client) -> Scraped | None:
    """Secondary pages (mission, about): readable text only, failures are not fatal."""
    try:
        return html.from_readable(fetch(client, url).text, url)
    except ScrapeError:
        return None
