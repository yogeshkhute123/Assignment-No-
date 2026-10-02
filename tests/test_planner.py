import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from seo_audit.crawler import Crawler
from seo_audit import planner

BODY = "We sell yoga mats and teach yoga classes for beginners every day of the week. " * 30
PAGES = {
    "/": f"<html><head><title>Yoga Mats Store</title></head><body><h1>Yoga mats</h1><p>{BODY}</p><a href='/blog/yoga-for-beginners'>x</a></body></html>",
    "/blog/yoga-for-beginners": f"<html><head><title>Yoga for Beginners: First Steps</title></head><body><h1>Yoga for beginners</h1><h2>Choosing a thick mat</h2><p>{BODY}</p></body></html>",
}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        if self.path in PAGES:
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(PAGES[self.path].encode())
        else:
            self.send_response(404); self.end_headers()


def make_index():
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    site = Crawler(f"http://127.0.0.1:{srv.server_port}/", verbose=False).crawl()
    srv.shutdown()
    return planner.build_index(site)


def test_coverage_statuses():
    idx = make_index()
    assert planner.coverage("yoga mats", idx)[0] == "Covered"
    assert planner.coverage("yoga for beginners", idx)[0] == "Covered"
    assert planner.coverage("thick mat", idx)[0] == "Partial"          # only in an H2
    assert planner.coverage("yoga retreat in bali", idx)[0] == "Gap"
    assert planner.coverage("buy yoga blocks online", idx)[0] == "Gap"


def test_intent_and_suggestions():
    assert planner.intent("buy yoga blocks") == "transactional"
    assert planner.intent("best yoga mats") == "commercial"
    assert planner.intent("how to do a headstand") == "informational"
    title, page = planner.suggest("how to do a headstand", "informational")
    assert title.startswith("How To Do A Headstand?") and len(title) <= 62


def test_analyse_clusters_and_priority():
    idx = make_index()
    cands = {"yoga mats": {"score": 5}, "yoga retreat bali": {"score": 9}, "yoga retreat in bali price": {"score": 4},
             "meditation timer app": {"score": 7, "volume": 5000}}
    rows, clusters = planner.analyse(cands, idx)
    status = {r["keyword"]: r["status"] for r in rows}
    assert status["yoga mats"] == "Covered" and status["yoga retreat bali"] == "Gap"
    topics = {c["topic"]: c for c in clusters}
    assert topics["meditation timer app"]["volume"] == 5000
    assert any(c["count"] == 2 for c in clusters)                      # the two bali keywords merge
    assert clusters[0]["topic"] == "meditation timer app"              # volume beats autocomplete score


def test_expand_with_fake_fetcher_and_xlsx(tmp_path):
    fake = lambda q: [f"{q} one", f"{q} two"]
    scores = planner.expand_seeds(["yoga"], fetcher=fake, delay=0)
    assert "yoga one" in scores and scores["yoga one"] > scores["yoga two"]
    rows, clusters = planner.analyse({k: {"score": v} for k, v in scores.items()}, make_index())
    planner.write_xlsx(str(tmp_path / "p.xlsx"), "http://x", rows, clusters)
    assert (tmp_path / "p.xlsx").stat().st_size > 0


def test_keyword_csv(tmp_path):
    f = tmp_path / "k.csv"
    f.write_text("Keyword,Avg. monthly searches,KD\nYoga Block,\"1,900\",35\n")
    assert planner.load_keyword_csv(str(f)) == {"yoga block": {"volume": 1900.0, "difficulty": 35.0}}
