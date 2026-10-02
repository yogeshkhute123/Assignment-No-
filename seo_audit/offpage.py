"""Off-page checks. True backlink data needs a third-party index (Ahrefs, Moz, Majestic, GSC export), so this
module covers what can be measured from the crawl and public data, plus an optional backlink CSV import."""
import csv
import re
import socket
import ssl
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from .models import OFFPAGE as F, HIGH, MEDIUM, LOW, issue

SOCIAL = {"facebook.com": "Facebook", "twitter.com": "Twitter/X", "x.com": "Twitter/X", "linkedin.com": "LinkedIn",
          "instagram.com": "Instagram", "youtube.com": "YouTube", "pinterest.com": "Pinterest", "tiktok.com": "TikTok"}
SPAM_TLDS = (".xyz", ".top", ".click", ".gq", ".ml", ".tk", ".cf", ".work", ".loan", ".win", ".bid", ".icu")
GENERIC = {"click here", "here", "website", "link", "read more", "visit", "this site", "source", "www"}


def _private(host):
    return host in ("localhost", "") or re.match(r"^(127\.|10\.|192\.168\.|169\.254\.|0\.)", host) or host.endswith(".local")


def _check_link(session, url):
    try:
        r = session.head(url, timeout=10, allow_redirects=True)
        if r.status_code in (403, 405, 501) or r.status_code >= 400:
            r = session.get(url, timeout=10, stream=True)
            r.close()
        return r.status_code
    except requests.RequestException as e:
        return type(e).__name__


def _ssl_days_left(host):
    try:
        with socket.create_connection((host, 443), timeout=8) as sock:
            with ssl.create_default_context().wrap_socket(sock, server_hostname=host) as s:
                exp = ssl.cert_time_to_seconds(s.getpeercert()["notAfter"])
        return (exp - datetime.now(timezone.utc).timestamp()) / 86400
    except (OSError, ssl.SSLError, KeyError):
        return None


def _domain_info(domain, session):
    try:
        r = session.get(f"https://rdap.org/domain/{domain}", timeout=10)
        if r.status_code != 200:
            return None, None
        created = expires = None
        for ev in r.json().get("events", []):
            d = ev.get("eventDate", "")[:10]
            if ev.get("eventAction") == "registration":
                created = d
            elif ev.get("eventAction") == "expiration":
                expires = d
        parse = lambda d: datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc) if d else None
        return parse(created), parse(expires)
    except (requests.RequestException, ValueError):
        return None, None


def analyse_backlink_csv(path):
    """Accepts a backlink export (Ahrefs / Semrush / Moz / GSC style) with flexible column names."""
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return [issue(F, HIGH, "no-backlinks", "Backlink export is empty", "Re-export your backlink data.", "")]
    cols = {c.lower().strip(): c for c in rows[0]}
    find = lambda *names: next((cols[k] for k in cols if any(n in k for n in names)), None)
    src, anchor, rel = find("source url", "referring page", "from url", "linking page", "url from", "site", "domain"), \
        find("anchor"), find("nofollow", "rel", "type")
    if not src:
        return [issue(F, LOW, "csv-unknown", "Could not recognise columns in the backlink CSV",
                      "Include a column such as 'Source URL' or 'Referring page URL'.", "", ", ".join(rows[0]))]
    domains = Counter()
    for r in rows:
        host = urlparse(r[src] if "//" in r[src] else "//" + r[src]).hostname or ""
        domains[host.removeprefix("www.")] += 1
    n_dom = len([d for d in domains if d])
    if n_dom < 10:
        out.append(issue(F, HIGH, "few-ref-domains", "Very few referring domains",
                         "Earn links from more unique, relevant sites (digital PR, partnerships, guest content).", "",
                         f"{n_dom} referring domain(s), {len(rows)} backlinks"))
    elif n_dom < 50:
        out.append(issue(F, MEDIUM, "low-ref-domains", "Low number of referring domains",
                         "Keep building links from diverse, relevant sources.", "", f"{n_dom} referring domains"))
    spam = [d for d in domains if d.endswith(SPAM_TLDS)]
    if spam and sum(domains[d] for d in spam) / len(rows) > 0.2:
        out.append(issue(F, HIGH, "spammy-tlds", "Large share of backlinks from spam-prone TLDs",
                         "Review and disavow clearly manipulative links.", "", ", ".join(spam[:10])))
    if rel:
        nf = sum(1 for r in rows if re.search(r"nofollow|ugc|sponsored|true|yes", r[rel] or "", re.I))
        if nf / len(rows) > 0.7:
            out.append(issue(F, MEDIUM, "mostly-nofollow", "Over 70% of backlinks are nofollow/UGC/sponsored",
                             "Seek more editorial dofollow links.", "", f"{100*nf/len(rows):.0f}% nofollow"))
    if anchor:
        anchors = Counter((r[anchor] or "").strip().lower() for r in rows if (r[anchor] or "").strip())
        if anchors:
            top, n = anchors.most_common(1)[0]
            total = sum(anchors.values())
            if n / total > 0.3 and top not in GENERIC:
                out.append(issue(F, MEDIUM, "anchor-overuse", "One anchor text dominates the backlink profile",
                                 "Diversify anchors (brand, URL, generic, partial match) to avoid over-optimisation.", "",
                                 f"'{top}' = {100*n/total:.0f}% of anchors"))
    top_dom = domains.most_common(1)[0]
    if top_dom[1] / len(rows) > 0.3 and n_dom > 1:
        out.append(issue(F, MEDIUM, "domain-concentration", "Backlinks concentrated on one domain",
                         "Diversify link sources.", "", f"{top_dom[0]} = {100*top_dom[1]/len(rows):.0f}% of links"))
    return out


def run(site, check_external=True, max_external=150, backlink_csv=None):
    out = []
    host = urlparse(site.start_url).hostname or ""
    pages = site.html_pages

    # outbound links
    ext = {}
    for p in pages:
        for target, anchor, nofollow in p.external_links:
            ext.setdefault(target, []).append((p.url, nofollow))
            if target.startswith("http://"):
                out.append(issue(F, LOW, "outbound-http", "Outbound link to an insecure (http://) URL",
                                 "Link to the https:// version if available.", p.url, target))
        n_ext = len(p.external_links)
        if n_ext > 100:
            out.append(issue(F, LOW, "many-outbound", "More than 100 outbound links on one page",
                             "Trim outbound links or nofollow untrusted ones.", p.url, f"{n_ext} links"))
    if check_external and ext:
        targets = [t for t in ext if not _private(urlparse(t).hostname or "")][:max_external]
        with ThreadPoolExecutor(8) as pool:
            codes = dict(zip(targets, pool.map(lambda u: _check_link(site.session, u), targets)))
        for t, code in codes.items():
            if isinstance(code, str) or code >= 400 and code not in (401, 403, 429, 999):
                for src, _ in ext[t]:
                    out.append(issue(F, MEDIUM, "broken-outbound", "Broken outbound link",
                                     "Remove or replace links that point to dead pages.", src, f"{t} ({code})"))

    # brand/social signals
    all_links = [t for p in pages for t, _, _ in p.external_links]
    found = {name for t in all_links for dom, name in SOCIAL.items() if (urlparse(t).hostname or "").removeprefix("www.").endswith(dom)}
    if pages and not found:
        out.append(issue(F, LOW, "no-social-links", "No links to social media profiles found",
                         "Link your brand profiles (Facebook, LinkedIn, YouTube...) to build entity signals.", site.start_url))
    home = site.pages.get(site.start_url)
    if home and home.is_html and not re.search(r'"sameAs"', home.html):
        out.append(issue(F, LOW, "no-sameas", "Homepage Organization schema has no sameAs links",
                         "Add sameAs URLs of your official profiles to Organization JSON-LD.", site.start_url))

    # domain / certificate trust signals (skipped for local/IP hosts)
    if not _private(host) and not re.match(r"^[\d.]+$", host):
        if site.start_url.startswith("https://"):
            days = _ssl_days_left(host)
            if days is not None and days < 30:
                out.append(issue(F, HIGH if days < 7 else MEDIUM, "ssl-expiring", "SSL certificate expires soon",
                                 "Renew the certificate (or enable auto-renewal).", site.start_url, f"{days:.0f} days left"))
        root = ".".join(host.removeprefix("www.").split(".")[-2:])
        created, expires = _domain_info(root, site.session)
        now = datetime.now(timezone.utc)
        if created and (now - created).days < 365:
            out.append(issue(F, LOW, "young-domain", "Domain is less than a year old",
                             "New domains need time to build trust; keep earning quality links.", site.start_url,
                             f"registered {created:%Y-%m-%d}"))
        if expires and (expires - now).days < 60:
            out.append(issue(F, HIGH, "domain-expiring", "Domain registration expires within 60 days",
                             "Renew the domain and enable auto-renew.", site.start_url, f"expires {expires:%Y-%m-%d}"))

    if backlink_csv:
        out += analyse_backlink_csv(backlink_csv)
    else:
        out.append(issue(F, LOW, "no-backlink-data", "Backlink profile not analysed (no data supplied)",
                         "Export backlinks from Google Search Console, Ahrefs, Semrush or Moz and re-run with --backlinks-csv.",
                         site.start_url))
    return out
