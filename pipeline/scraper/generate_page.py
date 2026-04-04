#!/usr/bin/env python3
"""Generates power-index.html with embedded SC Power Index data."""

import json
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_OUTPUT = REPO_ROOT / "pipeline" / "data" / "output"

# Confederation is now embedded in sc_power_index.json — keep for fallback
CONFED = {}  # read from JSON

FLAGS = {
    # UEFA
    "France": "🇫🇷",    "England": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",  "Spain": "🇪🇸",     "Germany": "🇩🇪",
    "Portugal": "🇵🇹",  "Netherlands": "🇳🇱","Belgium": "🇧🇪",   "Croatia": "🇭🇷",
    "Switzerland": "🇨🇭","Austria": "🇦🇹",  "Scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿","Norway": "🇳🇴",
    "Bosnia and Herzegovina": "🇧🇦", "Sweden": "🇸🇪", "Türkiye": "🇹🇷", "Czechia": "🇨🇿",
    # CONMEBOL
    "Argentina": "🇦🇷", "Brazil": "🇧🇷",    "Uruguay": "🇺🇾",   "Colombia": "🇨🇴",
    "Ecuador": "🇪🇨",   "Paraguay": "🇵🇾",
    # CONCACAF
    "USA": "🇺🇸",       "Mexico": "🇲🇽",    "Canada": "🇨🇦",    "Haiti": "🇭🇹",
    "Panama": "🇵🇦",    "Curaçao": "🇨🇼",
    # AFC
    "Japan": "🇯🇵",     "South Korea": "🇰🇷","Australia": "🇦🇺", "Saudi Arabia": "🇸🇦",
    "Iran": "🇮🇷",      "Iraq": "🇮🇶",       "Uzbekistan": "🇺🇿","Jordan": "🇯🇴",
    "Qatar": "🇶🇦",     "New Zealand": "🇳🇿",
    # CAF
    "Morocco": "🇲🇦",   "Senegal": "🇸🇳",   "South Africa": "🇿🇦","Egypt": "🇪🇬",
    "Algeria": "🇩🇿",   "Ghana": "🇬🇭",     "Ivory Coast": "🇨🇮","Tunisia": "🇹🇳",
    "Cape Verde": "🇨🇻","DR Congo": "🇨🇩",
}


def main():
    sc_data = json.loads((PIPELINE_OUTPUT / "sc_power_index.json").read_text())
    details_dir = PIPELINE_OUTPUT / "team_details"

    enriched = []
    for entry in sc_data:
        team = entry["team"]
        slug = team.lower().replace(" ", "_").replace(",", "").replace("ü", "u").replace("ç", "c")
        detail_file = details_dir / f"{slug}.json"

        players = []
        if detail_file.exists():
            for p in json.loads(detail_file.read_text())[:30]:
                # v2 pipeline uses "pos", v1 used "pos_group" — handle both
                pos = p.get("pos") or p.get("pos_group", "?")
                players.append({
                    "name": p["name"],
                    "pos":  pos,
                    "age":  round(p.get("age", 0), 1),
                    "rtg":  p["rating"],
                    "mv":   int(p.get("market_value_in_eur", 0)),
                    "g":    p.get("goals", 0),
                    "a":    p.get("assists", 0),
                })

        # confederation comes from the JSON in v2; fall back to CONFED dict
        conf = entry.get("confederation") or CONFED.get(team, "Other")
        grp  = entry.get("group", "")

        enriched.append({
            "rank":  entry["rank"],
            "team":  team,
            "flag":  FLAGS.get(team, ""),
            "conf":  conf,
            "grp":   grp,
            "sci":   entry["sc_power_index"],
            "sq":    entry["squad_quality"],
            "xf":    entry["x_factor"],
            "sd":    entry["squad_depth"],
            "rf":    entry["recent_form"],
            "size":  entry["squad_size"],
            "xi":    entry["best_xi"],
            "pl":    players,
        })

    data_js = json.dumps(enriched, ensure_ascii=False, separators=(",", ":"))
    today = date.today().strftime("%B %d, %Y").replace(" 0", " ")

    html = HTML_TEMPLATE.replace("__DATA__", data_js).replace("__UPDATED__", today)
    out = REPO_ROOT / "power-index.html"
    out.write_text(html, encoding="utf-8")
    print(f"Written: {out}  ({len(html)//1024} KB)")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SC Power Index — SquadCheck</title>
<meta name="description" content="Data-driven power rankings for all 48 World Cup 2026 qualified nations. Built from market value, goals, assists, age, and squad depth.">
<meta property="og:title" content="SC Power Index — SquadCheck">
<meta property="og:description" content="All 48 teams ranked. Market value + form + youth + depth. Who's really ready?">/
<meta property="og:type" content="website">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800;900&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
  --lime: #CCFF00;
  --lime-dim: #99BB00;
  --dark: #0A0A0C;
  --dark-2: #111114;
  --dark-3: #1A1A1F;
  --dark-4: #252530;
  --dark-5: #2E2E3A;
  --white: #F0F0F2;
  --gray: #8A8A96;
  --gray-dim: #3A3A46;
}
* { margin:0; padding:0; box-sizing:border-box; }
html { scroll-behavior:smooth; }
body {
  font-family: 'Outfit', sans-serif;
  background: var(--dark); color: var(--white);
  -webkit-font-smoothing: antialiased; overflow-x: hidden;
}

/* ── NAV ───────────────────────────────────────────────── */
nav {
  position:sticky; top:0; z-index:100;
  display:flex; justify-content:space-between; align-items:center;
  padding:14px 40px;
  background:rgba(10,10,12,0.97); backdrop-filter:blur(20px);
  border-bottom:1px solid var(--dark-4);
}
.nav-logo { font-weight:800; font-size:18px; text-decoration:none; color:var(--white); }
.nav-logo span { color:var(--lime); }
.nav-right { display:flex; gap:20px; align-items:center; }
.nav-badge {
  font-size:11px; font-weight:700; padding:4px 12px; border-radius:100px;
  border:1px solid rgba(204,255,0,0.3); color:var(--lime); letter-spacing:1px;
  text-transform:uppercase;
}
.nav-home {
  color:var(--gray); text-decoration:none; font-size:13px; font-weight:500;
  text-transform:uppercase; letter-spacing:.5px; transition:color .2s;
}
.nav-home:hover { color:var(--lime); }

/* ── HERO ──────────────────────────────────────────────── */
.hero {
  padding:72px 40px 52px; border-bottom:1px solid var(--dark-3);
  position:relative; overflow:hidden;
}
.hero::after {
  content:''; position:absolute; top:-300px; right:-300px;
  width:700px; height:700px; border-radius:50%;
  background:radial-gradient(circle, rgba(204,255,0,0.05) 0%, transparent 65%);
  pointer-events:none;
}
.hero-eyebrow {
  font-size:11px; text-transform:uppercase; letter-spacing:3px;
  color:var(--lime); font-weight:600; margin-bottom:16px;
}
.hero h1 {
  font-size:clamp(40px,6vw,72px); font-weight:900;
  letter-spacing:-3px; line-height:1; margin-bottom:16px;
}
.hero h1 em { color:var(--lime); font-style:normal; }
.hero-sub {
  color:var(--gray); max-width:520px; line-height:1.65;
  font-weight:300; font-size:16px; margin-bottom:36px;
}
.hero-stats {
  display:flex; gap:40px; flex-wrap:wrap; margin-bottom:36px;
}
.hstat-val {
  display:block; font-family:'DM Mono',monospace;
  font-size:26px; font-weight:500; color:var(--lime);
}
.hstat-lbl {
  font-size:11px; text-transform:uppercase; letter-spacing:2px;
  color:var(--gray); margin-top:2px;
}
.methodology {
  display:inline-flex; gap:0; border:1px solid var(--dark-4);
  border-radius:12px; overflow:hidden; background:var(--dark-2);
}
.meth-item {
  padding:16px 24px; border-right:1px solid var(--dark-4);
  display:flex; align-items:center; gap:12px;
}
.meth-item:last-child { border-right:none; }
.meth-pct {
  font-family:'DM Mono',monospace; font-size:20px; font-weight:500;
  color:var(--lime); min-width:44px;
}
.meth-name { font-size:13px; font-weight:600; }
.meth-desc { font-size:11px; color:var(--gray); margin-top:2px; }
.updated-tag {
  margin-top:20px; font-size:11px; color:var(--gray); opacity:.5;
  letter-spacing:.5px;
}

/* ── CONTROLS ──────────────────────────────────────────── */
.controls {
  position:sticky; top:57px; z-index:90;
  padding:12px 40px;
  background:rgba(10,10,12,0.97); backdrop-filter:blur(20px);
  border-bottom:1px solid var(--dark-3);
  display:flex; gap:12px; align-items:center; flex-wrap:wrap;
}
.search-wrap { position:relative; flex:1; min-width:180px; max-width:260px; }
.search-wrap svg {
  position:absolute; left:11px; top:50%; transform:translateY(-50%);
  color:var(--gray); pointer-events:none; flex-shrink:0;
}
.search-input {
  width:100%; padding:9px 12px 9px 36px;
  background:var(--dark-3); border:1px solid var(--dark-4);
  border-radius:8px; color:var(--white); font-family:'Outfit'; font-size:14px;
  outline:none; transition:border-color .2s;
}
.search-input:focus { border-color:var(--lime); }
.search-input::placeholder { color:var(--gray); opacity:.45; }
.filters { display:flex; gap:6px; flex-wrap:wrap; }
.fbtn {
  padding:7px 14px; border-radius:100px; border:1px solid var(--dark-4);
  background:transparent; color:var(--gray); font-family:'Outfit';
  font-size:11px; font-weight:700; cursor:pointer; transition:all .2s;
  letter-spacing:.8px; text-transform:uppercase; white-space:nowrap;
}
.fbtn:hover { border-color:var(--gray); color:var(--white); }
.fbtn.on { background:var(--lime); border-color:var(--lime); color:var(--dark); }
.rcount {
  margin-left:auto; font-family:'DM Mono',monospace;
  font-size:12px; color:var(--gray); white-space:nowrap;
}

/* ── TABLE ─────────────────────────────────────────────── */
.table-wrap { overflow-x:auto; }
table { width:100%; border-collapse:collapse; min-width:740px; }
thead tr { border-bottom:1px solid var(--dark-4); }
thead th {
  padding:11px 16px; text-align:left; font-size:10px; text-transform:uppercase;
  letter-spacing:2px; color:var(--gray); font-weight:600;
  cursor:pointer; user-select:none; white-space:nowrap; transition:color .15s;
}
thead th:hover { color:var(--white); }
thead th.sorted { color:var(--lime); }
thead th .si { margin-left:4px; font-size:10px; opacity:.5; }
thead th.sorted .si { opacity:1; }
th.tc { text-align:center; }

tbody tr.tr-team {
  border-bottom:1px solid rgba(255,255,255,0.03);
  cursor:pointer; transition:background .12s;
}
tbody tr.tr-team:hover { background:rgba(255,255,255,0.025); }
tbody tr.tr-team.open { background:var(--dark-2); border-bottom:none; }

td { padding:13px 16px; vertical-align:middle; }
.td-rank {
  text-align:center; font-family:'DM Mono',monospace;
  font-size:13px; color:var(--gray); width:50px;
}
.td-rank.t1  { color:var(--lime); font-weight:600; }
.td-rank.t2  { color:var(--lime-dim); font-weight:500; }

.td-team { display:flex; align-items:center; gap:10px; }
.t-flag { font-size:24px; line-height:1; flex-shrink:0; }
.t-name { font-weight:600; font-size:15px; white-space:nowrap; }
.t-conf {
  font-size:9px; font-weight:700; padding:2px 7px; border-radius:4px;
  letter-spacing:.8px; text-transform:uppercase; margin-top:3px;
  display:inline-block;
}
.c-UEFA    { background:rgba(74,158,255,.15); color:#4A9EFF; }
.c-CONMEBOL{ background:rgba(255,184,74,.15);  color:#FFB84A; }
.c-CONCACAF{ background:rgba(255,107,74,.15);  color:#FF6B4A; }
.c-AFC     { background:rgba(74,255,158,.15);  color:#4AFF9E; }
.c-CAF     { background:rgba(255,154,74,.15);  color:#FF9A4A; }
.c-Other   { background:rgba(150,150,150,.15); color:#aaa; }
.grp-badge {
  font-family:'DM Mono',monospace; font-size:10px; font-weight:700;
  padding:2px 6px; border-radius:4px; letter-spacing:.5px;
  background:rgba(204,255,0,.1); color:var(--lime); margin-left:4px;
}

.sci-wrap { display:flex; align-items:center; gap:10px; min-width:160px; }
.sci-num {
  font-family:'DM Mono',monospace; font-size:16px; font-weight:500;
  min-width:42px;
}
.sci-num.t1  { color:var(--lime); }
.sci-num.t2  { color:var(--lime-dim); }
.sci-num.t3  { color:var(--gray); }
.sci-track {
  flex:1; height:5px; background:var(--dark-4);
  border-radius:3px; overflow:hidden;
}
.sci-fill { height:100%; border-radius:3px; width:0; }
.sci-fill.t1 { background:var(--lime); }
.sci-fill.t2 { background:var(--lime-dim); }
.sci-fill.t3 { background:var(--gray-dim); }

.cv {
  font-family:'DM Mono',monospace; font-size:13px;
  color:var(--gray); text-align:right;
}
.xv { width:32px; text-align:center; color:var(--gray); font-size:18px;
  transition:transform .3s, color .2s; }
tr.open .xv { transform:rotate(180deg); color:var(--lime); }

/* ── DETAIL ROW ────────────────────────────────────────── */
tr.tr-detail td { padding:0; border-bottom:1px solid var(--dark-4); }
.d-inner {
  max-height:0; overflow:hidden;
  transition:max-height .38s cubic-bezier(.4,0,.2,1);
  background:var(--dark-2);
}
tr.tr-detail.open .d-inner { max-height:900px; }
.d-content {
  padding:24px 40px 28px;
  display:grid; grid-template-columns:1fr 1fr 1fr; gap:28px;
}
.d-col h4 {
  font-size:10px; text-transform:uppercase; letter-spacing:2px;
  color:var(--gray); font-weight:600; margin-bottom:14px;
  padding-bottom:8px; border-bottom:1px solid var(--dark-4);
}
/* best xi */
.xi-row {
  display:flex; align-items:center; gap:8px; padding:4px 0;
  font-size:13px; border-bottom:1px solid rgba(255,255,255,.02);
}
.xi-row:last-child { border-bottom:none; }
.xi-pos {
  font-family:'DM Mono',monospace; font-size:9px; color:var(--gray);
  background:var(--dark-4); padding:2px 5px; border-radius:3px;
  width:32px; text-align:center; flex-shrink:0;
}
.xi-name { flex:1; font-weight:500; }
.xi-rtg {
  font-family:'DM Mono',monospace; font-size:13px; font-weight:500;
}
.xi-rtg.e { color:var(--lime); }
.xi-rtg.g { color:var(--white); }
.xi-rtg.o { color:var(--gray); }

/* u23 */
.u23-row { display:flex; align-items:center; gap:8px; padding:5px 0; font-size:13px; }
.u23-age {
  font-family:'DM Mono',monospace; font-size:10px; color:var(--lime);
  background:rgba(204,255,0,.1); padding:2px 7px; border-radius:4px;
  flex-shrink:0;
}
.u23-name { flex:1; font-weight:500; }
.u23-pos { font-size:10px; color:var(--gray); }
.u23-rtg { font-family:'DM Mono',monospace; font-size:13px; color:var(--lime); }

/* breakdown */
.bk-row { margin-bottom:12px; }
.bk-hd { display:flex; justify-content:space-between; margin-bottom:4px; }
.bk-lbl { font-size:12px; color:var(--gray); }
.bk-val { font-family:'DM Mono',monospace; font-size:13px; font-weight:500; }
.bk-track { height:3px; background:var(--dark-4); border-radius:2px; overflow:hidden; }
.bk-fill  { height:100%; background:var(--lime); border-radius:2px; }
.bk-divider {
  border-top:1px solid var(--dark-4); margin:14px 0 10px;
}
.bk-sq-row { display:flex; justify-content:space-between; }
.bk-sq-lbl { font-size:12px; color:var(--gray); }
.bk-sq-val { font-family:'DM Mono',monospace; font-size:13px; color:var(--gray); }

/* no results */
.no-res {
  padding:64px 40px; text-align:center; color:var(--gray); font-size:15px;
  display:none;
}

/* ── FOOTER ────────────────────────────────────────────── */
footer {
  padding:32px 40px; text-align:center;
  border-top:1px solid var(--dark-3); margin-top:40px;
}
footer a { color:var(--gray); text-decoration:none; font-size:13px; transition:color .2s; }
footer a:hover { color:var(--lime); }
.f-note { font-size:11px; color:var(--gray); opacity:.35; margin-top:10px; line-height:1.6; }

/* ── RESPONSIVE ────────────────────────────────────────── */
@media (max-width: 900px) {
  .methodology { flex-direction:column; }
  .meth-item { border-right:none; border-bottom:1px solid var(--dark-4); }
  .meth-item:last-child { border-bottom:none; }
  .d-content { grid-template-columns:1fr; gap:20px; }
}
@media (max-width: 640px) {
  nav { padding:12px 16px; }
  .hero { padding:48px 16px 36px; }
  .controls { padding:10px 16px; top:53px; }
  .d-content { padding:16px; }
  .hero-stats { gap:24px; }
  .rcount { display:none; }
  .nav-badge { display:none; }
}
</style>
</head>
<body>

<nav>
  <a href="index.html" class="nav-logo">Squad<span>Check</span></a>
  <div class="nav-right">
    <span class="nav-badge">SC Power Index</span>
    <a href="index.html" class="nav-home">← Home</a>
  </div>
</nav>

<section class="hero">
  <div class="hero-eyebrow">World Cup 2026 · Data Intelligence</div>
  <h1>SC Power <em>Index</em></h1>
  <p class="hero-sub">Data-driven rankings for every qualified nation. Built from market value, current form, youth talent, and squad depth.</p>

  <div class="hero-stats">
    <div><span class="hstat-val">48</span><span class="hstat-lbl">Nations Ranked</span></div>
    <div><span class="hstat-val">1,248</span><span class="hstat-lbl">Squad Players</span></div>
    <div><span class="hstat-val">933</span><span class="hstat-lbl">Scorers Cross-Referenced</span></div>
    <div><span class="hstat-val">12</span><span class="hstat-lbl">Groups</span></div>
  </div>

  <div class="methodology">
    <div class="meth-item">
      <span class="meth-pct">50%</span>
      <div><div class="meth-name">Squad Quality</div><div class="meth-desc">Best XI average rating</div></div>
    </div>
    <div class="meth-item">
      <span class="meth-pct">20%</span>
      <div><div class="meth-name">Squad Depth</div><div class="meth-desc">Bench quality &amp; positional cover</div></div>
    </div>
    <div class="meth-item">
      <span class="meth-pct">15%</span>
      <div><div class="meth-name">X-Factor</div><div class="meth-desc">U23 talent density</div></div>
    </div>
    <div class="meth-item">
      <span class="meth-pct">15%</span>
      <div><div class="meth-name">Recent Form</div><div class="meth-desc">Live results (v2 — placeholder)</div></div>
    </div>
  </div>
  <div class="updated-tag">Last updated: __UPDATED__</div>
</section>

<div class="controls" id="controls">
  <div class="search-wrap">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
    <input class="search-input" id="search" placeholder="Search nation…" oninput="onSearch(this.value)" autocomplete="off">
  </div>
  <div class="filters" id="fbar">
    <button class="fbtn on"  onclick="onFilter(this,'All')">All</button>
    <button class="fbtn"     onclick="onFilter(this,'UEFA')">UEFA</button>
    <button class="fbtn"     onclick="onFilter(this,'CONMEBOL')">CONMEBOL</button>
    <button class="fbtn"     onclick="onFilter(this,'CONCACAF')">CONCACAF</button>
    <button class="fbtn"     onclick="onFilter(this,'AFC')">AFC</button>
    <button class="fbtn"     onclick="onFilter(this,'CAF')">CAF</button>
  </div>
  <span class="rcount" id="rcount">44 nations</span>
</div>

<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th class="tc sorted" data-col="rank" onclick="onSort(this)"># <span class="si">↑</span></th>
        <th data-col="team" onclick="onSort(this)">Nation <span class="si">↕</span></th>
        <th data-col="sci"  onclick="onSort(this)">SCI Score <span class="si">↕</span></th>
        <th data-col="sq"   onclick="onSort(this)" style="text-align:right">Sq. Quality <span class="si">↕</span></th>
        <th data-col="xf"   onclick="onSort(this)" style="text-align:right">X-Factor <span class="si">↕</span></th>
        <th data-col="sd"   onclick="onSort(this)" style="text-align:right">Depth <span class="si">↕</span></th>
        <th data-col="size" onclick="onSort(this)" style="text-align:right">Squad <span class="si">↕</span></th>
        <th style="width:36px"></th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
</div>
<div class="no-res" id="nores">No nations match your search.</div>

<footer>
  <a href="index.html">← Back to SquadCheck</a>
  <div class="f-note">
    Market values from Transfermarkt · Goals &amp; assists from football-data.org (PL, BL1, SA, FL1, PD, PPL, DED, BSA, CL, ELC)<br>
    Ratings are algorithmic — not official FIFA rankings. Recent Form score is a placeholder (v2 will use live match data).
  </div>
</footer>

<script>
const D = __DATA__;

const S = { col:'rank', asc:true, conf:'All', q:'', open:new Set() };

const tier = r => r <= 10 ? 't1' : r <= 20 ? 't2' : 't3';
const rc   = v => v >= 85 ? 'e' : v >= 65 ? 'g' : 'o';
const esc  = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const ea   = s => String(s).replace(/\\/g,'\\\\').replace(/'/g,"\\'");
const fmt1 = v => v.toFixed(1);
const fmtK = v => v >= 1e6 ? (v/1e6).toFixed(0)+'M' : v >= 1e3 ? (v/1e3).toFixed(0)+'K' : String(v);

function filtered() {
  return D
    .filter(d => S.conf === 'All' || d.conf === S.conf)
    .filter(d => d.team.toLowerCase().includes(S.q))
    .sort((a,b) => {
      const av = a[S.col], bv = b[S.col];
      const c = typeof av === 'string' ? av.localeCompare(bv) : av - bv;
      return S.asc ? c : -c;
    });
}

function xiList(d) {
  const byName = {};
  for (const p of d.pl) byName[p.name] = p;
  return d.xi.map(n => {
    const p = byName[n] || { pos:'?', rtg:0 };
    return `<div class="xi-row">
      <span class="xi-pos">${esc(p.pos)}</span>
      <span class="xi-name">${esc(n)}</span>
      <span class="xi-rtg ${rc(p.rtg)}">${fmt1(p.rtg)}</span>
    </div>`;
  }).join('');
}

function u23List(d) {
  const stars = d.pl.filter(p => p.age > 0 && p.age < 23 && p.rtg > 50)
    .sort((a,b) => b.rtg - a.rtg).slice(0,7);
  if (!stars.length) return '<div style="color:var(--gray);font-size:13px;padding:4px 0">No U23 players with rating > 50</div>';
  return stars.map(p => `<div class="u23-row">
    <span class="u23-age">${Math.floor(p.age)}y</span>
    <span class="u23-name">${esc(p.name)}</span>
    <span class="u23-pos">${esc(p.pos)}</span>
    <span class="u23-rtg">${fmt1(p.rtg)}</span>
  </div>`).join('');
}

function breakdown(d) {
  const bars = [
    { lbl:'Squad Quality',  pct:d.sq, val:fmt1(d.sq), note:'50%' },
    { lbl:'X-Factor',       pct:d.xf, val:fmt1(d.xf), note:'15%' },
    { lbl:'Squad Depth',    pct:d.sd, val:fmt1(d.sd), note:'20%' },
  ];
  const barsHtml = bars.map(b => `<div class="bk-row">
    <div class="bk-hd">
      <span class="bk-lbl">${b.lbl} <span style="opacity:.4;font-size:10px">${b.note}</span></span>
      <span class="bk-val">${b.val}</span>
    </div>
    <div class="bk-track"><div class="bk-fill" style="width:${b.pct.toFixed(1)}%"></div></div>
  </div>`).join('');
  return `${barsHtml}
  <div class="bk-row">
    <div class="bk-hd">
      <span class="bk-lbl">Recent Form <span style="opacity:.4;font-size:10px">15% · v2</span></span>
      <span class="bk-val" style="color:var(--gray)">${fmt1(d.rf)}</span>
    </div>
  </div>
  <div class="bk-divider"></div>
  <div class="bk-sq-row"><span class="bk-sq-lbl">Players in database</span><span class="bk-sq-val">${d.size.toLocaleString()}</span></div>`;
}

function row(d) {
  const t = tier(d.rank);
  const isOpen = S.open.has(d.team);
  const barW = Math.min(100, (d.sci / 90) * 100).toFixed(1);

  return `
<tr class="tr-team${isOpen?' open':''}" onclick="toggleRow('${ea(d.team)}')">
  <td class="td-rank ${t}">${d.rank}</td>
  <td><div class="td-team">
    <span class="t-flag">${d.flag}</span>
    <div><div class="t-name">${esc(d.team)}</div>
    <span class="t-conf c-${d.conf}">${d.conf}</span>${d.grp ? `<span class="grp-badge">Gr.${d.grp}</span>` : ''}</div>
  </div></td>
  <td><div class="sci-wrap">
    <span class="sci-num ${t}">${fmt1(d.sci)}</span>
    <div class="sci-track"><div class="sci-fill ${t}" data-w="${barW}"></div></div>
  </div></td>
  <td class="cv">${fmt1(d.sq)}</td>
  <td class="cv">${fmt1(d.xf)}</td>
  <td class="cv">${fmt1(d.sd)}</td>
  <td class="cv">${d.size.toLocaleString()}</td>
  <td class="xv">⌄</td>
</tr>
<tr class="tr-detail${isOpen?' open':''}">
  <td colspan="8"><div class="d-inner">
    <div class="d-content">
      <div class="d-col"><h4>Best XI</h4>${xiList(d)}</div>
      <div class="d-col"><h4>U23 Rising Stars</h4>${u23List(d)}</div>
      <div class="d-col"><h4>Index Breakdown</h4>${breakdown(d)}</div>
    </div>
  </div></td>
</tr>`;
}

function render() {
  const data = filtered();
  const tbody = document.getElementById('tbody');
  const nores = document.getElementById('nores');
  if (!data.length) {
    tbody.innerHTML = '';
    nores.style.display = 'block';
  } else {
    nores.style.display = 'none';
    tbody.innerHTML = data.map(row).join('');
    // animate bars on next frame
    requestAnimationFrame(() => {
      document.querySelectorAll('.sci-fill[data-w]').forEach(el => {
        el.style.transition = 'width 0.7s ease';
        el.style.width = el.dataset.w + '%';
      });
    });
  }
  const n = data.length;
  document.getElementById('rcount').textContent =
    n === D.length ? `all ${D.length} nations` : `${n} of ${D.length} nations`;
}

function onSort(th) {
  const col = th.dataset.col;
  if (S.col === col) {
    S.asc = !S.asc;
  } else {
    S.col = col;
    S.asc = (col === 'team' || col === 'rank');
  }
  document.querySelectorAll('thead th').forEach(t => {
    t.classList.remove('sorted');
    const si = t.querySelector('.si');
    if (si) si.textContent = '↕';
  });
  th.classList.add('sorted');
  const si = th.querySelector('.si');
  if (si) si.textContent = S.asc ? '↑' : '↓';
  render();
}

function onFilter(btn, conf) {
  S.conf = conf;
  document.querySelectorAll('.fbtn').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  render();
}

let _st;
function onSearch(v) {
  clearTimeout(_st);
  _st = setTimeout(() => { S.q = v.toLowerCase().trim(); render(); }, 120);
}

function toggleRow(team) {
  S.open.has(team) ? S.open.delete(team) : S.open.add(team);
  render();
}

render();
</script>
</body>
</html>"""

if __name__ == "__main__":
    main()
