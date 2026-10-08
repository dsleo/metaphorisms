#!/usr/bin/env python3
"""Draft texts/<id>.json from a URL with an LLM. Stops after drafting: review, then run build.py.

    python3 scripts/add.py <url> [--model gpt-6-luna] [--force]   # OPENAI_API_KEY from the environment or .env

Only existing districts are handled automatically. If the LLM says a metaphor fits none of them,
the draft goes to texts/_pending/ (ignored by build.py) with a note, and a new district has to be
designed in a Claude Code session.
"""
from datetime import datetime
import html.parser, json, os, re, sys, urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build

MAX_CHARS = 60000
DEFAULT_MODEL = "gpt-6-luna"
UA = {"User-Agent": "Mozilla/5.0 (metaphorisms add.py)"}


def load_env():  # minimal .env reader: KEY=value lines, real environment wins
    f = build.ROOT / ".env"
    for line in f.read_text().splitlines() if f.exists() else []:
        k, _, v = line.partition("=")
        if k.strip() and not line.lstrip().startswith("#"):
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def get(url, **kw):
    req = urllib.request.Request(url, headers={**UA, **kw.pop("headers", {})}, **kw)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")


class Page(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(); self.text, self.meta, self.skip, self.title = [], {}, 0, ""; self.in_title = False
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "nav", "footer", "noscript"): self.skip += 1
        if tag == "title": self.in_title = True
        if tag == "meta" and a.get("content"): self.meta[a.get("property") or a.get("name") or ""] = a["content"]
        if tag == "time" and a.get("datetime"): self.meta.setdefault("time", a["datetime"])
    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "noscript"): self.skip = max(0, self.skip - 1)
        if tag == "title": self.in_title = False
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3"): self.text.append("\n")
    def handle_data(self, d):
        if self.in_title: self.title += d
        elif not self.skip: self.text.append(d)


def fetch(url):
    """-> dict(title, author, date, text). Tweets via oEmbed; arXiv abs pages work as plain HTML."""
    host = urllib.parse.urlparse(url).netloc
    if host in ("twitter.com", "x.com", "www.twitter.com", "www.x.com"):
        o = json.loads(get("https://publish.twitter.com/oembed?omit_script=1&url=" + urllib.parse.quote(url, safe="")))
        p = Page(); p.feed(o["html"])
        text = "".join(p.text)
        m = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})\s*$", text.strip())  # "... — Name (@x) October 8, 2026"
        date = datetime.strptime(m.group(1), "%B %d, %Y").strftime("%Y-%m-%d") if m else ""
        return {"title": f"Tweet by {o['author_name']}", "author": o["author_name"], "date": date, "text": text, "short": True}
    p = Page(); p.feed(get(url))
    m = p.meta
    date = next((m[k][:10] for k in ("article:published_time", "citation_publication_date", "citation_date", "date", "time") if k in m), "")
    return {"title": m.get("og:title") or m.get("citation_title") or p.title.strip(),
            "author": m.get("author") or m.get("citation_author") or "", "date": date.replace("/", "-"),
            "text": re.sub(r"\n\s*\n+", "\n\n", "".join(p.text)).strip()[:MAX_CHARS]}


def prompt(page, url, data):
    ex = {}
    for r in data["rows"]:
        ex.setdefault(r["c"], []).append(r["t"])
    dist = "\n".join(f'- {c["id"]}: {c["name"]} (e.g. {"; ".join(ex.get(c["id"], [])[:4])})' for c in data["clusters"])
    return f"""You catalogue the metaphors that mathematicians and writers use when talking about AI and mathematics.
Read the text below and list its distinct metaphors/analogies/images (not plain arguments). For each:
- district: the family the image is drawn FROM (what AI or mathematics is being compared to), as one id from the list below. Do not force a fit: if the source domain is not one of these families (for example a mathematical structure such as a hull or a group used as a picture for AI), answer "NEW" and give new_district: a short name for the missing family. For other metaphors, "NEW" must not be used just because the fit is imperfect.
- district_reason: one short line saying what the image is drawn from and why that district (or why none).
- title: 2-5 words naming the image.
- paraphrase: one or two sentences in your own words (never quote), saying what the image says about AI/mathematics.
- style_hint: one sentence describing the building to draw for this image (concrete shapes and objects).
Also give: id (camelCase, author's surname plus a short word if needed), title, author (display name; "Readers on X's post" for comment threads), kind ("essay" or "comment"), date (YYYY-MM-DD, use the hint if given).
Be selective: only real, vivid metaphors, between 1 and 8. Existing metaphors in the city, for calibration:
{dist}

URL: {url}
Hints from the page: title={page['title']!r} author={page['author']!r} date={page['date']!r}
TEXT:
{page['text']}"""


SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["id", "title", "author", "kind", "date", "metaphors"],
          "properties": {"id": {"type": "string"}, "title": {"type": "string"}, "author": {"type": "string"},
                         "kind": {"type": "string", "enum": ["essay", "comment"]}, "date": {"type": "string"},
                         "metaphors": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                             "required": ["district", "district_reason", "new_district", "title", "paraphrase", "style_hint"],
                             "properties": {k: {"type": "string"} for k in ("district", "district_reason", "new_district", "title", "paraphrase", "style_hint")}}}}}


def ask(content, model):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": content}],
                       "response_format": {"type": "json_schema", "json_schema": {"name": "text", "strict": True, "schema": SCHEMA}}}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", body,
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(json.loads(r.read())["choices"][0]["message"]["content"])


def write_draft(resp, url, page, districts, force=False):
    """-> (path, new_districts). Pure file logic, no network."""
    tid = re.sub(r"[^A-Za-z0-9]", "", resp["id"]) or "text"
    tid = tid[0].lower() + tid[1:] if tid[0].isalpha() else "t" + tid
    if tid in {p.stem for p in (build.TEXTS.glob("*.json"))} | {p.stem for p in (build.TEXTS / "_pending").glob("*.json")}:
        sys.exit(f"{tid} already exists in texts/ (is this URL already in the city?)")
    new = {m["new_district"] for m in resp["metaphors"] if m["district"] not in districts}
    ms = []
    for m in resp["metaphors"]:
        d = {"district": m["district"] if m["district"] in districts else "NEW", "title": m["title"],
             "paraphrase": m["paraphrase"], "style_hint": m["style_hint"], "district_reason": m["district_reason"]}
        if d["district"] == "NEW":
            d["new_district"] = m["new_district"]
        ms.append(d)
    t = {"id": tid, "title": resp["title"], "url": url, "date": page["date"] or resp["date"],
         "author": resp["author"], "kind": resp["kind"], "metaphors": ms}
    if force:
        t["duplicate_ok"] = True
    out = build.TEXTS / ("_pending" if new else "") / f"{tid}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(t, indent=2, ensure_ascii=False) + "\n")
    return out, new


def main():
    load_env()
    argv = sys.argv[1:]
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    if "--model" in argv:
        i = argv.index("--model"); model = argv[i + 1]; del argv[i:i + 2]
    force = "--force" in argv
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1 or "OPENAI_API_KEY" not in os.environ:
        sys.exit(__doc__)
    url = args[0]
    _, data, _, _ = build.load_html()
    page = fetch(url)
    known = {i: (v["t"], v["u"]) for i, v in data["S"].items()}
    dup = None if force else build.find_duplicate(page["title"], url, known)  # before any LLM call
    if dup:
        sys.exit(f"Already in the city as {dup!r} ({known[dup][0]!r}, {known[dup][1]}). Use --force to add it anyway.")
    if len(page["text"]) < (40 if page.get("short") else 500):
        sys.exit("Fetched too little text (paywall/JS page?). Paste the text into a file or use a Claude Code session.")
    out, new = write_draft(ask(prompt(page, url, data), model), url, page, [c["id"] for c in data["clusters"]], force)
    print(f"Draft: {out.relative_to(build.ROOT)}")
    return out, new


def cli():
    out, new = main()
    if new:
        print("Some metaphors fit no existing district: " + ", ".join(sorted(new)) +
              ".\nA new district needs building art; take this draft to a Claude Code session. build.py ignores texts/_pending/.")
    else:
        print("Review the metaphors and style hints, then run: python3 scripts/build.py")


if __name__ == "__main__":
    cli()
