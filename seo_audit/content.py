"""Spelling and grammar checks on the visible copy of every page."""
import re
from collections import Counter, defaultdict
from urllib.parse import urlparse

from .models import CONTENT as C, MEDIUM, LOW, issue
from .onpage import visible_text, _text

try:
    from spellchecker import SpellChecker
except ImportError:          # pragma: no cover
    SpellChecker = None

WORD = re.compile(r"[A-Za-z][A-Za-z'’]*[A-Za-z]|[A-Za-z]")
ABBREV = {"e.g", "i.e", "etc", "vs", "approx", "inc", "ltd", "dr", "mr", "mrs", "ms", "no", "fig", "st", "co", "sr", "jr"}
Y_SOUND = ("univers", "uniq", "unit", "unif", "union", "unic", "use", "usu", "util", "uten", "eu", "ubiq", "ukr", "ura", "uri")
W_SOUND = {"one", "once"}                   # start with a vowel letter but a consonant sound
AN_BEFORE_CONS_OK = ("hour", "honest", "honor", "honour", "heir")


def _lang(page):
    tag = page.soup.find("html")
    code = (tag.get("lang", "en") if tag else "en").lower()[:2]
    return code if code in {"en", "es", "fr", "pt", "de", "ru", "ar", "it", "nl"} else "en"


def spelling(text, checker, allow):
    """Return [(word, suggestion)] for probable misspellings."""
    found, seen = [], set()
    for m in WORD.finditer(text):
        w = m.group(0).replace("’", "'")
        if w.endswith("'s"):
            w = w[:-2]
        low = w.lower()
        if len(low) < 3 or low in seen or low in allow or w.isupper() or any(c.isupper() for c in w[1:]):
            continue
        prev = text[:m.start()].rstrip(" ")[-1:]     # a newline (block boundary) also starts a sentence
        at_sentence_start = prev == "" or prev in ".!?:\n"
        if w[0].isupper() and not at_sentence_start:
            continue   # mid-sentence capital = proper noun (brand, place, person)
        seen.add(low)
        if low in checker.unknown([low]):
            found.append((w, checker.correction(low) or ""))
    return found


def grammar(text, lang="en"):
    """Lightweight rule-based grammar checks. Returns [(message, snippet)]."""
    if lang != "en":
        return []
    hits = []

    def add(msg, m, pad=25):
        s = max(m.start() - pad, 0)
        hits.append((msg, "…" + text[s:m.end() + pad].replace("\n", " ") + "…"))

    for m in re.finditer(r"\b([A-Za-z']{2,})[ \t]+\1\b", text, re.I):
        if m.group(1).lower() not in {"had", "that", "very", "bye", "no", "so", "yeah"}:
            add(f"Repeated word '{m.group(1)}'", m)
    for m in re.finditer(r"\b(a|an)[ \t]+([A-Za-z]+)", text, re.I):
        art, nxt = m.group(1), m.group(2)
        low = nxt.lower()
        if nxt.isupper() and len(nxt) > 1:
            continue    # acronyms depend on pronunciation
        consonant_sound = low in W_SOUND or low.startswith(Y_SOUND)
        art = art.lower()
        if art == "a" and low[0] in "aeiou" and not consonant_sound:
            add(f"Use 'an' before '{nxt}'", m)
        elif art == "an" and (consonant_sound or (low[0] not in "aeiou" and not low.startswith(AN_BEFORE_CONS_OK))):
            add(f"Use 'a' before '{nxt}'", m)
    for m in re.finditer(r"\b(should|could|would|must|might)\s+of\b", text, re.I):
        add(f"'{m.group(0)}' should be '{m.group(1)} have'", m)
    for m in re.finditer(r"\b(he|she|it)\s+(are|were|have)\b", text, re.I):
        add(f"Subject-verb agreement: '{m.group(0)}'", m)
    for m in re.finditer(r"\b(they|we|you)\s+(is|was|has)\b", text, re.I):
        add(f"Subject-verb agreement: '{m.group(0)}'", m)
    for m in re.finditer(r"(?<![\w'.(\[/])i(?=\s+(?:am|was|have|will|would|can|do|did|think|need|want|love)\b|'m|'ll|'ve|'d)", text):
        add("Pronoun 'I' should be capitalised", m)
    for m in re.finditer(r"\b[a-z]{2,}[.,;:!?][A-Z][a-z]+", text):
        if not re.search(r"\.(com|net|org|io|js|co|in)\b", m.group(0), re.I):
            add("Missing space after punctuation", m, 10)
    for m in re.finditer(r"[A-Za-z]\s+[,;:!?](?=\s|$)", text):
        add("Space before punctuation", m, 15)
    for m in re.finditer(r"[!?]{2,}|\.{4,}", text):
        add("Excessive punctuation", m, 15)
    for m in re.finditer(r"([A-Za-z.]+)[.!?]\s+([a-z]{2,})\b", text):
        prev = m.group(1).lower().rstrip(".")
        if prev not in ABBREV and len(prev) > 1 and not prev[-1].isdigit() and "." not in prev:
            add("Sentence should start with a capital letter", m, 15)
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if len(sent.split()) > 45:
            hits.append(("Very long sentence (45+ words) - consider splitting", sent[:90] + "…"))
    return hits


def _languagetool(text, tool):
    out = []
    for m in tool.check(text[:20000]):
        if m.ruleIssueType in ("misspelling",):     # spelling is handled separately
            continue
        s = max(m.offset - 25, 0)
        out.append((m.message, "…" + text[s:m.offset + m.errorLength + 25].replace("\n", " ") + "…"))
    return out


def run(site, custom_dict=None, use_languagetool=False, max_per_page=15):
    out = []
    if SpellChecker is None:
        return [issue(C, LOW, "no-spellchecker", "Spell checker library not installed",
                      "pip install pyspellchecker", site.start_url)]
    allow = {w.lower() for w in re.findall(r"[A-Za-z]+", urlparse(site.start_url).hostname or "")}
    if custom_dict:
        with open(custom_dict, encoding="utf-8") as fh:
            allow |= {l.strip().lower() for l in fh if l.strip()}
    checkers = {}
    lt = None
    if use_languagetool:
        try:
            import language_tool_python
            lt = language_tool_python.LanguageTool("en-US")
        except Exception as e:   # Java / download problems
            out.append(issue(C, LOW, "lt-unavailable", "LanguageTool could not start; using built-in grammar rules",
                             "Install Java 17+ and language_tool_python, or ignore.", site.start_url, str(e)[:150]))

    pages = site.html_pages
    per_page, word_pages = {}, defaultdict(set)
    for p in pages:
        soup = p.soup
        lang = _lang(p)
        checker = checkers.setdefault(lang, SpellChecker(language=lang))
        desc = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
        fields = [("title", _text(soup.find("title"))),
                  ("meta description", (desc.get("content") if desc else "") or ""),
                  ("headings", " . ".join(_text(h) for h in soup.find_all(re.compile(r"^h[1-6]$")))),
                  ("body", visible_text(soup))]
        spell, gram = [], []
        for name, txt in fields:
            if not txt.strip():
                continue
            for w, s in spelling(txt, checker, allow):
                spell.append((name, w, s))
            for msg, snip in (_languagetool(txt, lt) if lt else grammar(txt, lang)):
                gram.append((name, msg, snip))
        per_page[p.url] = (spell, gram)
        for _, w, _ in spell:
            word_pages[w.lower()].add(p.url)

    # a "misspelling" repeated on many pages is almost always brand/jargon, not a typo
    common = {w for w, urls in word_pages.items() if len(urls) >= max(4, 0.5 * len(pages))}
    for p in pages:
        spell, gram = per_page[p.url]
        spell = [s for s in spell if s[1].lower() not in common]
        if spell:
            detail = "; ".join(f"{w} -> {s or '?'} ({where})" for where, w, s in spell[:max_per_page])
            out.append(issue(C, MEDIUM if any(w[0] in ("title", "meta description", "headings") for w in spell) else LOW,
                             "spelling", "Possible spelling mistakes",
                             "Correct the words, or add legitimate terms to a custom dictionary (--dict).", p.url,
                             f"{len(spell)} word(s): {detail}"))
        for where, msg, snip in gram[:max_per_page]:
            out.append(issue(C, MEDIUM if where in ("title", "headings", "meta description") else LOW,
                             "grammar", "Grammar / style issue", "Rewrite the sentence.", p.url,
                             f"[{where}] {msg}: {snip}"))
    return out
