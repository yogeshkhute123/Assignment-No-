# Assignment-No-

## SEO Audit Tool (`seo_audit/`)

Crawls a whole website and reports SEO issues in four groups, each with a severity and a fix:

| Group | What it checks |
|---|---|
| **Technical** | HTTP errors, broken internal links, redirect chains/302s, HTTPS & mixed content, www/non-www, robots.txt, XML sitemap (bad/noindex/missing URLs), canonicals, noindex, soft 404, viewport/lang/charset, speed, compression, caching, URL hygiene, orphan & deep pages, JSON-LD validity, hreflang, duplicate content |
| **On-Page** | Title/meta description (missing, length, duplicate), H1/heading structure, thin content, keyword stuffing, readability, image alt/dimensions, internal-link anchors, Open Graph/Twitter cards |
| **Off-Page** | Broken & insecure outbound links, social profile links, `sameAs` schema, SSL/domain expiry, domain age (RDAP), plus backlink-profile analysis from a CSV export |
| **Content** | Spelling (pyspellchecker) and grammar (built-in rules, or LanguageTool) on titles, descriptions, headings and body copy |

Output: `seo_report.xlsx` (Summary, All Issues, one sheet per group, Issues per URL, Pages), `seo_report.html`, `seo_report.json`. Scores are 0-100 per group.

### Run

```bash
pip install -r requirements.txt
python -m seo_audit https://example.com --max-pages 300
python -m seo_audit https://example.com --backlinks-csv ahrefs_export.csv --dict my_words.txt --out reports/example
```

Options: `--max-depth`, `--workers`, `--delay`, `--ignore-robots`, `--skip-external`, `--formats xlsx,html,json`, `--languagetool`, `-q`.

### Limits to know about

- **Backlinks**: real backlink data lives in third-party indexes (Ahrefs, Semrush, Moz, Search Console). Without `--backlinks-csv` the off-page group only covers what a crawl and public data can show.
- **Spelling/grammar** are heuristics: add brand and jargon to `--dict` to cut false positives. Capitalised mid-sentence words are treated as proper nouns and skipped. Grammar rules are English-only; `--languagetool` is better but needs Java and was not tested here.
- **JavaScript-rendered sites**: pages are fetched as raw HTML, so content added by client-side JS is not seen.
- Page speed is server response time only, not Core Web Vitals (use PageSpeed Insights API for those).

### Tests

```bash
pip install pytest && python -m pytest tests -q
```
