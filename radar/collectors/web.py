"""Company homepages: the home page plus About, Team, and Careers pages, for memos.

Rules:
- robots.txt is fetched first and obeyed for our user agent. A 401 or 403 on robots.txt means
  "disallow everything"; a 404 means no restrictions (RFC 9309).
- One page per kind (about, team, careers), found from links on the home page, same site only.
- Every response is cached as a raw snapshot with its SHA-256 and fetch time; text is always
  re-extracted from the cached HTML, so a citation points at exactly what the site served.
- Live radar only: pages are current, so the backtest never reads them.
"""

from __future__ import annotations

import contextlib
import logging
import re
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from radar.collectors.base import USER_AGENT, HttpCollector
from radar.collectors.common import clean_text, upsert
from radar.store.models import WebPage

log = logging.getLogger(__name__)

AGENT_TOKEN = "FrontierRadar"
MAX_TEXT = 20_000
SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe", "head"}
BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "tr", "br", "td", "header", "footer"}
PAGE_KINDS = {
    "team": re.compile(r"\b(team|people|founders?|leadership|who-we-are|who we are)\b", re.I),
    "about": re.compile(r"\b(about|about-us|company|our-story|story|mission)\b", re.I),
    "careers": re.compile(r"\b(careers?|jobs|join(-us)?|hiring|work-with-us|open-roles)\b", re.I),
}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._href: str | None = None
        self._anchor: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS and tag != "head":
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor = []

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and tag != "head" and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._anchor).strip()))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._anchor.append(data.strip())


def extract(html: str) -> tuple[str, str, list[tuple[str, str]]]:
    """(title, visible text, links) from an HTML page."""
    p = _Extractor()
    with contextlib.suppress(Exception):  # malformed HTML: keep what was parsed
        p.feed(html)
    lines = [clean_text(re.sub(r"\s+", " ", ln).strip()) for ln in "".join(p.parts).split("\n")]
    text = "\n".join(ln for ln in lines if ln)  # one line per block element, cleaned line by line
    return clean_text(p.title.strip())[:300], text[:MAX_TEXT], p.links


def same_site(url: str, domain: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host == domain or host.endswith("." + domain)


def pick_subpages(base: str, domain: str, links: list[tuple[str, str]]) -> dict[str, str]:
    """At most one URL per page kind, preferring the shortest path that matches."""
    found: dict[str, str] = {}
    for href, anchor in links:
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urljoin(base, href).split("#")[0]
        if not same_site(url, domain):
            continue
        path = urlparse(url).path.rstrip("/")
        if not path:
            continue
        hay = f"{path.replace('/', ' ')} {anchor}"
        for kind, rx in PAGE_KINDS.items():
            if rx.search(hay) and (kind not in found or len(path) < len(urlparse(found[kind]).path)):
                found[kind] = url
                break
    return found


class WebCollector(HttpCollector):
    source = "web"
    default_ttl = timedelta(days=7)

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._robots: dict[str, RobotFileParser | None] = {}

    def robots(self, domain: str) -> RobotFileParser | None:
        """None means fetching is not allowed at all."""
        if domain in self._robots:
            return self._robots[domain]
        rp = RobotFileParser()
        try:
            f = self.fetch(f"https://{domain}/robots.txt", parse="text", ok_statuses=(200, 401, 403, 404, 410))
        except Exception as exc:  # unreachable site: do not crawl it
            log.info("robots.txt for %s unavailable (%s); skipping site", domain, exc)
            self._robots[domain] = None
            return None
        if f.status in (401, 403):
            self._robots[domain] = None
            return None
        rp.parse(((f.payload or {}).get("text") or "").splitlines() if f.status == 200 else [])
        self._robots[domain] = rp
        return rp

    def allowed(self, url: str, domain: str) -> bool:
        rp = self.robots(domain)
        return bool(rp and rp.can_fetch(AGENT_TOKEN, url) and rp.can_fetch(USER_AGENT, url))

    def page(self, url: str, domain: str, kind: str) -> tuple[WebPage | None, list[tuple[str, str]]]:
        if not self.allowed(url, domain):
            log.info("robots.txt disallows %s", url)
            return None, []
        try:
            f = self.fetch(url, parse="text", ok_statuses=(200,))
        except Exception as exc:
            log.info("could not fetch %s (%s)", url, exc)
            return None, []
        payload = f.payload or {}
        if "html" not in str(payload.get("content_type") or "").lower():
            return None, []
        title, text, links = extract(payload.get("text") or "")
        if len(text) < 40:  # JavaScript-rendered page with no server-side text
            return None, links
        row = upsert(self.session, WebPage, {"url": url},
                     {"domain": domain, "page_kind": kind, "title": title or None, "text": text,
                      "snapshot_id": f.snapshot_id, "fetched_at": datetime.now(UTC)})
        return row, links

    def collect_site(self, domain: str) -> list[WebPage]:
        """Home page, then one About, Team, and Careers page found from its links."""
        domain = domain.lower().removeprefix("www.")
        home_url = f"https://{domain}/"
        home, links = self.page(home_url, domain, "home")
        pages = [home] if home else []
        for kind, url in pick_subpages(home_url, domain, links).items():
            row, _ = self.page(url, domain, kind)
            if row:
                pages.append(row)
        self.session.flush()
        return pages
