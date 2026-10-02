from collections import defaultdict

from . import content, offpage, onpage, technical
from .crawler import Crawler
from .models import CATEGORIES, SEVERITY_ORDER, SEVERITY_WEIGHT


def score(issues, total_pages):
    """0-100 per category: each distinct problem costs its severity weight, scaled up by how many pages it hits."""
    total_pages = max(total_pages, 1)
    result = {}
    for cat in CATEGORIES:
        by_code = defaultdict(set)
        sev = {}
        for i in issues:
            if i.category == cat:
                by_code[i.code].add(i.url or "<site>")
                sev[i.code] = i.severity
        penalty = sum(SEVERITY_WEIGHT[sev[c]] * (0.5 + 0.5 * min(len(u) / total_pages, 1)) for c, u in by_code.items())
        result[cat] = max(0, round(100 - penalty))
    result["Overall"] = round(sum(result[c] for c in CATEGORIES) / len(CATEGORIES))
    return result


def run_audit(url, max_pages=200, max_depth=6, workers=6, delay=0.0, respect_robots=True,
              check_external=True, backlink_csv=None, custom_dict=None, languagetool=False, verbose=True):
    site = Crawler(url, max_pages, max_depth, workers, delay, respect_robots=respect_robots, verbose=verbose).crawl()
    home = site.pages.get(site.start_url)
    if home is None or home.error:
        raise SystemExit(f"Could not fetch {site.start_url}: {home.error if home else 'blocked by robots.txt'}. "
                         "Check the URL, your network/proxy, or use --ignore-robots.")
    if verbose:
        print(f"Crawled {len(site.pages)} pages. Running checks...", flush=True)
    issues = []
    issues += technical.run(site)
    issues += onpage.run(site)
    issues += offpage.run(site, check_external=check_external, backlink_csv=backlink_csv)
    issues += content.run(site, custom_dict=custom_dict, use_languagetool=languagetool)
    # drop exact duplicates, then order: category, severity, code, url
    uniq = {(i.category, i.code, i.url, i.detail): i for i in issues}
    issues = sorted(uniq.values(), key=lambda i: (CATEGORIES.index(i.category), SEVERITY_ORDER[i.severity], i.code, i.url))
    return site, issues, score(issues, len(site.html_pages))
