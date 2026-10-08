#!/usr/bin/env python3
"""Extend the city from texts/*.json. Never moves anything already built.

    python3 scripts/build.py            # add new texts to index.html + README table
    python3 scripts/build.py --check    # exit 1 if index.html / README are out of date

Input : one texts/<id>.json per text (see README, "Adding a text").
State : the DATA blob inside index.html is the ledger of what is already built.
A text whose id is already in DATA is only checked (and its wording refreshed in
place); a text whose id is not in DATA is placed in the unexplored frontier.
"""
import collections
import copy
import itertools
import json
import math
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
HTML, README, TEXTS, DISTRICTS = ROOT / "index.html", ROOT / "README.md", ROOT / "texts", ROOT / "districts"
KINDS = ("essay", "comment", "tweet")
MAX_PER_BLOCK = 3  # keep at least one tree per block so the map can breathe
TREE_FLOOR = 0.4   # and at least this share of all cells stays tree/park; below it the map grows instead
FIRST_RING = 6  # blocks with max(i,j) >= 6 are the expansion area; the original 6x6 city is frozen


def die(msg):
    sys.exit("build.py: " + msg)


def ensure(cond, msg):
    """Invariant check that, unlike `assert`, survives `python -O`."""
    if not cond:
        die("internal check failed, nothing written: " + msg)


def read(path):
    return path.read_text(encoding="utf-8")


def write_atomic(path, text):
    """Write via a temp file so an interrupted run never leaves a half-written page."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_json(path):
    try:
        return json.loads(read(path))
    except json.JSONDecodeError as e:
        die(f"{path.name}: invalid JSON ({e})")


def frontier(bk):
    return max(bk["i"], bk["j"]) >= FIRST_RING


def cells(bk):
    return [(3 * bk["i"] + 1 + a, 3 * bk["j"] + 1 + b) for b in (0, 1) for a in (0, 1)]


def footprint(b):
    return [(b["x"] + a, b["y"] + c) for a in range(b["w"]) for c in range(b["w"])]


# ---------- read / write the DATA blob ----------
def load_html():
    html = read(HTML)
    m = re.search(r"const DATA = ", html)
    if not m:
        die("no 'const DATA = ' found in index.html")
    data, n = json.JSONDecoder().raw_decode(html[m.end():])
    return html, data, m.end(), m.end() + n


def dump(data):
    """The blob goes inside a <script>: escape <, > and & so scraped text can never close the tag
    (\\u003c etc. are plain JSON escapes, the page reads the same values)."""
    out = json.dumps(data, separators=(",", ":"))  # ensure_ascii also covers U+2028/2029
    return out.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


# ---------- duplicates ----------
def _alnum(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def norm_url(u):
    p = urlparse(u)
    return p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")


def same_title(a, b):
    a, b = _alnum(a), _alnum(b)
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= 12 and (a in b or b in a)))


def find_duplicate(title, url, known):
    """known: {id: (title, url)} -> id of a text with the same URL or title, else None."""
    for tid, (t, u) in known.items():
        if norm_url(u) == norm_url(url) or same_title(t, title):
            return tid


# ---------- new districts ----------
def load_districts():
    """districts/<id>.json {id, name, icon}: districts that can be added to the city. The page also needs a
    BUILD.<id> drawing function in index.html (hand-made, see README)."""
    out = {}
    for p in sorted(DISTRICTS.glob("*.json")):
        d = read_json(p)
        if d.get("id") != p.stem or not all(isinstance(d.get(k), str) and d[k] for k in ("name", "icon")):
            die(f"{p.name}: needs 'id' (= file name), 'name' and 'icon' (inner SVG of a 24x24 stroke icon)")
        out[d["id"]] = d
    return out


def ensure_district(data, avail, c):
    if c not in {k["id"] for k in data["clusters"]}:
        data["clusters"].append({"id": c, "name": avail[c]["name"]})
        data["ICONS"][c] = avail[c]["icon"]


def label_new_districts(data):  # a new district's label sits on the middle of its first block
    for c in data["clusters"]:
        if c["id"] not in data["labels"]:
            bk = next(b for b in data["blocks"] if b["c"] == c["id"])
            data["labels"][c["id"]] = [3 * bk["i"] + 2, 3 * bk["j"] + 2]


# ---------- read / validate texts ----------
def load_texts(districts):
    out = {}
    for p in sorted(TEXTS.glob("*.json")):
        t = read_json(p)
        where = p.name + ": "
        if t.get("id") != p.stem or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", p.stem):
            die(where + "'id' must equal the file name (letters/digits, e.g. littBabel)")
        for f in ("title", "url", "date", "author", "kind", "metaphors"):
            if not t.get(f):
                die(where + f"missing '{f}'")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", t["date"]):
            die(where + "date must be YYYY-MM-DD")
        if not re.fullmatch(r"https?://[^\s\"'<>]+", t["url"]):
            die(where + "url must be a plain http(s) URL (no spaces, quotes or angle brackets)")
        if t["kind"] not in KINDS:
            die(where + f"kind must be one of {KINDS}")
        if not isinstance(t["metaphors"], list) or not all(isinstance(m, dict) for m in t["metaphors"]):
            die(where + "'metaphors' must be a list of objects")
        for m in t["metaphors"]:
            if m.get("district") not in districts:
                die(where + f"unknown district {m.get('district')!r}; existing: {', '.join(districts)}. "
                    "To add a district: districts/<id>.json plus BUILD.<id> in index.html.")
            if not m.get("title") or not m.get("paraphrase"):
                die(where + "every metaphor needs 'title' and 'paraphrase'")
            if not isinstance(m.get("style_hint", ""), str):
                die(where + "'style_hint' must be a string (what the building should look like)")
        out[p.stem] = t
    return out


# ---------- reconcile texts with what is already built ----------
def reconcile(data, texts):
    for s in data["S"]:
        if s not in texts:
            die(f"texts/{s}.json is missing: that text is already in the city")
    for tid, t in texts.items():
        if tid not in data["S"]:
            continue
        built, want = collections.defaultdict(list), collections.defaultdict(list)
        for b in data["buildings"]:
            if b["s"] == tid:
                built[b["c"]] += b["m"]
        for m in t["metaphors"]:
            want[m["district"]].append(m)
        if {c: len(v) for c, v in want.items()} != {c: len(v) for c, v in built.items()}:
            die(f"{tid}: metaphors per district differ from the built city; that would move buildings. "
                "Wording can be edited freely, but keep the same number of metaphors in each district.")
        data["S"][tid] = {"t": t["title"], "u": t["url"], "d": t["date"]}
        for c, ms in want.items():
            for k, m in zip(built[c], ms):
                data["rows"][k].update(t=m["title"], d=m["paraphrase"], a=t["author"], k=t["kind"])
                if m.get("style_hint"):
                    data["rows"][k]["h"] = m["style_hint"]


def freeze_style_index(data):
    """The page used to derive each building's style index (b.k) from its rank in the district.
    A new building elsewhere could change that rank, so store it. Values equal the old computed ones."""
    for c in data["clusters"]:
        todo = sorted((b for b in data["buildings"] if b["c"] == c["id"] and b["w"] == 1 and "k" not in b),
                      key=lambda b: (b["x"] + b["y"], b["x"]))
        for k, b in enumerate(todo):
            b["k"] = k


# ---------- placement ----------
def add_text(data, t, avail):
    data["S"][t["id"]] = {"t": t["title"], "u": t["url"], "d": t["date"]}
    per = collections.OrderedDict()
    for m in t["metaphors"]:
        row = {"c": m["district"], "t": m["title"], "d": m["paraphrase"], "a": t["author"], "s": t["id"], "k": t["kind"]}
        if m.get("style_hint"):
            row["h"] = m["style_hint"]  # not rendered yet; guides custom building art
        data["rows"].append(row)
        per.setdefault(m["district"], []).append(len(data["rows"]) - 1)
    for c, ms in per.items():
        ensure_district(data, avail, c)
        w = 2 if len(ms) >= 3 else 1  # 3+ metaphors get a whole block
        x, y = pick_slot(data, c, w)
        b = {"c": c, "s": t["id"], "m": ms, "n": len(ms), "x": x, "y": y, "w": w}
        if w == 1:
            b["k"] = 1 + max((o["k"] for o in data["buildings"] if o["c"] == c and o["w"] == 1), default=-1)
        data["buildings"].append(b)  # append only: a building's drawing seed is its index


def room(data, n):
    """Can n more cells be built on while the whole map keeps TREE_FLOOR of its cells as trees?"""
    total = 4 * data["NB"] ** 2
    return (total - len({p for b in data["buildings"] for p in footprint(b)}) - n) / total >= TREE_FLOOR


def pick_slot(data, c, w):
    blocks = data["blocks"]
    used = {p for b in data["buildings"] for p in footprint(b)}
    if w == 1 and room(data, 1):  # replace a tree in a block of this district (original or new)
        spots = [[p for p in cells(bk) if p not in used] for bk in blocks if bk["c"] == c and not bk.get("open")]
        spots = [f for f in spots if len(f) > 4 - MAX_PER_BLOCK]  # the block keeps a tree
        if spots:
            return max(spots, key=len)[0]  # the block with the most trees left (first one on ties)
    while not any(bk.get("open") for bk in blocks) or not room(data, 1 if w == 1 else 4):
        grow(data)
    mine = [bk for bk in blocks if bk["c"] == c and frontier(bk)]
    if mine:  # stay next to what this district already built out here
        gi, gj = (sum(bk[a] for bk in mine) / len(mine) for a in "ij")
    elif c in data["labels"]:  # otherwise the open block closest to the district's own label
        gi, gj = (v / 3 for v in data["labels"][c])
    else:     # a brand-new district starts near the middle of the map
        gi = gj = data["NB"] / 2
    bk = min((b for b in blocks if b.get("open")), key=lambda b: (math.hypot(b["i"] - gi, b["j"] - gj), b["j"], b["i"]))
    bk["c"] = c
    del bk["open"]
    return cells(bk)[0]


def grow(data):  # one new L-shaped ring of unexplored blocks along +x / +y
    n = data["NB"]
    data["NB"] = n + 1
    data["blocks"] += [{"i": n, "j": j, "c": None, "open": True} for j in range(n + 1)]
    data["blocks"] += [{"i": i, "j": n, "c": None, "open": True} for i in range(n)]


def refresh_decor(data):
    """Trees on every cell without a building. Trees under a new building are dropped; the new area
    (open blocks included, as parks) is regenerated. Original trees elsewhere are never touched."""
    used = {p for b in data["buildings"] for p in footprint(b)}
    fr = [bk for bk in data["blocks"] if frontier(bk)]
    skip = {p for bk in fr for p in cells(bk)}
    data["decor"] = [d for d in data["decor"] if (d["x"], d["y"]) not in skip and (d["x"], d["y"]) not in used]
    for bk in fr:
        data["decor"] += [{"x": x, "y": y, "c": bk["c"]} for x, y in cells(bk) if (x, y) not in used]


def refresh_links(data):
    sets = collections.defaultdict(set)
    for r in data["rows"]:
        sets[r["c"]].add(r["s"])
    new = {}
    for a, b in itertools.combinations(sorted(sets), 2):
        both = sets[a] & sets[b]
        if both:
            new[frozenset((a, b))] = (a, b, len(both), round(len(both) / len(sets[a] | sets[b]), 2))
    links = []
    for l in data["links"]:  # keep existing order, update values
        k = frozenset((l["a"], l["b"]))
        if k in new:
            n = new.pop(k)
            links.append({"a": l["a"], "b": l["b"], "texts": n[2], "j": n[3]})
    data["links"] = links + [{"a": a, "b": b, "texts": n, "j": j} for a, b, n, j in sorted(new.values())]


# ---------- README ----------
def readme_table(data):
    cl = {c["id"]: c["name"] for c in data["clusters"]}
    au, kind, cs = {}, {}, collections.defaultdict(collections.Counter)
    for r in data["rows"]:
        au[r["s"]], kind[r["s"]] = r["a"], r["k"]
        cs[r["s"]][r["c"]] += 1
    L = ["| Date | Authors | Title | Type | Topics (districts) | Metaphors | URL | ID |", "|---|---|---|---|---|---|---|---|"]
    for k, v in sorted(data["S"].items(), key=lambda kv: (kv[1]["d"], kv[0])):
        host = urlparse(v["u"]).netloc.removeprefix("www.")
        L.append(f"| {v['d']} | {au[k]} | {v['t'].replace('|', chr(92) + '|')} | {kind[k]} | "
                 f"{', '.join(cl[c] for c, _ in cs[k].most_common())} | {sum(cs[k].values())} | [{host}]({v['u']}) | `{k}` |")
    return "\n".join(L)


def render_readme(data):
    r = read(README)
    if "<!-- sources:start -->" not in r or "<!-- sources:end -->" not in r:
        die("README.md lost its <!-- sources:start --> / <!-- sources:end --> markers")
    a, b = r.index("<!-- sources:start -->"), r.index("<!-- sources:end -->")
    intro = (f"\n\nThe {len(data['S'])} texts currently in the city, oldest first. *Type* is `essay` (a post or paper), "
             "`comment` (reader comments on a post) or `tweet`. *Topics* are the districts where the text has buildings, "
             "most metaphors first. *ID* is the file name in `texts/` and the key in `DATA.S` in `index.html`. "
             "This table is generated by `scripts/build.py`.\n\n")
    return r[:a] + "<!-- sources:start -->" + intro + readme_table(data) + "\n\n" + r[b:]


# ---------- the guarantee ----------
def assert_extension_only(old, new):
    """The guarantee: everything already on the original map is untouched; the rest is only appended to."""
    ensure(len(new["buildings"]) >= len(old["buildings"]) and len(new["rows"]) >= len(old["rows"]), "something was removed")
    for i, b in enumerate(old["buildings"]):
        nb = new["buildings"][i]
        ensure({k: v for k, v in nb.items() if k != "k"} == {k: v for k, v in b.items() if k != "k"}, f"building {i} moved")
        ensure(b.get("k", nb.get("k")) == nb.get("k"), f"building {i} restyled")
    for a, b in zip(old["rows"], new["rows"]):
        ensure((a["c"], a["s"]) == (b["c"], b["s"]), "a metaphor was reassigned")
    ensure(new["clusters"][:len(old["clusters"])] == old["clusters"], "districts changed")
    for key in ("labels", "ICONS"):
        ensure(all(new[key].get(k) == v for k, v in old[key].items()), key + " changed")
    ensure(all(b in new["blocks"] for b in old["blocks"] if not frontier(b)), "an original block changed")
    built = {p for b in new["buildings"] for p in footprint(b)}
    ensure(all(d in new["decor"] or (d["x"], d["y"]) in built for d in old["decor"]
               if (d["x"] - 1) // 3 < FIRST_RING and (d["y"] - 1) // 3 < FIRST_RING), "an original tree was removed without a building")


def main():
    check = "--check" in sys.argv
    html, data, s, e = load_html()
    old = copy.deepcopy(data)
    avail = load_districts()
    reconcile(data, texts := load_texts([c["id"] for c in data["clusters"]] + sorted(avail)))
    freeze_style_index(data)
    known = {i: (v["t"], v["u"]) for i, v in data["S"].items()}
    for t in sorted((t for i, t in texts.items() if i not in data["S"]), key=lambda t: (t["date"], t["id"])):
        dup = None if t.get("duplicate_ok") else find_duplicate(t["title"], t["url"], known)
        if dup:
            die(f"{t['id']} looks like a duplicate of {dup} ({known[dup][0]!r}). If it really is a different text, "
                'add "duplicate_ok": true to its JSON; otherwise delete texts/' + t["id"] + ".json.")
        known[t["id"]] = (t["title"], t["url"])
        add_text(data, t, avail)
    label_new_districts(data)
    refresh_decor(data)
    refresh_links(data)
    assert_extension_only(old, data)
    new_html, new_readme = html[:s] + dump(data) + html[e:], render_readme(data)
    if check:
        sys.exit(0 if new_html == html and new_readme == read(README) else 1)
    write_atomic(HTML, new_html)
    write_atomic(README, new_readme)
    n = len(data["S"]) - len(old["S"])
    print(f"{n} text(s) added, NB {old['NB']} -> {data['NB']}" if n else "nothing new to add")


if __name__ == "__main__":
    main()
