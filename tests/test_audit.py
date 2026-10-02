import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from seo_audit import run_audit
from seo_audit.content import grammar

LOREM = "This is a sentence about our products and services that we offer to customers every day. " * 20

PAGES = {
    "/": f"""<html lang="en"><head><title>Home</title></head><body><h1>Welcome</h1><h1>Second</h1>
        <p>We recieve many orders. This is a an example.  the the best shop. {LOREM}</p>
        <img src="/a.png"><a href="/about">click here</a> <a href="/missing">gone</a>
        <a href="/old">old</a> <a href="https://external.invalid/x">ext</a></body></html>""",
    "/about": f"""<html lang="en"><head><title>About us and our long story of being a very good company that cares ok</title>
        <meta name="description" content="short"><link rel="canonical" href="/about"></head>
        <body><h1>About</h1><h3>Skipped level</h3><p>Teh team is great. {LOREM}</p></body></html>""",
    "/dup": f"""<html lang="en"><head><title>Home</title></head><body><h1>Welcome</h1><p>{LOREM}</p></body></html>""",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/old":
            self.send_response(302); self.send_header("Location", "/about"); self.end_headers(); return
        if self.path in PAGES:
            body = PAGES[self.path].encode()
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body)
        elif self.path == "/sitemap.xml":
            xml = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>http://127.0.0.1:%d/dup</loc></url></urlset>' % self.server.server_port).encode()
            self.send_response(200); self.send_header("Content-Type", "application/xml"); self.end_headers(); self.wfile.write(xml)
        else:
            self.send_response(404); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(b"nope")


@pytest.fixture(scope="module")
def result():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield run_audit(f"http://127.0.0.1:{srv.server_port}/", max_pages=20, check_external=False, verbose=False)
    srv.shutdown()


def codes(result):
    return {i.code for i in result[1]}


def test_crawls_all_pages(result):
    assert {"/", "/about", "/dup", "/missing", "/old"} <= {u[u.index("/", 8):] for u in result[0].pages}


def test_technical(result):
    c = codes(result)
    assert {"http-4xx", "broken-internal-link", "no-viewport", "temp-redirect", "no-https", "no-robots",
            "orphan"} <= c


def test_onpage(result):
    c = codes(result)
    assert {"multi-h1", "dup-title", "meta-desc-short", "title-long", "img-no-alt", "generic-anchor",
            "heading-skip", "no-meta-desc"} <= c


def test_spelling_and_grammar(result):
    spell = " ".join(i.detail for i in result[1] if i.code == "spelling")
    assert "recieve" in spell and "Teh" in spell
    gram = " ".join(i.detail for i in result[1] if i.code == "grammar")
    assert "Repeated word 'the'" in gram
    assert "'a an'" not in gram


def test_scores_in_range(result):
    assert all(0 <= v <= 100 for v in result[2].values())


@pytest.mark.parametrize("text,expect", [
    ("It is an university.", "Use 'a' before 'university'"),
    ("She has a apple.", "Use 'an' before 'apple'"),
    ("You should of called.", "should have"),
    ("I think i am right.", "Pronoun 'I'"),
    ("It works.Next one.", "Missing space"),
    ("Wait what!!", "Excessive punctuation"),
    ("He go home. then he left.", "capital letter"),
])
def test_grammar_rules(text, expect):
    assert any(expect in m for m, _ in grammar(text)), grammar(text)


def test_grammar_no_false_positives():
    assert grammar("An hour ago, a user bought a unique product from the U.S. e.g. a car. Dr. Smith agreed.") == []
