#!/usr/bin/env python3
"""Capital Controls Early-Warning Monitor: renders the site from state.json.

Usage: python3 build_report.py --state state.json --out site_dir
Reads the tracked items, today's report fields, levels, dates, source checks and triggers
from state.json and writes:
  site_dir/docs/index.html, site_dir/docs/archive/<date>.html, site_dir/docs/archive/index.html,
  site_dir/docs/.nojekyll, site_dir/data/state.json (with refreshed_at set), site_dir/README.md
Never hand-edit the HTML; change state.json and rebuild.
"""
import argparse, datetime as dt, html, json, os, sys
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
ap = argparse.ArgumentParser()
ap.add_argument("--state", required=True, help="state.json to read (missing is fine with --update)")
ap.add_argument("--update", help="optional update JSON to merge into the state first")
ap.add_argument("--out", required=True)
a = ap.parse_args()
S = json.load(open(a.state, encoding="utf-8")) if os.path.exists(a.state) else {"schema_version": 1, "items": {}, "levels": []}
if a.update:
    U = json.load(open(a.update, encoding="utf-8"))
    cur = (S.get("report") or {}).get("date")
    if cur and U["report"]["date"] < cur:
        print(f"Update for {U['report']['date']} is older than the published report ({cur}); nothing changed.")
        sys.exit(0)
    for k in ("github_user", "repo", "monitor", "schema_version"):
        if U.get(k):
            S[k] = U[k]
    S["report"] = U["report"]
    lv = {h["date"]: h["level"] for h in S.get("levels", [])}
    lv.update({h["date"]: h["level"] for h in U.get("levels", [])})
    S["levels"] = [{"date": d, "level": l} for d, l in sorted(lv.items())]
    S.setdefault("items", {}).update(U.get("upsert_items", {}))
    for k in ("upcoming_dates", "source_checks", "raise_level_triggers"):
        if k in U:
            S[k] = U[k]
R = S["report"]
TODAY = dt.date.fromisoformat(R["date"])
USER, REPO = S.get("github_user") or "GITHUB-USERNAME", S.get("repo") or "capital-controls-monitor"
LIVE = f"https://{USER}.github.io/{REPO}/"
now = dt.datetime.now(TZ)
R["refreshed_at"] = now.isoformat(timespec="seconds")
checked_through = dt.datetime.fromisoformat(R.get("sources_checked_through") or R["refreshed_at"]).astimezone(TZ)

E = lambda s: html.escape(str(s if s is not None else ""), quote=True)
def pdate(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None
def fdate(s):
    d = s if isinstance(s, dt.date) else pdate(s)
    return d.strftime("%b %-d, %Y") if d else "Date unconfirmed"
def ftime(t):
    return t.strftime("%A, %B %-d, %Y, %-I:%M %p ") + t.tzname()

LEVELS = {"GREEN": ("🟢", "Quiet", "lvl-green"), "YELLOW": ("🟡", "Subtle movement", "lvl-yellow"),
          "ORANGE": ("🟠", "Concerning", "lvl-orange"), "RED": ("🔴", "Serious", "lvl-red")}
level = R["level"]
emoji, level_label, level_cls = LEVELS[level]
W_CT = {"Prohibition": 4, "Permission": 3, "Friction": 2, "Visibility": 1}
W_AF = {"All Americans": 3, "Sector or group": 2, "Named parties": 1}
W_ST = {"In effect": 4, "Adopted": 3, "Proposal": 2, "Study or pilot": 1, "Talk": 0}
W_TI = {3: 6, 2: 4, 1: 2}
def score(it):
    s = W_CT.get(it.get("control_type"), 0) + W_AF.get(it.get("affected"), 0) + W_ST.get(it.get("stage"), 0)
    s += W_TI.get(it.get("tier"), 0) + (1 if it.get("direction") == "Toward controls" else 0)
    return s * 0.3 if it.get("evidence") in ("Commentary only", "Unverified") else s
def sortkey(x):
    return (-score(x), -((pdate(x.get("date")) or dt.date(1900, 1, 1)).toordinal()))

WINDOW = int(R.get("display_window_days") or 120)
def shown(it):
    """Open official items always show; others show while recent or while a future milestone is pending."""
    if not it.get("open", True):
        return False
    if it.get("section") == "official_us":
        return True
    d = pdate(it.get("last_change") or it.get("date"))
    win = max(WINDOW, 365) if it.get("section") == "claims_checked" else WINDOW
    if d and (TODAY - d).days <= win:
        return True
    nm = pdate((it.get("next_milestone") or {}).get("date"))
    return bool(nm and nm >= TODAY)
items = {k: v for k, v in S["items"].items() if shown(v)}
SECTION_NAME = {"official_us": "Official U.S.", "pre_legislative": "Pre-legislative", "international": "International",
                "banking_stress": "Banking stress", "counter_signal": "Counter-signal", "claims_checked": "Claim check"}
by_sec = {}
for it in items.values():
    by_sec.setdefault(it.get("section"), []).append(it)
for k in by_sec:
    by_sec[k].sort(key=sortkey)
flags = [(items[f["id"]], f.get("movement", "NEW")) for f in R.get("flags", []) if f["id"] in items]
flags.sort(key=lambda p: sortkey(p[0]))

def short_quote(it):
    q = it.get("quote") or {}
    t = (q.get("text") or "").strip().strip('"').strip()
    return t if t and len(t.split()) < 15 else None

def sources_html(it):
    out = []
    for s in it.get("sources", [])[:8]:
        url = s.get("url") or ""
        if not url.startswith("http"):
            continue
        d = fdate(s.get("date")) if s.get("date") else ""
        prim = ' <span class="prim">primary</span>' if s.get("primary") else ""
        out.append(f'<li><a href="{E(url)}" rel="noopener" target="_blank">{E(s.get("publisher") or "Source")}: {E(s.get("title") or url)}</a>{" · " + E(d) if d else ""}{prim}</li>')
    return '<ul class="srcs">' + "".join(out) + "</ul>" if out else ""

def chips(it):
    c = []
    if it.get("control_type") and it["control_type"] != "None":
        c.append(("Control", it["control_type"]))
    for k, f in (("Affects", "affected"), ("Stage", "stage")):
        if it.get(f):
            c.append((k, it[f]))
    if it.get("tier"):
        sig = ", ".join("#" + str(n) for n in (it.get("signal_numbers") or []))
        c.append(("Tier", f'{it["tier"]}' + (f" ({sig})" if sig else "")))
    for k, f in (("Direction", "direction"), ("Evidence", "evidence")):
        if it.get(f):
            c.append((k, it[f]))
    cls = {"Toward controls": " dir-toward", "Away from controls": " dir-away"}
    return '<ul class="chips">' + "".join(f'<li class="chip{cls.get(v, "") if k == "Direction" else ""}"><span>{E(k)}</span> {E(v)}</li>' for k, v in c) + "</ul>"

def card(it, move=None, show_section=True):
    nm = it.get("next_milestone") or {}
    nxt = f'<p class="next"><strong>Next:</strong> {E(nm["what"])}{" · " + E(fdate(nm.get("date"))) if nm.get("date") else ""}</p>' if nm.get("what") else ""
    extra = ""
    if it.get("section") == "pre_legislative":
        rows = [("Who", it.get("who") or it.get("org")), ("Momentum", it.get("momentum")), ("Verdict", it.get("verdict")),
                ("Official acknowledgment", it.get("official_acknowledgment")), ("Linked bills or rules", it.get("linked_bills_or_rules"))]
        extra = '<dl class="facts">' + "".join(f"<dt>{E(k)}</dt><dd>{E(v)}</dd>" for k, v in rows if v) + "</dl>"
    if it.get("section") == "international" and it.get("us_link"):
        extra = f'<p class="uslink"><strong>Link to U.S. policy:</strong> {E(it["us_link"])}</p>'
    top = []
    if move:
        top.append(f'<span class="move move-{E(move.lower())}">{E(move)}</span>')
    if show_section:
        top.append(f'<span class="sec">{E(SECTION_NAME.get(it.get("section"), ""))}</span>')
    top.append(f'<time datetime="{E(it.get("date"))}">{E(fdate(it.get("date")))}</time>')
    q = short_quote(it)
    return (f'<article class="card"><div class="card-top">{" ".join(top)}</div><h3>{E(it.get("headline"))}</h3>'
            + (f'<p class="org">{E(it.get("org"))}</p>' if it.get("org") else "")
            + f'<p class="changed"><strong>What changed:</strong> {E(it.get("what_changed"))}</p><p>{E(it.get("summary"))}</p>'
            + (f"<blockquote>“{E(q)}”</blockquote>" if q else "")
            + chips(it) + extra
            + f'<div class="why"><p><strong>Why it matters:</strong> {E(it.get("why_it_matters"))}</p><p><strong>Why it might not:</strong> {E(it.get("why_it_might_not"))}</p></div>'
            + nxt + f"<details><summary>Sources</summary>{sources_html(it)}</details></article>")

def tracker_rows(lst):
    rows = []
    for it in lst:
        nm = it.get("next_milestone") or {}
        nxt = E(nm.get("what") or "None set") + (f'<br><span class="muted">{E(fdate(nm.get("date")))}</span>' if nm.get("date") else "")
        srcs = [s for s in it.get("sources", []) if (s.get("url") or "").startswith("http")]
        prim = next((s for s in srcs if s.get("primary")), srcs[0] if srcs else None)
        link = f'<a href="{E(prim["url"])}" rel="noopener" target="_blank">Source</a>' if prim else ""
        rows.append(f'''<tr><td data-label="Item"><strong>{E(it.get("headline"))}</strong><details><summary>Details</summary><p>{E(it.get("summary"))}</p><p><strong>Why it matters:</strong> {E(it.get("why_it_matters"))}</p><p><strong>Why it might not:</strong> {E(it.get("why_it_might_not"))}</p>{sources_html(it)}</details></td>
<td data-label="Agency or sponsor">{E(it.get("org"))}</td><td data-label="Stage">{E(it.get("stage"))}</td><td data-label="Control">{E(it.get("control_type"))}</td>
<td data-label="Affects">{E(it.get("affected"))}</td><td data-label="Tier">{E("Tier " + str(it["tier"]) if it.get("tier") else "None")}</td><td data-label="Next milestone">{nxt}</td>
<td data-label="First seen">{E(fdate(it.get("first_seen")))}</td><td data-label="Last change">{E(fdate(it.get("last_change") or it.get("date")))}</td><td data-label="Link">{link}</td></tr>''')
    return "".join(rows)

RATING_CLS = {"Supported": "r-sup", "Partly supported": "r-part", "Unsupported": "r-uns", "Can't verify": "r-cv"}
def claim_rows(lst):
    return "".join(f'''<tr><td data-label="Claim"><strong>{E(it.get("claim") or it.get("headline"))}</strong><br><span class="muted">{E(fdate(it.get("date")))}</span></td>
<td data-label="Who said it">{E(it.get("claimant") or it.get("who") or it.get("org"))}</td>
<td data-label="Rating"><span class="rating {RATING_CLS.get(it.get("claim_rating"), "r-cv")}">{E(it.get("claim_rating") or "Can't verify")}</span></td>
<td data-label="What the evidence shows">{E(it.get("summary"))}<details><summary>Sources</summary>{sources_html(it)}</details></td></tr>''' for it in lst)

ups = sorted([(pdate(u["date"]), u) for u in S.get("upcoming_dates", []) if pdate(u.get("date")) and pdate(u["date"]) >= TODAY], key=lambda x: x[0])
horizon = TODAY + dt.timedelta(days=90)
near, far = [x for x in ups if x[0] <= horizon], [x for x in ups if x[0] > horizon]
def date_rows(lst):
    return "".join(f'<tr><td data-label="Date">{E(fdate(d))}</td><td data-label="What">{E(u.get("what"))}</td><td data-label="Link">'
                   + (f'<a href="{E(u["url"])}" rel="noopener" target="_blank">Source</a>' if (u.get("url") or "").startswith("http") else "") + "</td></tr>" for d, u in lst) \
        or '<tr><td colspan="3">None</td></tr>'

RANK = {"Checked": 3, "Partial": 2, "Not checked": 1}
def check_rows():
    out = []
    for c in sorted(S.get("source_checks", []), key=lambda x: (-RANK.get(x.get("status"), 0), x["source"].lower())):
        st = c.get("status") or "Not checked"
        cls = {"Checked": "st-ok", "Partial": "st-part"}.get(st, "st-no")
        name = f'<a href="{E(c["url"])}" rel="noopener" target="_blank">{E(c["source"])}</a>' if (c.get("url") or "").startswith("http") else E(c["source"])
        out.append(f'<tr><td data-label="Source">{name}</td><td data-label="Status"><span class="st {cls}">{E(st)}</span></td><td data-label="Note">{E(c.get("note"))}</td><td data-label="Last successful check">{E(fdate(c["last_success"]) if c.get("last_success") else "None yet")}</td></tr>')
    return "".join(out)

hist = {h["date"]: h["level"] for h in S.get("levels", [])}
hist[TODAY.isoformat()] = level
S["levels"] = [{"date": d, "level": l} for d, l in sorted(hist.items())]
strip = []
for i in range(29, -1, -1):
    d = TODAY - dt.timedelta(days=i)
    lv = hist.get(d.isoformat())
    cls, txt = (LEVELS[lv][2], lv) if lv else ("none", "no report")
    strip.append(f'<li class="day {cls}" title="{E(fdate(d))}: {E(txt)}"><span class="sr">{E(fdate(d))}: {E(txt)}</span></li>')

CSS = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "report.css"), encoding="utf-8").read()

def page(home_prefix, archive_href):
    toc = [("flags", "What moved"), ("radar", "Pre-legislative radar"), ("tracker", "Official U.S. tracker"), ("stress", "Banking stress"),
           ("international", "International"), ("counter", "Counter-signals"), ("claims", "Claims checked"), ("dates", "Upcoming dates"),
           ("raise", "What would raise the level"), ("sources", "Source log"), ("about", "About")]
    sec = lambda k: by_sec.get(k, [])
    grid = lambda lst, empty: ("".join(card(it, None, False) for it in lst) or f'<p class="note">{E(empty)}</p>')
    flags_html = "".join(card(it, mv) for it, mv in flags) or '<p class="note">Nothing new or changed since the last report.</p>'
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="color-scheme" content="light dark">
<title>{emoji} {level} · Capital Controls Monitor · {E(fdate(TODAY))}</title>
<style>{CSS}</style>
</head>
<body data-refreshed="{E(R["refreshed_at"])}">
<header class="top" id="top"><div class="wrap">
<h1>Capital Controls Early-Warning Monitor</h1>
<p class="sub">A daily check of official U.S. sources, international bodies and the policy community for any movement toward limits on Americans' ability to withdraw, move, convert, invest or hold their money.</p>
<div class="meta"><span><strong>Last refreshed:</strong> {E(ftime(now))}</span><span><strong>Sources checked through:</strong> {E(ftime(checked_through))}</span></div>
<div class="level {level_cls}">
<span class="badge" role="img" aria-label="Alert level {level}: {E(level_label)}">{emoji} {level}</span>
<div class="bl"><strong>{E(level_label)}.</strong> {E(R.get("bottom_line"))}<p class="change"><strong>Change since the last report:</strong> {E(R.get("change_text"))} {E(R.get("level_reason"))}</p></div>
</div>
<ol class="strip" aria-label="Alert level for each of the last 30 days">{"".join(strip)}</ol>
<p class="strip-label">Last 30 days (oldest to newest; gray means no report)</p>
<div id="stale" class="stale" hidden>⚠ This report hasn't been refreshed in <span id="stale-hours">more than 26</span> hours. The latest daily run may be waiting for approval or may have failed, so check the date above.</div>
</div></header>
<nav class="toc" aria-label="Sections"><div class="wrap"><ul>{"".join(f'<li><a href="#{k}">{E(v)}</a></li>' for k, v in toc)}</ul></div></nav>
<main class="wrap">
<section id="flags"><h2>What moved</h2><p class="lede">{E(R.get("flags_lede"))}</p><div class="grid">{flags_html}</div></section>
<section id="radar"><h2>Pre-legislative radar</h2><p class="lede">Ideas people are developing or discussing that aren't bills or formal rules yet.</p><div class="grid">{grid(sec("pre_legislative"), "Nothing on the radar.")}</div></section>
<section id="tracker"><h2>Official U.S. tracker</h2><p class="lede">Every open official item: rules, bills, orders and programs. Open "Details" for the summary and sources.</p>
<div class="tablewrap"><table class="stack"><thead><tr><th>Item</th><th>Agency or sponsor</th><th>Stage</th><th>Control</th><th>Affects</th><th>Tier</th><th>Next milestone</th><th>First seen</th><th>Last change</th><th>Link</th></tr></thead><tbody>{tracker_rows(sec("official_us"))}</tbody></table></div></section>
<section id="stress"><h2>Banking stress context</h2><p class="lede">Bank failures, emergency lending and outflows. Stress alone isn't evidence of planned controls.</p><div class="grid">{grid(sec("banking_stress"), "No banking-stress items.")}</div></section>
<section id="international"><h2>International ideas that could migrate</h2><p class="lede">Foreign measures and proposals. A foreign idea doesn't mean the U.S. will adopt it.</p><div class="grid">{grid(sec("international"), "No international items.")}</div></section>
<section id="counter"><h2>Counter-signals</h2><p class="lede">Moves that protect access to and movement of money.</p><div class="grid">{grid(sec("counter_signal"), "No counter-signals.")}</div></section>
<section id="claims"><h2>Claims checked</h2><p class="lede">What commentators are saying, traced to sources. Commentary is a lead, never evidence, and can't raise the alert level.</p>
<div class="tablewrap"><table class="stack"><thead><tr><th>Claim</th><th>Who said it</th><th>Rating</th><th>What the evidence shows</th></tr></thead><tbody>{claim_rows(sec("claims_checked"))}</tbody></table></div></section>
<section id="dates"><h2>Upcoming dates</h2><p class="lede">Next 90 days: comment deadlines, votes, effective dates and expirations.</p>
<div class="tablewrap"><table class="stack"><thead><tr><th>Date</th><th>What</th><th>Link</th></tr></thead><tbody>{date_rows(near)}</tbody></table></div>
<h3>Further out</h3><div class="tablewrap"><table class="stack"><thead><tr><th>Date</th><th>What</th><th>Link</th></tr></thead><tbody>{date_rows(far)}</tbody></table></div></section>
<section id="raise"><h2>What would raise the level</h2><p class="lede">Specific next steps to watch for.</p><ul class="triggers">{"".join(f"<li>{E(t)}</li>" for t in S.get("raise_level_triggers", []))}</ul></section>
<section id="sources"><h2>Source check log</h2><p class="lede">"Not checked" means the source wasn't read this run. It doesn't mean nothing happened there.</p>
<div class="tablewrap"><table class="stack"><thead><tr><th>Source</th><th>Status</th><th>Note</th><th>Last successful check</th></tr></thead><tbody>{check_rows()}</tbody></table></div></section>
<section id="about"><h2>About this report</h2>
<p><strong>What counts:</strong> limits on Americans' ability to withdraw, move, convert, invest or hold money. A routine anti-fraud, sanctions, anti-money-laundering or tax-reporting rule isn't a capital control by itself, so each item says what the measure actually lets the government or a bank do.</p>
<p><strong>Control ladder:</strong> Visibility (reporting) → Friction (delays, extra checks, fees, taxes) → Permission (government approval needed) → Prohibition (ban, limit, freeze, forced conversion).</p>
<p><strong>Signal tiers:</strong> Tier 1 is subtle (new reporting, cash-reporting changes, outbound-investment limits, new official vocabulary, proposed emergency powers, payment systems that enable monitoring or conditions). Tier 2 is concerning (limits on transfers abroad or currency conversion, unexplained withdrawal limits, regulators discussing deposit limits, holding periods or redemption gates, limits on foreign assets, retirement-account limits). Tier 3 is serious (government withdrawal limits, broad bans on moving money overseas, forced conversion, bank holidays, emergency orders).</p>
<p><strong>Alert levels:</strong> 🟢 GREEN means quiet. 🟡 YELLOW means subtle movement. 🟠 ORANGE means an official proposal for a Tier 2 measure, or one adopted for a defined group. 🔴 RED means a Tier 3 measure is adopted, imminent or in effect for ordinary Americans. Measures limited to failed banks, sanctioned parties or named countries count toward YELLOW, not ORANGE. Commentary and unverified claims can't raise the level.</p>
<p><strong>How it's made:</strong> researched and compiled each morning with an AI assistant from public sources. It can contain errors, so check the linked sources before relying on anything.</p>
</section>
</main>
<footer><div class="wrap">
<p><a href="{E(archive_href)}">Past reports</a> · <a href="{E(home_prefix)}index.html">Latest report</a> · <a href="#top">Back to top</a></p>
<p>Personal research project. Informational only, not financial, legal or tax advice. Not affiliated with any employer.</p>
</div></footer>
<script>
(function(){{var b=document.body,el=document.getElementById("stale");if(!b||!el)return;var t=Date.parse(b.getAttribute("data-refreshed"));if(isNaN(t))return;var h=(Date.now()-t)/36e5;if(h>26){{document.getElementById("stale-hours").textContent=String(Math.floor(h));el.hidden=false;}}}})();
</script>
</body>
</html>
'''

O = a.out
os.makedirs(os.path.join(O, "docs", "archive"), exist_ok=True)
os.makedirs(os.path.join(O, "data"), exist_ok=True)
open(os.path.join(O, "docs", "index.html"), "w", encoding="utf-8").write(page("", "archive/index.html"))
open(os.path.join(O, "docs", "archive", f"{TODAY.isoformat()}.html"), "w", encoding="utf-8").write(page("../", "index.html"))
arch = "".join(f'<li><a href="{E(h["date"])}.html">{E(fdate(h["date"]))}</a>: {LEVELS[h["level"]][0]} {E(h["level"])}</li>' for h in reversed(S["levels"]))
open(os.path.join(O, "docs", "archive", "index.html"), "w", encoding="utf-8").write(f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, nofollow"><meta name="color-scheme" content="light dark">
<title>Past reports · Capital Controls Monitor</title>
<style>:root{{--bg:#f5f6f8;--text:#1a1d21;--link:#0a58b0}}@media (prefers-color-scheme: dark){{:root{{--bg:#0e1115;--text:#e7eaee;--link:#7fb8ff}}}}body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}main{{max-width:760px;margin-inline:auto;padding:24px 16px}}a{{color:var(--link)}}</style></head>
<body><main><h1>Past reports</h1><p><a href="../index.html">Latest report</a></p><ul>{arch}</ul>
<p>Personal research project. Informational only, not financial, legal or tax advice. Not affiliated with any employer.</p></main></body></html>
''')
open(os.path.join(O, "docs", ".nojekyll"), "w").write("")
S["live_page"] = LIVE
json.dump(S, open(os.path.join(O, "data", "state.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
open(os.path.join(O, "README.md"), "w", encoding="utf-8").write(f"""# Capital Controls Early-Warning Monitor

A daily report that checks official U.S. sources, international bodies and the policy community for any movement toward capital controls: limits on Americans' ability to withdraw, move, convert, invest or hold their money. It also tracks ideas still being discussed or developed, and fact-checks popular claims.

**Live report:** {LIVE}

- `docs/index.html`: today's report (published with GitHub Pages)
- `docs/archive/`: daily snapshots
- `data/state.json`: tracked items, levels and source-check history
- `tools/`: the page builder and its stylesheet

Researched and compiled each morning with an AI assistant from public sources. It can contain errors, so check the linked sources.

*Personal research project. Informational only, not financial, legal or tax advice. Not affiliated with any employer.*
""")
print(json.dumps({"items_open": len(items), "by_section": {k: len(v) for k, v in by_sec.items()}, "flags": [f[0]["id"] for f in flags],
                  "dates_near": len(near), "dates_far": len(far), "refreshed": ftime(now), "checked_through": ftime(checked_through)}, indent=1))
