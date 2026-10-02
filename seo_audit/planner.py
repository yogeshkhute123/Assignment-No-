"""Content gap & topic planner.

Crawls your site to learn which topics you already cover, expands seed keywords into related searches
(Google autocomplete and/or a keyword CSV with volumes), and reports only the topics you have NOT covered,
grouped into clusters with a suggested page type and title.

    python -m seo_audit.planner --site https://example.com --seed "yoga mats" --out content_plan
"""
import argparse
import csv
import json
import re
import string
import time
from collections import Counter, defaultdict

import requests
from openpyxl import Workbook

from .crawler import Crawler
from .onpage import visible_text
from .report import _style_sheet

STOP = set("""a an the and or of to in on for with at by from as is are was were be it its this that these those you your we
our they their i me my do does did have has can could should would will about into than then so how what why when where which
who whom best top vs versus near""".split())
INTENT_WORDS = {
    "transactional": {"buy", "price", "prices", "pricing", "cost", "cheap", "discount", "order", "coupon", "deal", "deals",
                      "hire", "quote", "shop", "sale", "purchase", "booking", "book"},
    "commercial": {"best", "top", "review", "reviews", "vs", "versus", "compare", "comparison", "alternative", "alternatives",
                   "software", "tool", "tools", "service", "services", "company", "companies", "agency", "near"},
    "informational": {"how", "what", "why", "when", "guide", "tutorial", "tips", "ideas", "examples", "meaning", "learn",
                      "definition", "difference", "benefits", "can", "does", "is", "are"},
}
INTENT_WEIGHT = {"transactional": 1.0, "commercial": 0.9, "informational": 0.7}
PREFIXES = ["how to", "what is", "why", "best", "vs", "for", "near me", "price", "cost", "can", "does", "tips"]


def stem(w):
    for suf, rep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)] + rep
    return w


def tokens(text):
    return {stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 1}


# ---- 1. what the site already covers ---------------------------------------------------------
def build_index(site):
    index = []
    for p in site.html_pages:
        s = p.soup
        title = s.find("title").get_text(" ", strip=True) if s.find("title") else ""
        h1 = s.find("h1").get_text(" ", strip=True) if s.find("h1") else ""
        slug = re.sub(r"[-_/]+", " ", p.final_url.split("//", 1)[-1].split("/", 1)[-1].split("?")[0])
        heads = " ".join(h.get_text(" ", strip=True) for h in s.find_all(["h2", "h3"]))
        index.append({"url": p.url, "title": title or h1 or p.url,
                      "T": tokens(f"{title} {h1} {slug}"), "H": tokens(heads), "B": tokens(visible_text(s))})
    return index


# ---- 2. candidate keywords ---------------------------------------------------------------------
def google_autocomplete(query, lang="en", country="us", timeout=8):
    r = requests.get("https://suggestqueries.google.com/complete/search",
                     params={"client": "firefox", "q": query, "hl": lang, "gl": country}, timeout=timeout)
    return r.json()[1] if r.status_code == 200 else []


def expand_seeds(seeds, fetcher=google_autocomplete, delay=0.2, log=print):
    """Returns {keyword: score} where score rewards suggestions that rank high / appear for several queries."""
    scores = Counter()
    for seed in seeds:
        queries = [seed] + [f"{seed} {c}" for c in string.ascii_lowercase] + [f"{pre} {seed}" for pre in PREFIXES]
        for q in queries:
            try:
                for rank, kw in enumerate(fetcher(q)):
                    scores[kw.lower().strip()] += max(10 - rank, 1)
            except (requests.RequestException, ValueError, IndexError) as e:
                log(f"  autocomplete failed for '{q}': {type(e).__name__}")
                break
            time.sleep(delay)
        scores[seed.lower()] += 0
    return scores


def load_keyword_csv(path):
    """Columns (any case): keyword | volume (or search volume / avg. monthly searches) | difficulty (or kd)."""
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    cols = {c.lower().strip(): c for c in rows[0]} if rows else {}
    pick = lambda *names: next((cols[k] for k in cols if any(n in k for n in names)), None)
    kw, vol, kd = pick("keyword", "query", "search term"), pick("volume", "searches"), pick("difficulty", "kd", "competition")
    if not kw:
        raise SystemExit("Keyword CSV needs a 'keyword' column.")
    num = lambda v: float(re.sub(r"[^\d.]", "", v) or 0) if v else 0
    for r in rows:
        k = (r[kw] or "").strip().lower()
        if k:
            out[k] = {"volume": num(r[vol]) if vol else None, "difficulty": num(r[kd]) if kd else None}
    return out


# ---- 3. gap analysis -----------------------------------------------------------------------------
def intent(keyword):
    words = set(keyword.lower().split())
    for name in ("transactional", "commercial", "informational"):
        if words & INTENT_WORDS[name]:
            return name
    return "informational"


def coverage(keyword, index):
    """('Covered'|'Partial'|'Gap', best page url or '')"""
    k = tokens(keyword)
    if not k:
        return "Covered", ""
    best = ("Gap", "", 0.0)
    for page in index:
        ct = len(k & page["T"]) / len(k)
        ch = len(k & (page["T"] | page["H"])) / len(k)
        cb = len(k & (page["T"] | page["H"] | page["B"])) / len(k)
        if ct >= 0.8:
            status, strength = "Covered", 3 + ct
        elif ch >= 0.8:
            status, strength = "Partial", 2 + ch      # appears in a subheading only: expand or give it its own page
        elif cb == 1 and len(k) > 1:
            status, strength = "Partial", 1 + cb      # words appear somewhere in the text, no heading
        else:
            continue
        if strength > best[2]:
            best = (status, page["url"], strength)
    return best[0], best[1]


def suggest(keyword, kind):
    kw = keyword.strip()
    title = kw.title().replace("'S", "'s")
    if re.match(r"^(how|what|why|when|where|can|does|is|are|which|who)\b", kw):
        t, page = f"{title}? A Clear, Practical Answer", "Blog post / FAQ answer"
    elif kind == "transactional":
        t, page = f"{title}: Options, Pricing & How to Choose", "Landing / product or service page"
    elif kind == "commercial":
        t, page = (title if re.search(r"\b(best|top|vs|review)", kw) else f"Best {title}") + " (Compared)", "Comparison / listicle"
    else:
        t, page = f"{title}: The Complete Guide", "In-depth guide"
    return (t if len(t) <= 62 else t[:59].rsplit(" ", 1)[0] + "…"), page


def analyse(candidates, index):
    """candidates: {keyword: {'score': autocomplete score, 'volume': v|None, 'difficulty': d|None}}"""
    rows = []
    for kw, info in candidates.items():
        status, url = coverage(kw, index)
        kind = intent(kw)
        vol = info.get("volume")
        base = vol if vol else info.get("score", 0)
        priority_score = base * INTENT_WEIGHT[kind] / (1 + (info.get("difficulty") or 0) / 100)
        title, page = suggest(kw, kind)
        rows.append({"keyword": kw, "status": status, "page": url, "intent": kind, "volume": vol,
                     "difficulty": info.get("difficulty"), "score": round(priority_score, 1),
                     "suggested_title": title, "page_type": page})
    gaps = sorted((r for r in rows if r["status"] == "Gap"), key=lambda r: (-r["score"], len(r["keyword"])))
    if gaps:   # priority bands by rank within the gaps
        for n, r in enumerate(gaps):
            r["priority"] = "High" if n < len(gaps) / 3 else "Medium" if n < 2 * len(gaps) / 3 else "Low"
    return rows, cluster(gaps)


def cluster(gaps, threshold=0.5):
    clusters = []
    for r in gaps:
        t = tokens(r["keyword"])
        for c in clusters:
            u = t | c["tokens"]
            if u and len(t & c["tokens"]) / len(u) >= threshold:
                c["members"].append(r)
                break
        else:
            clusters.append({"head": r, "tokens": t, "members": [r]})
    out = []
    for c in clusters:
        m = c["members"]
        out.append({"topic": c["head"]["keyword"], "keywords": [x["keyword"] for x in m], "count": len(m),
                    "volume": sum(x["volume"] or 0 for x in m) or None, "score": round(sum(x["score"] for x in m), 1),
                    "priority": c["head"]["priority"], "intent": c["head"]["intent"],
                    "suggested_title": c["head"]["suggested_title"], "page_type": c["head"]["page_type"]})
    return sorted(out, key=lambda c: -c["score"])


# ---- 4. output --------------------------------------------------------------------------------------
def write_xlsx(path, site_url, rows, clusters):
    wb = Workbook()
    ws = wb.active
    ws.title = "Topic Clusters"
    ws.append(["Priority", "Topic (main keyword)", "Keywords in cluster", "Intent", "Suggested page type",
               "Suggested title", "Total volume", "Score", "Related keywords"])
    for c in clusters:
        ws.append([c["priority"], c["topic"], c["count"], c["intent"], c["page_type"], c["suggested_title"],
                   c["volume"], c["score"], "\n".join(c["keywords"][:30])])
    _style_sheet(ws, [10, 34, 12, 14, 28, 48, 12, 10, 50])

    def sheet(name, subset, header, fn, widths):
        sh = wb.create_sheet(name)
        sh.append(header)
        for r in subset:
            sh.append(fn(r))
        _style_sheet(sh, widths)

    gaps = [r for r in rows if r["status"] == "Gap"]
    sheet("Gap Keywords", sorted(gaps, key=lambda r: -r["score"]),
          ["Priority", "Keyword", "Intent", "Volume", "Difficulty", "Score", "Suggested title", "Page type"],
          lambda r: [r["priority"], r["keyword"], r["intent"], r["volume"], r["difficulty"], r["score"],
                     r["suggested_title"], r["page_type"]], [10, 40, 14, 10, 10, 10, 50, 28])
    sheet("Partially Covered", [r for r in rows if r["status"] == "Partial"],
          ["Keyword", "Intent", "Volume", "Best matching page", "Action"],
          lambda r: [r["keyword"], r["intent"], r["volume"], r["page"],
                     "Add a dedicated section/heading or give it its own page"], [40, 14, 10, 60, 50])
    sheet("Already Covered", [r for r in rows if r["status"] == "Covered"],
          ["Keyword", "Intent", "Volume", "Covered by page"],
          lambda r: [r["keyword"], r["intent"], r["volume"], r["page"]], [40, 14, 10, 60])
    summary = wb.create_sheet("Summary", 0)
    counts = Counter(r["status"] for r in rows)
    for line in (["Content gap plan", site_url], ["Keywords analysed", len(rows)], ["Gaps (new content)", counts["Gap"]],
                 ["Partially covered", counts["Partial"]], ["Already covered", counts["Covered"]],
                 ["Topic clusters to write", len(clusters)]):
        summary.append(line)
    summary.column_dimensions["A"].width = 28
    summary.column_dimensions["B"].width = 50
    wb.save(path)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="seo_audit.planner", description="Find topics your site has not covered yet.")
    ap.add_argument("--site", required=True)
    ap.add_argument("--seed", action="append", default=[], help="seed keyword/topic (repeat for several)")
    ap.add_argument("--keywords-csv", help="keyword export with optional volume/difficulty columns")
    ap.add_argument("--no-autocomplete", action="store_true")
    ap.add_argument("--country", default="us")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--max-pages", type=int, default=200)
    ap.add_argument("--out", default="content_plan")
    a = ap.parse_args(argv)
    if not a.seed and not a.keywords_csv:
        ap.error("give at least one --seed or a --keywords-csv")

    site = Crawler(a.site, max_pages=a.max_pages, verbose=False).crawl()
    index = build_index(site)
    print(f"Indexed {len(index)} existing pages")
    candidates = {}
    if a.seed and not a.no_autocomplete:
        fetch = lambda q: google_autocomplete(q, a.lang, a.country)
        for kw, sc in expand_seeds(a.seed, fetch).items():
            candidates[kw] = {"score": sc}
    for s in a.seed:
        candidates.setdefault(s.lower(), {"score": 0})
    if a.keywords_csv:
        for kw, info in load_keyword_csv(a.keywords_csv).items():
            candidates.setdefault(kw, {"score": 0}).update(info)
    if not candidates:
        raise SystemExit("No keywords found (autocomplete may be blocked on this network). Supply --keywords-csv.")
    rows, clusters = analyse(candidates, index)
    write_xlsx(a.out + ".xlsx", a.site, rows, clusters)
    with open(a.out + ".json", "w") as fh:
        json.dump({"clusters": clusters, "keywords": rows}, fh, indent=2)
    c = Counter(r["status"] for r in rows)
    print(f"{len(rows)} keywords: {c['Gap']} gaps, {c['Partial']} partial, {c['Covered']} covered -> {len(clusters)} topic clusters")
    print(f"Wrote {a.out}.xlsx and {a.out}.json")
    for cl in clusters[:10]:
        print(f"  [{cl['priority']:<6}] {cl['suggested_title']}  ({cl['count']} keywords)")


if __name__ == "__main__":
    main()
