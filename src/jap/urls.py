import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = re.compile(r"^(utm_.*|gh_src|ref|referrer|source|src|lever-source.*|lever-origin|trk.*|fbclid|gclid|mc_.*)$", re.I)


def normalize_url(url: str) -> str:
    """Canonical form for dedup: lowercase host, no fragment, no tracking params, sorted query."""
    parts = urlsplit(url.strip())
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k))
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower(), path, urlencode(query), ""))


def slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len].rstrip("-") or "unknown"
