"""Tests for scripts/build.py and scripts/add.py. Stdlib only: python3 -m unittest discover -s tests

Everything runs on a temporary copy of the repo, so the real files are never touched.
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import add  # noqa: E402
import build  # noqa: E402

DISTRICTS = ["terrain", "speed", "build", "economy", "body", "religion", "war", "grief", "games", "food", "machines", "cosmos"]


def text(tid, districts, **kw):
    """A minimal valid text with one metaphor per entry in `districts`."""
    t = {"id": tid, "title": f"Essay about {tid} things", "url": f"https://example.com/{tid}", "date": "2026-10-09",
         "author": "T. Author", "kind": "essay",
         "metaphors": [{"district": d, "title": f"m {d}", "paraphrase": f"p {d}"} for d in districts]}
    t.update(kw)
    return t


class Sandbox(unittest.TestCase):
    """Points build.py at a temp copy of the repo and exposes run()/load()."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        for name in ("index.html", "README.md"):
            shutil.copy(REPO / name, self.tmp / name)
        for name in ("texts", "districts"):
            shutil.copytree(REPO / name, self.tmp / name)
        saved = {k: getattr(build, k) for k in ("ROOT", "HTML", "README", "TEXTS", "DISTRICTS")}
        self.addCleanup(lambda: [setattr(build, k, v) for k, v in saved.items()])
        build.ROOT, build.HTML, build.README = self.tmp, self.tmp / "index.html", self.tmp / "README.md"
        build.TEXTS, build.DISTRICTS = self.tmp / "texts", self.tmp / "districts"

    def put(self, *texts):
        for t in texts:
            (build.TEXTS / f"{t['id']}.json").write_text(json.dumps(t), encoding="utf-8")

    def run_build(self, *flags):
        """-> exit code (0 = ok); the message of a failure is returned as the second item."""
        argv, sys.argv = sys.argv, ["build.py", *flags]
        err = io.StringIO()
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                build.main()
            return 0, ""
        except SystemExit as e:
            return (e.code if isinstance(e.code, int) else 1), str(e.code)
        finally:
            sys.argv = argv

    def data(self):
        return build.load_html()[1]


class TestCurrentRepo(unittest.TestCase):
    def test_index_and_readme_are_in_sync_with_texts(self):
        with contextlib.redirect_stdout(io.StringIO()):
            argv, sys.argv = sys.argv, ["build.py", "--check"]
            try:
                with self.assertRaises(SystemExit) as cm:
                    build.main()
            finally:
                sys.argv = argv
        self.assertEqual(cm.exception.code, 0, "run python3 scripts/build.py and commit the result")


class TestExtension(Sandbox):
    def test_nothing_new_changes_nothing(self):
        before = (build.HTML.read_text(encoding="utf-8"), build.README.read_text(encoding="utf-8"))
        self.assertEqual(self.run_build()[0], 0)
        self.assertEqual(before, (build.HTML.read_text(encoding="utf-8"), build.README.read_text(encoding="utf-8")))

    def test_new_text_only_appends_and_keeps_the_original_map(self):
        old = self.data()
        self.put(text("alpha", ["terrain", "games", "war"]))
        self.assertEqual(self.run_build()[0], 0)
        new = self.data()
        for a, b in zip(old["buildings"], new["buildings"]):
            self.assertEqual({k: v for k, v in a.items() if k != "k"}, {k: v for k, v in b.items() if k != "k"})
        self.assertEqual(len(new["buildings"]), len(old["buildings"]) + 3)
        self.assertEqual(new["rows"][:len(old["rows"])][0], old["rows"][0])
        self.assertIn("alpha", new["S"])
        self.assertEqual(self.run_build("--check")[0], 0, "a second run must be a no-op")

    def test_buildings_never_overlap_and_trees_stay_above_the_floor(self):
        self.put(*[text(f"t{chr(97 + i)}x", [DISTRICTS[i % 12], DISTRICTS[(i * 5 + 3) % 12]], url=f"https://e.com/{i}",
                        title=f"Distinct title number {chr(97 + i)} {chr(110 + i)}") for i in range(12)])
        self.assertEqual(self.run_build()[0], 0)
        d = self.data()
        cells = [p for b in d["buildings"] for p in build.footprint(b)]
        self.assertEqual(len(cells), len(set(cells)))
        self.assertGreaterEqual(1 - len(cells) / (4 * d["NB"] ** 2), build.TREE_FLOOR)
        used = set(cells)
        for bk in d["blocks"]:  # a new-area block holding single buildings keeps at least one tree
            if build.frontier(bk) and bk["c"] and not any(b["w"] == 2 and (b["x"], b["y"]) == build.cells(bk)[0] for b in d["buildings"]):
                self.assertTrue([p for p in build.cells(bk) if p not in used], bk)

    def test_wording_can_change_but_not_the_number_of_metaphors(self):
        p = build.TEXTS / "asher.json"
        t = json.loads(p.read_text(encoding="utf-8"))
        t["metaphors"][0]["paraphrase"] = "reworded"
        p.write_text(json.dumps(t), encoding="utf-8")
        self.assertEqual(self.run_build()[0], 0)
        self.assertEqual(next(r for r in self.data()["rows"] if r["s"] == "asher")["d"], "reworded")
        t["metaphors"].append(dict(t["metaphors"][0]))
        p.write_text(json.dumps(t), encoding="utf-8")
        self.assertNotEqual(self.run_build()[0], 0)

    def test_missing_text_file_is_refused(self):
        (build.TEXTS / "asher.json").unlink()
        self.assertNotEqual(self.run_build()[0], 0)


class TestValidation(Sandbox):
    def test_duplicate_title_or_url_is_refused_unless_allowed(self):
        self.put(text("copy", ["games"], title="Math, accelerating toward light speed"))
        self.assertNotEqual(self.run_build()[0], 0)
        self.put(text("copy", ["games"], title="Math, accelerating toward light speed", duplicate_ok=True))
        self.assertEqual(self.run_build()[0], 0)

    def test_bad_inputs_are_refused(self):
        for bad in (text("a1", ["nope"]), text("a1", ["games"], kind="poem"), text("a1", ["games"], date="2026-1-1"),
                    text("a1", ["games"], url="https://x.com/a b"), text("a1", ["games"], url='https://x.com/"onload=')):
            with self.subTest(bad=bad):
                self.put(bad)
                self.assertNotEqual(self.run_build()[0], 0)

    def test_scraped_text_cannot_close_the_script_tag(self):
        self.put(text("evil", ["games"], metaphors=[{"district": "games", "title": "</script><b>", "paraphrase": "a & b <x>"}]))
        self.assertEqual(self.run_build()[0], 0)
        html = build.HTML.read_text(encoding="utf-8")
        blob = html[html.index("const DATA = "):]
        blob = blob[:blob.index("\n")]
        self.assertNotIn("</script>", blob)
        self.assertEqual(self.data()["rows"][-1]["t"], "</script><b>")


class TestNewDistrict(Sandbox):
    def test_a_district_file_is_added_once_with_a_label(self):
        (build.DISTRICTS / "weather.json").write_text(json.dumps(
            {"id": "weather", "name": "Weather", "icon": '<path d="M3 12h18"/>'}), encoding="utf-8")
        self.put(text("storm", ["weather"]))
        self.assertEqual(self.run_build()[0], 0)
        d = self.data()
        self.assertEqual([c["id"] for c in d["clusters"]].count("weather"), 1)
        self.assertIn("weather", d["labels"])
        self.assertIn("weather", d["ICONS"])
        self.assertEqual(self.run_build("--check")[0], 0)

    def test_unknown_district_is_refused(self):
        self.put(text("storm", ["weather"]))
        self.assertNotEqual(self.run_build()[0], 0)


class TestAdd(unittest.TestCase):
    def test_duplicate_detection_before_the_llm(self):
        known = {"loh": ("Why Do We Need Human Mathematicians Anymore?", "https://terrytao.wordpress.com/2026/09/19/x/")}
        find = build.find_duplicate
        self.assertEqual(find("Why Do We Need Human Mathematicians Anymore? | Po-Shen Loh", "https://a.com/1", known), "loh")
        self.assertEqual(find("Other", "http://www.terrytao.wordpress.com/2026/09/19/x", known), "loh")
        self.assertIsNone(find("A completely different essay", "https://a.com/1", known))

    def test_page_parser_skips_scripts_and_reads_meta(self):
        p = add.Page()
        p.feed('<html><head><title>T</title><meta property="og:title" content="OG"><script>bad()</script></head>'
               '<body><nav>menu</nav><p>Hello</p><time datetime="2026-10-01">x</time></body></html>')
        self.assertEqual("".join(p.text).strip(), "Hello\nx")  # script and nav text are dropped
        self.assertEqual((p.title, p.meta["og:title"], p.meta["time"]), ("T", "OG", "2026-10-01"))

    def test_tweet_date(self):
        self.assertEqual(add.parse_tweet_date("hi\n— Name (@x) October 8, 2026\n"), "2026-10-08")
        self.assertEqual(add.parse_tweet_date("no date"), "")

    def test_only_http_urls_are_fetched(self):
        with self.assertRaises(SystemExit):
            add.get("file:///etc/passwd")

    def test_ids_are_safe(self):
        self.assertEqual(add.make_id("Litt Babel!"), "littBabel")
        self.assertEqual(add.make_id("../../x"), "x")
        self.assertEqual(add.make_id("123"), "t123")


if __name__ == "__main__":
    unittest.main()
