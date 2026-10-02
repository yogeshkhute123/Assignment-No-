import argparse
import os
import re
import sys
from urllib.parse import urlparse

from . import report
from .audit import run_audit


def main(argv=None):
    ap = argparse.ArgumentParser(prog="seo_audit", description="Crawl a website and audit technical, on-page, off-page and content SEO.")
    ap.add_argument("url")
    ap.add_argument("--max-pages", type=int, default=200)
    ap.add_argument("--max-depth", type=int, default=6)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to wait before each request")
    ap.add_argument("--ignore-robots", action="store_true", help="crawl pages disallowed by robots.txt")
    ap.add_argument("--skip-external", action="store_true", help="do not check outbound links")
    ap.add_argument("--backlinks-csv", help="backlink export (Ahrefs/Semrush/Moz/GSC) to analyse")
    ap.add_argument("--dict", dest="custom_dict", help="text file of extra words the spell checker should accept")
    ap.add_argument("--languagetool", action="store_true", help="use LanguageTool for grammar (needs Java + language_tool_python)")
    ap.add_argument("--out", default="seo_report", help="output path prefix (default: seo_report)")
    ap.add_argument("--formats", default="xlsx,html,json")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)

    site, issues, scores = run_audit(
        a.url, a.max_pages, a.max_depth, a.workers, a.delay, not a.ignore_robots, not a.skip_external,
        a.backlinks_csv, a.custom_dict, a.languagetool, verbose=not a.quiet)
    prefix = a.out
    if os.path.dirname(prefix):
        os.makedirs(os.path.dirname(prefix), exist_ok=True)
    writers = {"xlsx": report.write_xlsx, "html": report.write_html, "json": report.write_json}
    for fmt in (f.strip() for f in a.formats.split(",")):
        if fmt in writers:
            writers[fmt](f"{prefix}.{fmt}", site, issues, scores)
            print(f"Wrote {prefix}.{fmt}")
    print("\nScores:", ", ".join(f"{k} {v}" for k, v in scores.items()))
    print(f"{len(issues)} issue rows across {len(report.group(issues))} issue types")
    return 0


if __name__ == "__main__":
    sys.exit(main())
