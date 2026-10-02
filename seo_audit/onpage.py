"""On-page SEO checks: titles, meta, headings, content, images, internal links, social tags."""
import re
from collections import Counter, defaultdict

from .models import ONPAGE as O, HIGH, MEDIUM, LOW, issue

BLOCK_TAGS = ["p", "li", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "tr",
              "blockquote", "br", "ul", "ol", "table", "header", "figcaption", "dd", "dt"]
GENERIC_ANCHORS = {"click here", "here", "read more", "more", "learn more", "link", "this", "this page",
                   "click", "continue", "details", "see more"}
STOPWORDS = set("""a an the and or but if of to in on for with at by from as is are was were be been it its this that these
those you your we our they their he she his her i me my not no yes do does did have has had will would can could should
may might about into over than then so such also more most other some any each which who whom what when where why how
all one two there here up out just only very""".split())


def _text(tag):
    return " ".join(tag.get_text(" ", strip=True).split()) if tag else ""


def visible_text(soup):
    """Main readable text: prefers <main>/<article>, strips scripts, nav, footers, code."""
    root = soup.find("main") or soup.find("article") or soup.body or soup
    clone = type(soup)(str(root), "lxml")
    for t in clone(["script", "style", "noscript", "svg", "template", "code", "pre", "nav", "footer", "aside", "form"]):
        t.decompose()
    for t in clone(BLOCK_TAGS):          # newline at block boundaries so sentences do not run together
        t.insert_before("\n")
        t.append("\n")
    lines = (" ".join(line.split()) for line in clone.get_text(" ").splitlines())
    return "\n".join(line for line in lines if line)


def syllables(word):
    word = word.lower()
    groups = re.findall(r"[aeiouy]+", word)
    n = len(groups) - (1 if word.endswith("e") and len(groups) > 1 else 0)
    return max(n, 1)


def flesch_reading_ease(text):
    sentences = max(len(re.findall(r"[.!?]+", text)), 1)
    words = re.findall(r"[A-Za-z']+", text)
    if not words:
        return None
    return 206.835 - 1.015 * len(words) / sentences - 84.6 * sum(map(syllables, words)) / len(words)


def run(site):
    out = []
    pages = site.html_pages
    titles, descs, h1s = defaultdict(list), defaultdict(list), defaultdict(list)

    for p in pages:
        soup = p.soup
        title = _text(soup.find("title"))
        desc_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
        desc = (desc_tag.get("content") or "").strip() if desc_tag else ""
        h1_tags = soup.find_all("h1")

        # title
        if not title:
            out.append(issue(O, HIGH, "no-title", "Missing <title> tag",
                             "Add a unique, descriptive title (30-60 characters) with the main keyword near the start.", p.url))
        else:
            titles[title.lower()].append(p.url)
            if len(title) > 60:
                out.append(issue(O, MEDIUM, "title-long", "Title is longer than 60 characters",
                                 "Shorten the title so it is not truncated in search results.", p.url, f"{len(title)} chars: {title}"))
            elif len(title) < 30:
                out.append(issue(O, LOW, "title-short", "Title is shorter than 30 characters",
                                 "Expand the title with relevant keywords or brand.", p.url, f"{len(title)} chars: {title}"))

        # meta description
        if not desc:
            out.append(issue(O, MEDIUM, "no-meta-desc", "Missing meta description",
                             "Write a unique 70-160 character description that encourages clicks.", p.url))
        else:
            descs[desc.lower()].append(p.url)
            if len(desc) > 160:
                out.append(issue(O, LOW, "meta-desc-long", "Meta description is longer than 160 characters",
                                 "Trim it to avoid truncation.", p.url, f"{len(desc)} chars"))
            elif len(desc) < 70:
                out.append(issue(O, LOW, "meta-desc-short", "Meta description is shorter than 70 characters",
                                 "Use the space to describe the page and add a call to action.", p.url, f"{len(desc)} chars"))

        # headings
        if not h1_tags:
            out.append(issue(O, HIGH, "no-h1", "Missing H1 heading",
                             "Add one H1 that describes the page topic.", p.url))
        else:
            if len(h1_tags) > 1:
                out.append(issue(O, MEDIUM, "multi-h1", "Multiple H1 headings",
                                 "Use a single H1 and H2-H6 for sub-sections.", p.url, f"{len(h1_tags)} H1 tags"))
            h1 = _text(h1_tags[0])
            if not h1:
                out.append(issue(O, MEDIUM, "empty-h1", "H1 heading is empty", "Fill the H1 with meaningful text.", p.url))
            else:
                h1s[h1.lower()].append(p.url)
        levels = [int(h.name[1]) for h in soup.find_all(re.compile(r"^h[1-6]$"))]
        for a, b in zip(levels, levels[1:]):
            if b > a + 1:
                out.append(issue(O, LOW, "heading-skip", "Heading levels are skipped",
                                 "Keep headings in order (H2 -> H3, not H2 -> H4).", p.url, f"H{a} followed by H{b}"))
                break

        # content
        text = visible_text(soup)
        words = re.findall(r"[A-Za-z0-9']+", text)
        if len(words) < 300:
            out.append(issue(O, MEDIUM if len(words) < 150 else LOW, "thin-content", "Thin content (under 300 words)",
                             "Add useful, original content that fully answers the search intent.", p.url, f"{len(words)} words"))
        html_len = max(len(p.html), 1)
        if len(text) / html_len < 0.1:
            out.append(issue(O, LOW, "low-text-ratio", "Low text-to-HTML ratio (<10%)",
                             "Reduce code bloat or add more content.", p.url, f"{100*len(text)/html_len:.1f}%"))
        if len(words) >= 200:
            counts = Counter(w.lower() for w in words if len(w) > 3 and w.lower() not in STOPWORDS and not w.isdigit())
            if counts:
                word, n = counts.most_common(1)[0]
                if n / len(words) > 0.04:
                    out.append(issue(O, MEDIUM, "keyword-stuffing", "Possible keyword stuffing",
                                     "Write naturally; use synonyms and related terms.", p.url,
                                     f"'{word}' = {100*n/len(words):.1f}% of words"))
            fre = flesch_reading_ease(text)
            if fre is not None and fre < 30:
                out.append(issue(O, LOW, "hard-to-read", "Content is very hard to read (Flesch score < 30)",
                                 "Use shorter sentences and simpler words.", p.url, f"Flesch {fre:.0f}"))

        # images
        imgs = soup.find_all("img")
        no_alt = [i.get("src", "")[:80] for i in imgs if i.get("alt") is None]
        empty_alt = [i for i in imgs if i.get("alt") is not None and not i["alt"].strip() and i.get("role") != "presentation"]
        if no_alt:
            out.append(issue(O, MEDIUM, "img-no-alt", "Images missing alt attribute",
                             "Add descriptive alt text to every meaningful image.", p.url,
                             f"{len(no_alt)} image(s), e.g. {', '.join(no_alt[:3])}"))
        if empty_alt:
            out.append(issue(O, LOW, "img-empty-alt", "Images with empty alt text",
                             "Describe informative images; keep alt=\"\" only for decorative ones.", p.url, f"{len(empty_alt)} image(s)"))
        no_dims = [i for i in imgs if not (i.get("width") and i.get("height"))]
        if no_dims:
            out.append(issue(O, LOW, "img-no-dimensions", "Images without width/height (layout shift risk)",
                             "Set width and height attributes to reduce CLS.", p.url, f"{len(no_dims)} image(s)"))

        # links
        if not p.internal_links:
            out.append(issue(O, MEDIUM, "no-internal-links", "Page has no outgoing internal links",
                             "Link to related pages to pass authority and help users.", p.url))
        if len(p.internal_links) + len(p.external_links) > 200:
            out.append(issue(O, LOW, "too-many-links", "More than 200 links on the page",
                             "Reduce links so each one carries more weight.", p.url, f"{len(p.internal_links)+len(p.external_links)} links"))
        bad_anchor = [(t, a) for t, a, _ in p.internal_links if a.lower() in GENERIC_ANCHORS]
        empty_anchor = [t for t, a, _ in p.internal_links if not a]
        if bad_anchor:
            out.append(issue(O, LOW, "generic-anchor", "Generic internal link anchor text",
                             "Use descriptive anchors instead of 'click here' / 'read more'.", p.url,
                             f"{len(bad_anchor)} link(s), e.g. '{bad_anchor[0][1]}' -> {bad_anchor[0][0]}"))
        if empty_anchor:
            out.append(issue(O, LOW, "empty-anchor", "Internal links with no anchor text or image alt",
                             "Give every link visible text or an image alt.", p.url, f"{len(empty_anchor)} link(s)"))

        # social / sharing
        og = {m.get("property") for m in soup.find_all("meta", property=re.compile("^og:"))}
        missing = [t for t in ("og:title", "og:description", "og:image") if t not in og]
        if missing:
            out.append(issue(O, LOW, "og-missing", "Open Graph tags missing",
                             "Add og:title, og:description and og:image for better social previews.", p.url, ", ".join(missing)))
        if not soup.find("meta", attrs={"name": "twitter:card"}):
            out.append(issue(O, LOW, "no-twitter-card", "Twitter Card tag missing",
                             "Add <meta name=\"twitter:card\">.", p.url))

        # title vs h1 mismatch is only a hint, not reported; URL keyword hint skipped

    for label, groups, sev, code, rec in (
            ("title", titles, HIGH, "dup-title", "Give every page its own title."),
            ("meta description", descs, MEDIUM, "dup-meta-desc", "Write a unique meta description per page."),
            ("H1", h1s, LOW, "dup-h1", "Make H1s unique per page.")):
        for text, urls in groups.items():
            if len(urls) > 1:
                for u in urls:
                    out.append(issue(O, sev, code, f"Duplicate {label}", rec, u,
                                     f"shared with {len(urls)-1} other page(s): \"{text[:80]}\""))
    return out
