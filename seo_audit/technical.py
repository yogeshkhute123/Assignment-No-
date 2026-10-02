"""Technical SEO checks: crawlability, indexability, status codes, redirects, security, speed."""
import hashlib
import json
import re
import uuid
from collections import defaultdict
from urllib.parse import urlparse, urlunparse

import requests

from .crawler import normalize, site_key
from .models import TECHNICAL as T, HIGH, MEDIUM, LOW, issue


def _robots_meta(page):
    meta = page.soup.find("meta", attrs={"name": re.compile(r"^(robots|googlebot)$", re.I)})
    content = (meta.get("content", "") if meta else "").lower()
    header = page.headers.get("x-robots-tag", "").lower()
    return content + " " + header


def run(site):
    out = []
    pages = list(site.pages.values())
    html_pages = site.html_pages
    start = urlparse(site.start_url)

    # --- HTTPS ---------------------------------------------------------------
    if start.scheme != "https":
        out.append(issue(T, HIGH, "no-https", "Site is not served over HTTPS",
                         "Install an SSL certificate and 301-redirect all http:// URLs to https://.", site.start_url))
    else:
        http_url = urlunparse(("http",) + tuple(start)[1:])
        try:
            r = site.session.get(http_url, timeout=10, allow_redirects=True)
            if not r.url.startswith("https://"):
                out.append(issue(T, HIGH, "http-no-redirect", "HTTP version does not redirect to HTTPS",
                                 "301-redirect http:// to https:// so only one version is indexable.", http_url))
        except requests.RequestException:
            pass

    # --- www / non-www canonical host -------------------------------------------
    host = start.hostname or ""
    alt_host = host[4:] if host.startswith("www.") else "www." + host
    if not re.match(r"^[\d.]+$", host) and host != "localhost":
        alt = urlunparse((start.scheme, alt_host) + tuple(start)[2:])
        try:
            r = site.session.get(alt, timeout=10, allow_redirects=True)
            if r.status_code == 200 and site_key(r.url) == site_key(site.start_url) and \
                    (urlparse(r.url).hostname or "") == alt_host:
                out.append(issue(T, MEDIUM, "www-duplicate", "Both www and non-www versions resolve without redirecting",
                                 "Pick one host and 301-redirect the other to it (and set it in Search Console).", alt))
        except requests.RequestException:
            pass

    # --- robots.txt / sitemap ------------------------------------------------------
    if site.robots_status != 200:
        out.append(issue(T, MEDIUM, "no-robots", "robots.txt is missing or unreachable",
                         "Add a robots.txt at the site root that references your XML sitemap.", site.start_url,
                         f"HTTP {site.robots_status or 'no response'}"))
    elif re.search(r"(?im)^\s*disallow:\s*/\s*$", site.robots_text) and \
            re.search(r"(?im)^\s*user-agent:\s*\*", site.robots_text):
        out.append(issue(T, HIGH, "robots-blocks-all", "robots.txt disallows the whole site for all crawlers",
                         "Remove 'Disallow: /' unless the site is meant to stay out of search results.", site.start_url))
    if not site.sitemap_sources:
        out.append(issue(T, MEDIUM, "no-sitemap", "No readable XML sitemap found",
                         "Publish /sitemap.xml and list it in robots.txt and Search Console.", site.start_url,
                         "; ".join(site.sitemap_errors)))
    for sm_url in dict.fromkeys(site.sitemap_urls):
        p = site.pages.get(sm_url)
        if p and p.status >= 400:
            out.append(issue(T, MEDIUM, "sitemap-bad-url", "XML sitemap lists a URL that returns an error",
                             "Remove broken URLs from the sitemap or fix the pages.", sm_url, f"HTTP {p.status}"))
        elif p and p.redirects:
            out.append(issue(T, LOW, "sitemap-redirect-url", "XML sitemap lists a URL that redirects",
                             "List the final destination URL in the sitemap instead.", sm_url))
        elif p and p.is_html and "noindex" in _robots_meta(p):
            out.append(issue(T, MEDIUM, "sitemap-noindex", "XML sitemap lists a noindex page",
                             "Remove noindex pages from the sitemap or drop the noindex.", sm_url))
    if site.sitemap_sources:
        for p in html_pages:
            if p.url not in set(site.sitemap_urls) and not p.redirects and "noindex" not in _robots_meta(p):
                out.append(issue(T, LOW, "not-in-sitemap", "Indexable page is missing from the XML sitemap",
                                 "Add important indexable pages to the sitemap.", p.url))

    # --- soft-404 ---------------------------------------------------------------------
    probe = f"{start.scheme}://{start.netloc}/{uuid.uuid4().hex}-seo-audit-probe"
    try:
        r = site.session.get(probe, timeout=10)
        if r.status_code == 200:
            out.append(issue(T, HIGH, "soft-404", "Non-existent URLs return HTTP 200 (soft 404)",
                             "Configure the server to return a real 404 status for missing pages.", probe))
    except requests.RequestException:
        pass

    # --- per page --------------------------------------------------------------------------
    hashes = defaultdict(list)
    for p in pages:
        if p.error:
            out.append(issue(T, HIGH, "fetch-error", "Page could not be fetched", "Check server availability and URL.",
                             p.url, p.error))
            continue
        if 400 <= p.status < 500:
            refs = ", ".join(sorted(site.inlinks.get(p.url, []))[:5])
            out.append(issue(T, HIGH, "http-4xx", "Page returns a 4xx client error (broken page)",
                             "Restore the page, 301-redirect it to a relevant URL, or remove links pointing to it.",
                             p.url, f"HTTP {p.status}" + (f"; linked from: {refs}" if refs else "")))
        elif p.status >= 500:
            out.append(issue(T, HIGH, "http-5xx", "Page returns a 5xx server error",
                             "Fix the server-side error; repeated 5xx responses cause de-indexing.", p.url, f"HTTP {p.status}"))
        if len(p.redirects) > 1:
            out.append(issue(T, MEDIUM, "redirect-chain", "Redirect chain (more than one hop)",
                             "Link and redirect straight to the final URL.", p.url,
                             " -> ".join([f"{u} ({c})" for u, c in p.redirects] + [p.final_url])))
        if any(c in (302, 303, 307) for _, c in p.redirects):
            out.append(issue(T, LOW, "temp-redirect", "Temporary (302/307) redirect used",
                             "Use a 301/308 if the move is permanent so link equity passes.", p.url,
                             " -> ".join(u for u, _ in p.redirects) + " -> " + p.final_url))
        if p.redirects and p.final_url.startswith("http://") and p.url.startswith("https://"):
            out.append(issue(T, HIGH, "redirect-to-http", "HTTPS URL redirects to HTTP",
                             "Redirect to the HTTPS version.", p.url, p.final_url))
        # internal links that hit a redirect (should point to final URL)
        if not p.is_html or not (200 <= p.status < 300) or p.redirects:
            continue

        soup, headers = p.soup, p.headers
        robots = _robots_meta(p)
        if "noindex" in robots:
            out.append(issue(T, MEDIUM, "noindex", "Page is set to noindex",
                             "Confirm this is intentional; otherwise remove the noindex directive.", p.url, robots.strip()))
        if "nofollow" in robots.split():
            out.append(issue(T, LOW, "meta-nofollow", "Page-level nofollow directive",
                             "Remove 'nofollow' from the robots meta tag unless intended.", p.url))

        # canonical
        canon = soup.find_all("link", rel=lambda r: r and "canonical" in r)
        if not canon:
            out.append(issue(T, MEDIUM, "no-canonical", "Missing canonical tag",
                             "Add <link rel=\"canonical\"> pointing at the preferred URL.", p.url))
        else:
            if len(canon) > 1:
                out.append(issue(T, MEDIUM, "multi-canonical", "Multiple canonical tags",
                                 "Keep exactly one canonical tag.", p.url))
            href = canon[0].get("href", "").strip()
            if not href:
                out.append(issue(T, MEDIUM, "empty-canonical", "Canonical tag is empty",
                                 "Set the canonical href to the page's preferred absolute URL.", p.url))
            else:
                target = normalize(requests.compat.urljoin(p.final_url, href))
                if target != p.final_url and target.rstrip("/") != p.final_url.rstrip("/"):
                    tp = site.pages.get(target)
                    out.append(issue(T, LOW, "canonical-other", "Canonical points to a different URL",
                                     "Make sure this is intentional; the page will not rank on its own.", p.url, target))
                    if tp and (tp.status >= 400 or tp.redirects):
                        out.append(issue(T, HIGH, "canonical-broken", "Canonical points to a broken or redirecting URL",
                                         "Point the canonical at a live 200 URL.", p.url, f"{target} (HTTP {tp.status})"))

        # basics
        if not soup.find("meta", attrs={"name": "viewport"}):
            out.append(issue(T, HIGH, "no-viewport", "Missing viewport meta tag (not mobile friendly)",
                             "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">.", p.url))
        html_tag = soup.find("html")
        if not (html_tag and html_tag.get("lang")):
            out.append(issue(T, LOW, "no-lang", "Missing lang attribute on <html>",
                             "Declare the page language, e.g. <html lang=\"en\">.", p.url))
        if not soup.find("meta", charset=True) and "charset" not in p.content_type.lower() and \
                not soup.find("meta", attrs={"http-equiv": re.compile("content-type", re.I)}):
            out.append(issue(T, LOW, "no-charset", "Character encoding not declared",
                             "Add <meta charset=\"utf-8\"> or a Content-Type charset header.", p.url))
        if not soup.find("link", rel=lambda r: r and "icon" in " ".join(r).lower()):
            out.append(issue(T, LOW, "no-favicon", "No favicon declared",
                             "Add a <link rel=\"icon\"> tag.", p.url))

        # security / mixed content
        if p.final_url.startswith("https://"):
            mixed = [t.get("src") or t.get("href") for t in soup.find_all(["img", "script", "iframe", "link", "source"])
                     if str(t.get("src") or t.get("href") or "").startswith("http://")]
            if mixed:
                out.append(issue(T, HIGH, "mixed-content", "Mixed content: HTTP resources on an HTTPS page",
                                 "Load all resources over https://.", p.url, ", ".join(mixed[:5])))
            if "strict-transport-security" not in headers:
                out.append(issue(T, LOW, "no-hsts", "HSTS header missing",
                                 "Send Strict-Transport-Security to enforce HTTPS.", p.url))

        # performance
        if p.elapsed > 1.5:
            out.append(issue(T, MEDIUM if p.elapsed < 3 else HIGH, "slow-response", "Slow server response",
                             "Improve TTFB: caching, CDN, faster hosting, query optimisation.", p.url, f"{p.elapsed:.2f}s"))
        if p.size > 1_500_000:
            out.append(issue(T, MEDIUM, "large-html", "HTML document is very large",
                             "Reduce HTML size (pagination, lazy loading, fewer inline assets).", p.url, f"{p.size/1e6:.1f} MB"))
        if "content-encoding" not in headers and p.size > 10_000:
            out.append(issue(T, MEDIUM, "no-compression", "Response is not compressed (gzip/brotli)",
                             "Enable gzip or brotli compression on the server.", p.url))
        if not any(k in headers for k in ("cache-control", "expires", "etag")):
            out.append(issue(T, LOW, "no-cache-headers", "No caching headers on HTML response",
                             "Send Cache-Control/ETag headers where appropriate.", p.url))
        head = soup.find("head")
        blocking = [s for s in (head.find_all("script", src=True) if head else [])
                    if not (s.has_attr("async") or s.has_attr("defer") or s.get("type") == "module")]
        if len(blocking) > 3:
            out.append(issue(T, LOW, "render-blocking-js", "Several render-blocking scripts in <head>",
                             "Add defer/async or move scripts to the end of <body>.", p.url, f"{len(blocking)} scripts"))

        # URL hygiene
        u = urlparse(p.final_url)
        if len(p.final_url) > 115:
            out.append(issue(T, LOW, "long-url", "URL is longer than 115 characters",
                             "Use shorter, descriptive URLs.", p.url, f"{len(p.final_url)} chars"))
        if u.path != u.path.lower():
            out.append(issue(T, LOW, "url-uppercase", "URL contains uppercase characters",
                             "Use lowercase URLs and redirect mixed-case variants.", p.url))
        if "_" in u.path:
            out.append(issue(T, LOW, "url-underscore", "URL contains underscores",
                             "Use hyphens to separate words.", p.url))
        if u.query:
            out.append(issue(T, LOW, "url-params", "URL contains query parameters",
                             "Make sure parameter URLs canonicalise to a clean URL or are blocked from crawling.", p.url, u.query[:100]))
        if p.depth > 3:
            out.append(issue(T, LOW, "deep-page", "Page is more than 3 clicks from the homepage",
                             "Link to it from higher-level pages to improve crawl priority.", p.url, f"depth {p.depth}"))
        if not site.inlinks.get(p.url) and p.url != site.start_url:
            out.append(issue(T, MEDIUM, "orphan", "Orphan page: no internal links point to it",
                             "Link to this page from relevant pages or remove it.", p.url, "found only via sitemap"))

        # structured data
        ld = soup.find_all("script", type="application/ld+json")
        for block in ld:
            try:
                json.loads(block.string or "")
            except (ValueError, TypeError):
                out.append(issue(T, MEDIUM, "bad-jsonld", "Structured data (JSON-LD) is not valid JSON",
                                 "Fix the JSON syntax; validate with Google's Rich Results Test.", p.url))
        if not ld and not soup.find(attrs={"itemtype": True}):
            out.append(issue(T, LOW, "no-schema", "No structured data (schema.org) found",
                             "Add JSON-LD markup suited to the page (Organization, Article, Product, FAQ...).", p.url))

        # hreflang
        for tag in soup.find_all("link", rel="alternate", hreflang=True):
            tp = site.pages.get(normalize(requests.compat.urljoin(p.final_url, tag.get("href", ""))))
            if tp and tp.status >= 400:
                out.append(issue(T, MEDIUM, "hreflang-broken", "hreflang points to a broken URL",
                                 "Fix or remove the hreflang annotation.", p.url, tag.get("href")))

        # broken internal links on this page / links through redirects
        for target, _, _ in p.internal_links:
            tp = site.pages.get(target)
            if tp and (tp.status >= 400 or tp.error):
                out.append(issue(T, HIGH, "broken-internal-link", "Broken internal link",
                                 "Update or remove the link.", p.url, f"{target} (HTTP {tp.status or tp.error})"))
            elif tp and tp.redirects:
                out.append(issue(T, LOW, "internal-redirect-link", "Internal link points to a redirecting URL",
                                 "Link directly to the final URL.", p.url, f"{target} -> {tp.final_url}"))

        # duplicate content
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).lower()
        if len(text) > 200:
            hashes[hashlib.md5(text.encode()).hexdigest()].append(p.url)

    for urls in hashes.values():
        if len(urls) > 1:
            for u in urls:
                out.append(issue(T, MEDIUM, "duplicate-content", "Duplicate page content",
                                 "Consolidate with a 301 redirect or canonical tag, or differentiate the content.",
                                 u, "same content as: " + ", ".join(x for x in urls if x != u)[:300]))
    return out
