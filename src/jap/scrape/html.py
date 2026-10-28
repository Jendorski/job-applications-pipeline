import json
import re

import trafilatura
from bs4 import BeautifulSoup

from jap.scrape.base import Scraped

BLOCK_TAGS = ["p", "div", "section", "article", "br", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "tr"]


def html_to_text(markup: str) -> str:
    soup = BeautifulSoup(markup, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
    text = soup.get_text()
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", "\n".join(line.strip() for line in text.splitlines())).strip()


def _job_postings(node):
    if isinstance(node, list):
        for item in node:
            yield from _job_postings(item)
    elif isinstance(node, dict):
        kind = node.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            yield node
        yield from _job_postings(node.get("@graph", []))


def _location(posting: dict) -> str | None:
    locations = posting.get("jobLocation") or []
    if isinstance(locations, dict):
        locations = [locations]
    names = []
    for loc in locations:
        address = loc.get("address") if isinstance(loc, dict) else None
        if isinstance(address, dict):
            names.append(", ".join(v for k in ("addressLocality", "addressRegion", "addressCountry")
                                   if isinstance(v := address.get(k), str) and v))
        elif isinstance(address, str):
            names.append(address)
    if posting.get("jobLocationType") == "TELECOMMUTE":
        names.append("Remote")
    return "; ".join(n for n in names if n) or None


def from_jsonld(page: str, url: str):
    soup = BeautifulSoup(page, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "", strict=False)
        except json.JSONDecodeError:
            continue
        for posting in _job_postings(data):
            org = posting.get("hiringOrganization")
            company = org.get("name") if isinstance(org, dict) else org if isinstance(org, str) else None
            title = posting.get("title") or ""
            head = [f"# {title}", f"Company: {company}" if company else "", f"Location: {loc}" if (loc := _location(posting)) else "",
                    f"Employment type: {posting['employmentType']}" if isinstance(posting.get("employmentType"), str) else ""]
            text = "\n".join(h for h in head if h) + "\n\n" + html_to_text(posting.get("description") or "")
            return Scraped(url, "jsonld", text, page, "html", company, title)
    return None


def from_readable(page: str, url: str):
    text = trafilatura.extract(page, include_tables=True, include_comments=False, favor_recall=True) or ""
    if not text:
        return None
    soup = BeautifulSoup(page, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else None
    return Scraped(url, "readable", text, page, "html", None, title)
