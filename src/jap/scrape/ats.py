"""Public job-board APIs of the common applicant tracking systems."""

import html as htmllib
import json
import re
from collections.abc import Callable
from urllib.parse import urlsplit

from jap.models import FormQuestion
from jap.scrape.base import NeedsManualJD, Scraped
from jap.scrape.html import html_to_text

GREENHOUSE = re.compile(r"(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/([^/?#]+)/jobs/(\d+)")
GREENHOUSE_EMBED = re.compile(r"greenhouse\.io/embed/job_app\?.*?for=([^&#]+).*?token=(\d+)")
LEVER = re.compile(r"jobs\.(eu\.)?lever\.co/([^/?#]+)/([0-9a-f-]{36})")
ASHBY = re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})")
GH_JID = re.compile(r"[?&]gh_jid=(\d+)")
NOT_A_BOARD = {"www", "careers", "jobs", "boards", "apply", "work", "join"}


def match(url: str) -> tuple[str, str, Callable] | None:
    """(adapter name, API url, parser) for a posting URL on a known ATS, else None."""
    if m := GREENHOUSE.search(url) or GREENHOUSE_EMBED.search(url):
        board, job_id = m.groups()
        return "greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}?questions=true", parse_greenhouse
    if (m := GH_JID.search(url)) and "greenhouse.io" not in url:
        # A Greenhouse job embedded on the company's own site (stripe.com/jobs/search?gh_jid=…).
        # The board name is usually the company's domain; if the guess is wrong the API 404s
        # and scraping falls back to the page itself.
        labels = [l for l in urlsplit(url).netloc.lower().split(".")[:-1] if l not in NOT_A_BOARD]
        if labels:
            return "greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{labels[-1]}/jobs/{m.group(1)}?questions=true", parse_greenhouse
    if m := LEVER.search(url):
        eu, company, job_id = m.groups()
        return "lever", f"https://api.{'eu.' if eu else ''}lever.co/v0/postings/{company}/{job_id}", parse_lever
    if m := ASHBY.search(url):
        org, job_id = m.groups()
        api = f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"
        return "ashby", api, lambda raw, u: parse_ashby(raw, u, job_id, org)
    return None


def _compose(title: str, company: str | None, location: str | None, body: str, extra: list[str] = ()) -> str:
    head = [f"# {title}"]
    if company:
        head.append(f"Company: {company}")
    if location:
        head.append(f"Location: {location}")
    head += [e for e in extra if e]
    return "\n".join(head) + "\n\n" + body.strip()


FORM_SKIP = re.compile(r"^(resume/cv|cover letter|first name|last name|preferred first name|email|phone|linkedin profile)$", re.I)
ANSWERABLE = {"textarea", "input_text", "multi_value_single_select"}


def greenhouse_questions(d: dict) -> list[FormQuestion]:
    """Free-text and single-choice questions from the posting's application form."""
    out = []
    for q in d.get("questions") or []:
        label = html_to_text(q.get("label") or "").strip()
        fields = q.get("fields") or []
        if not label or FORM_SKIP.match(label) or not any(f.get("type") in ANSWERABLE for f in fields):
            continue
        options = [v.get("label", "") for f in fields for v in (f.get("values") or []) if v.get("label")]
        out.append(FormQuestion(label=label, required=bool(q.get("required")), options=options))
    return out


def parse_greenhouse(raw: str, url: str):
    d = json.loads(raw)
    body = html_to_text(htmllib.unescape(d.get("content") or ""))
    location = (d.get("location") or {}).get("name")
    text = _compose(d["title"], d.get("company_name"), location, body)
    return Scraped(url, "greenhouse", text, raw, "json", d.get("company_name"), d["title"], greenhouse_questions(d))


def parse_lever(raw: str, url: str):
    d = json.loads(raw)
    cats = d.get("categories") or {}
    parts = [d.get("openingPlain") or "", d.get("descriptionBodyPlain") or d.get("descriptionPlain") or ""]
    for section in d.get("lists") or []:
        parts.append(f"## {section.get('text', '')}\n{html_to_text(section.get('content', ''))}")
    parts.append(d.get("additionalPlain") or "")
    extra = [f"Workplace: {d['workplaceType']}" if d.get("workplaceType") else "",
             f"Commitment: {cats['commitment']}" if cats.get("commitment") else ""]
    text = _compose(d["text"], None, cats.get("location"), "\n\n".join(p for p in parts if p.strip()), extra)
    return Scraped(url, "lever", text, raw, "json", None, d["text"])


def parse_ashby(raw: str, url: str, job_id: str, org: str):
    d = json.loads(raw)
    job = next((j for j in d.get("jobs", []) if j.get("id") == job_id), None)
    if job is None:
        raise NeedsManualJD(f"Ashby board '{org}' has no listed job {job_id} (closed or unlisted?)")
    body = job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml") or "")
    extra = [f"Workplace: {job['workplaceType']}" if job.get("workplaceType") else "",
             f"Employment type: {job['employmentType']}" if job.get("employmentType") else ""]
    if comp := (job.get("compensation") or {}).get("compensationTierSummary"):
        extra.append(f"Compensation: {comp}")
    text = _compose(job["title"], None, job.get("location"), body, extra)
    return Scraped(url, "ashby", text, json.dumps(job, indent=1), "json", None, job["title"])
