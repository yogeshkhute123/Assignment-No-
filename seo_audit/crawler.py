"""Polite breadth-first crawler that stays on one site and records everything the checks need."""
import re
import time
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional
from urllib import robotparser
from urllib.parse import urljoin, urldefrag, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = "SEOAuditBot/1.0"
SKIP_EXT = re.compile(
    r"\.(jpe?g|png|gif|webp|svg|ico|avif|pdf|zip|gz|rar|mp[34]|mov|avi|wmv|docx?|xlsx?|pptx?|css|js|json|xml|woff2?|ttf|eot)$",
    re.I)


def normalize(url: str) -> str:
    url, _ = urldefrag(url.strip())
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if p.port and not ((p.scheme == "http" and p.port == 80) or (p.scheme == "https" and p.port == 443)):
        host = f"{host}:{p.port}"
    return urlunparse((p.scheme.lower(), host, p.path or "/", p.params, p.query, ""))


def site_key(url: str) -> str:
    """Host without 'www.' so www and non-www count as the same site."""
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


@dataclass
class Page:
    url: str
    depth: int = 0
    final_url: str = ""
    status: int = 0
    error: str = ""
    content_type: str = ""
    elapsed: float = 0.0
    size: int = 0
    headers: dict = field(default_factory=dict)
    redirects: list = field(default_factory=list)      # [(url, status), ...] hops before final
    html: Optional[str] = None
    internal_links: list = field(default_factory=list)  # (target, anchor_text, nofollow)
    external_links: list = field(default_factory=list)  # (target, anchor_text, nofollow)
    _soup: object = field(default=None, repr=False)

    @property
    def is_html(self) -> bool:
        return self.html is not None

    @property
    def soup(self):
        if self._soup is None and self.html:
            self._soup = BeautifulSoup(self.html, "lxml")
        return self._soup


@dataclass
class Site:
    start_url: str
    session: requests.Session
    pages: dict = field(default_factory=dict)       # normalized url -> Page
    inlinks: dict = field(default_factory=dict)     # normalized url -> set(referring urls)
    robots_status: int = 0
    robots_text: str = ""
    sitemap_urls: list = field(default_factory=list)
    sitemap_sources: list = field(default_factory=list)
    sitemap_errors: list = field(default_factory=list)
    truncated: bool = False

    @property
    def html_pages(self):
        # redirecting URLs are excluded: their body is the destination page, which is audited under its own URL
        return [p for p in self.pages.values() if p.is_html and 200 <= p.status < 300 and not p.redirects]


class Crawler:
    def __init__(self, start_url, max_pages=200, max_depth=6, workers=6, delay=0.0,
                 timeout=15, respect_robots=True, verbose=True):
        if not re.match(r"^https?://", start_url, re.I):
            start_url = "https://" + start_url
        self.start = normalize(start_url)
        self.key = site_key(self.start)
        self.max_pages, self.max_depth = max_pages, max_depth
        self.workers, self.delay, self.timeout = workers, delay, timeout
        self.respect_robots, self.verbose = respect_robots, verbose
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.robots = robotparser.RobotFileParser()

    def log(self, msg):
        if self.verbose:
            print(msg, flush=True)

    # ---- robots.txt / sitemap -------------------------------------------------
    def _load_robots(self, site):
        origin = "{0.scheme}://{0.netloc}".format(urlparse(self.start))
        try:
            r = self.session.get(origin + "/robots.txt", timeout=self.timeout)
            site.robots_status = r.status_code
            if r.status_code == 200 and "text" in r.headers.get("content-type", "text"):
                site.robots_text = r.text
                self.robots.parse(r.text.splitlines())
            else:
                self.robots.parse([])
        except requests.RequestException:
            self.robots.parse([])

    def _load_sitemaps(self, site):
        origin = "{0.scheme}://{0.netloc}".format(urlparse(self.start))
        candidates = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", site.robots_text) or [origin + "/sitemap.xml"]
        queue, seen = deque(candidates), set()
        while queue and len(seen) < 25:
            sm = queue.popleft()
            if sm in seen:
                continue
            seen.add(sm)
            try:
                r = self.session.get(sm, timeout=self.timeout)
                if r.status_code != 200:
                    site.sitemap_errors.append(f"{sm} returned HTTP {r.status_code}")
                    continue
                root = ET.fromstring(r.content)
            except (requests.RequestException, ET.ParseError) as e:
                site.sitemap_errors.append(f"{sm} could not be read: {e}")
                continue
            site.sitemap_sources.append(sm)
            locs = [el.text.strip() for el in root.iter() if el.tag.endswith("}loc") or el.tag == "loc" if el.text]
            if root.tag.endswith("sitemapindex"):
                queue.extend(locs)
            else:
                site.sitemap_urls.extend(normalize(u) for u in locs)

    # ---- fetching ---------------------------------------------------------------
    def _fetch(self, url, depth):
        page = Page(url=url, depth=depth)
        if self.delay:
            time.sleep(self.delay)
        current, start = url, time.time()
        try:
            for _ in range(10):
                r = self.session.get(current, timeout=self.timeout, allow_redirects=False)
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    page.redirects.append((current, r.status_code))
                    current = normalize(urljoin(current, r.headers["location"]))
                    continue
                break
            else:
                page.error = "Too many redirects"
                return page
        except requests.RequestException as e:
            page.error = type(e).__name__
            return page
        page.elapsed = time.time() - start
        page.final_url, page.status = current, r.status_code
        page.headers = {k.lower(): v for k, v in r.headers.items()}
        page.content_type = page.headers.get("content-type", "")
        page.size = len(r.content)
        if "html" in page.content_type.lower():
            page.html = r.text
            self._extract_links(page)
        return page

    def _extract_links(self, page):
        soup = page.soup
        base = soup.find("base", href=True)
        base_url = urljoin(page.final_url, base["href"]) if base else page.final_url
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:", "#", "sms:", "data:")):
                continue
            target = normalize(urljoin(base_url, href))
            if not target.startswith(("http://", "https://")):
                continue
            text = " ".join(a.get_text(" ", strip=True).split())
            if not text:
                img = a.find("img")
                text = (img.get("alt") or "").strip() if img else ""
            nofollow = "nofollow" in (a.get("rel") or [])
            bucket = page.internal_links if site_key(target) == self.key else page.external_links
            bucket.append((target, text, nofollow))

    # ---- main loop --------------------------------------------------------------
    def crawl(self) -> Site:
        site = Site(self.start, self.session)
        self.log(f"Crawling {self.start} (max {self.max_pages} pages)")
        self._load_robots(site)
        self._load_sitemaps(site)
        self.log(f"  robots.txt: HTTP {site.robots_status or 'n/a'}; sitemap URLs: {len(site.sitemap_urls)}")

        queued = {self.start}
        level = [self.start]
        depth = 0
        extra = [u for u in dict.fromkeys(site.sitemap_urls) if site_key(u) == self.key]
        while level and len(site.pages) < self.max_pages:
            batch = []
            for u in level:
                if len(site.pages) + len(batch) >= self.max_pages:
                    site.truncated = True
                    break
                if self.respect_robots and not self.robots.can_fetch(USER_AGENT, u):
                    continue
                batch.append(u)
            with ThreadPoolExecutor(self.workers) as pool:
                results = list(pool.map(lambda u: self._fetch(u, depth), batch))
            nxt = []
            for page in results:
                site.pages[page.url] = page
                self.log(f"  [{len(site.pages):>4}] {page.status or page.error:<6} {page.url}")
                for target, _, _ in page.internal_links:
                    site.inlinks.setdefault(target, set()).add(page.url)
                    if target not in queued and not SKIP_EXT.search(urlparse(target).path) and depth < self.max_depth:
                        queued.add(target)
                        nxt.append(target)
            if depth == 0:   # sitemap-only URLs join the queue so orphan pages are still audited
                for u in extra:
                    if u not in queued:
                        queued.add(u)
                        nxt.append(u)
            level, depth = nxt, depth + 1
        if level and len(site.pages) >= self.max_pages:
            site.truncated = True
        return site
