#!/usr/bin/env python3
"""
Reads pipeline/publish/template.html as the base design, injects the
rankings table + controls into <!-- RANKINGS_TABLE -->, and writes
the final page to /SquadCheck/power-index.html.
"""
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

ROOT      = Path(__file__).resolve().parent.parent
REPO_ROOT = ROOT.parent
TEMPLATE  = Path(__file__).resolve().parent / "template.html"
INPUT_FILE  = ROOT / "data" / "output" / "sc_power_index.json"
OUTPUT_FILE = REPO_ROOT / "power-index.html"

TODAY = date.today().strftime("%B %-d, %Y")

ALL_GROUPS = list("ABCDEFGHIJKL")
ALL_CONFS  = ["UEFA", "CONMEBOL", "CONCACAF", "AFC", "CAF"]

FLAGS = {
    "France":"🇫🇷",     "England":"🏴󠁧󠁢󠁥󠁮󠁧󠁿",   "Spain":"🇪🇸",      "Germany":"🇩🇪",
    "Portugal":"🇵🇹",   "Netherlands":"🇳🇱","Belgium":"🇧🇪",    "Croatia":"🇭🇷",
    "Switzerland":"🇨🇭","Austria":"🇦🇹",   "Scotland":"🏴󠁧󠁢󠁳󠁣󠁴󠁿","Norway":"🇳🇴",
    "Bosnia and Herzegovina":"🇧🇦","Sweden":"🇸🇪","Türkiye":"🇹🇷","Czechia":"🇨🇿",
    "Argentina":"🇦🇷",  "Brazil":"🇧🇷",     "Uruguay":"🇺🇾",    "Colombia":"🇨🇴",
    "Ecuador":"🇪🇨",    "Paraguay":"🇵🇾",
    "United States":"🇺🇸","Mexico":"🇲🇽",   "Canada":"🇨🇦",     "Haiti":"🇭🇹",
    "Panama":"🇵🇦",     "Curaçao":"🇨🇼",
    "Japan":"🇯🇵",      "South Korea":"🇰🇷","Australia":"🇦🇺",  "Saudi Arabia":"🇸🇦",
    "Iran":"🇮🇷",       "Iraq":"🇮🇶",        "Uzbekistan":"🇺🇿", "Jordan":"🇯🇴",
    "Qatar":"🇶🇦",      "New Zealand":"🇳🇿",
    "Morocco":"🇲🇦",    "Senegal":"🇸🇳",    "South Africa":"🇿🇦","Egypt":"🇪🇬",
    "Algeria":"🇩🇿",    "Ghana":"🇬🇭",      "Ivory Coast":"🇨🇮","Tunisia":"🇹🇳",
    "Cape Verde":"🇨🇻", "DR Congo":"🇨🇩",
}


# ── Helpers ────────────────────────────────────────────────────────────────

def fmt_value(v: int) -> str:
    if v >= 1_000_000:
        m = v / 1_000_000
        return f"€{int(m)}M" if m == int(m) else f"€{m:.1f}M"
    if v >= 1_000:
        k = v / 1_000
        return f"€{int(k)}K" if k == int(k) else f"€{k:.0f}K"
    return f"€{v}"


def escape_html(s: str) -> str:
    return (str(s)
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def sci_tier(rank: int) -> str:
    if rank <= 8:  return "t1"
    if rank <= 16: return "t2"
    return "t3"


def bar_width(val: float, max_val: float = 100.0) -> int:
    return max(1, min(100, int(val / max_val * 100)))


# ── Row builders ───────────────────────────────────────────────────────────

def build_best_xi_rows(best_xi: List[Dict[str, Any]]) -> str:
    pos_order = ["Goalkeeper", "Defender", "Midfielder", "Forward"]
    xi = sorted(best_xi,
                key=lambda p: pos_order.index(p.get("position", "Forward"))
                if p.get("position") in pos_order else 99)
    rows = []
    for p in xi:
        pos_short = {"Goalkeeper":"GK","Defender":"DF","Midfielder":"MF","Forward":"FW"}.get(
            p.get("position",""), "??")
        name = escape_html(p.get("name",""))
        rtg  = float(p.get("rating", 0) or 0)
        cls  = "e" if rtg >= 75 else ("g" if rtg >= 55 else "o")
        rows.append(
            f'<div class="xi-row">'
            f'<span class="xi-pos">{pos_short}</span>'
            f'<span class="xi-name">{name}</span>'
            f'<span class="xi-rtg {cls}">{rtg:.0f}</span>'
            f'</div>'
        )
    return "\n".join(rows)


def build_u23_rows(u23_stars: List[Dict[str, Any]]) -> str:
    if not u23_stars:
        return '<div style="color:var(--gray);font-size:13px;padding:8px 0">No U23 players tracked</div>'
    rows = []
    for p in u23_stars[:6]:
        age = p.get("age")
        age_str = f"{age:.0f}" if age else "—"
        pos  = escape_html(p.get("position", p.get("pos", "")))
        name = escape_html(p.get("name",""))
        rtg  = float(p.get("rating", 0) or 0)
        rows.append(
            f'<div class="u23-row">'
            f'<span class="u23-age">{age_str}</span>'
            f'<span class="u23-name">{name}</span>'
            f'<span class="u23-pos">{pos}</span>'
            f'<span class="u23-rtg">{rtg:.0f}</span>'
            f'</div>'
        )
    return "\n".join(rows)


def build_breakdown_col(entry: Dict[str, Any]) -> str:
    sq  = float(entry.get("squad_quality", 0) or 0)
    xf  = float(entry.get("x_factor",     0) or 0)
    sd  = float(entry.get("squad_depth",  0) or 0)
    rf  = float(entry.get("recent_form",  50) or 50)
    sci = float(entry.get("sc_power_index", 0) or 0)
    sz  = entry.get("squad_size", 0) or 0

    def row(label: str, val: float, pct: float) -> str:
        return (
            f'<div class="bk-row">'
            f'<div class="bk-hd"><span class="bk-lbl">{label}</span>'
            f'<span class="bk-val">{val:.1f}</span></div>'
            f'<div class="bk-track"><div class="bk-fill" style="width:{bar_width(pct)}%"></div></div>'
            f'</div>'
        )

    return (
        row("Squad Quality (50%)", sq, sq)
        + row("X-Factor (15%)", xf, xf)
        + row("Squad Depth (20%)", sd, sd)
        + row("Recent Form (15%)", rf, rf)
        + '<div class="bk-divider"></div>'
        + f'<div class="bk-sq-row"><span class="bk-sq-lbl">SC Power Index</span>'
          f'<span class="bk-sq-val">{sci:.2f}</span></div>'
        + f'<div class="bk-sq-row" style="margin-top:6px"><span class="bk-sq-lbl">Squad size</span>'
          f'<span class="bk-sq-val">{sz} players</span></div>'
    )


def build_table_rows(entries: List[Dict[str, Any]]) -> str:
    rows = []
    for entry in entries:
        rank = entry.get("rank", 0)
        team = entry.get("team", "")
        conf = entry.get("confederation", "Other")
        group = entry.get("group", "?")
        flag  = FLAGS.get(team, "🏳️")
        sci   = float(entry.get("sci",           0) or 0)
        sq    = float(entry.get("squad_quality", 0) or 0)
        xf    = float(entry.get("xfactor",       0) or 0)
        sd    = float(entry.get("squad_depth",   0) or 0)
        size  = entry.get("squad_size", 0) or 0

        tier     = sci_tier(rank)
        rank_cls = "td-rank t1" if rank <= 3 else ("td-rank t2" if rank <= 8 else "td-rank")

        team_e  = escape_html(team)
        conf_e  = escape_html(conf)
        group_e = escape_html(group)

        best_xi_data = entry.get("best_xi", [])
        u23_stars    = entry.get("u23_stars", [])
        best_xi_html  = build_best_xi_rows(best_xi_data)
        u23_html      = build_u23_rows(u23_stars)
        breakdown_html = build_breakdown_col(entry)

        data_attrs = (
            f'data-team="{team_e.lower()}" data-conf="{conf_e}" data-group="{group_e}" '
            f'data-sci="{sci:.4f}" data-rank="{rank}" data-sq="{sq:.4f}" '
            f'data-xf="{xf:.4f}" data-sd="{sd:.4f}" data-size="{size}"'
        )

        team_row = f"""<tr class="tr-team" id="row-{rank}" {data_attrs} onclick="toggle('{rank}')">
  <td class="{rank_cls}">{rank}</td>
  <td>
    <div class="td-team">
      <span class="t-flag">{flag}</span>
      <div>
        <div class="t-name">{team_e}</div>
        <span class="t-conf c-{conf_e}">{conf_e}</span>
        <span class="grp-badge">{group_e}</span>
      </div>
    </div>
  </td>
  <td>
    <div class="sci-wrap">
      <span class="sci-num {tier}">{sci:.2f}</span>
      <div class="sci-track"><div class="sci-fill {tier}" style="width:{bar_width(sci)}%"></div></div>
    </div>
  </td>
  <td class="cv" style="text-align:right">{sq:.1f}</td>
  <td class="cv" style="text-align:right">{xf:.1f}</td>
  <td class="cv" style="text-align:right">{sd:.1f}</td>
  <td class="cv" style="text-align:right">{size}</td>
  <td class="xv">&#9660;</td>
</tr>"""

        detail_row = f"""<tr class="tr-detail" id="det-{rank}">
  <td colspan="8">
    <div class="d-inner">
      <div class="d-content">
        <div class="d-col"><h4>Best XI</h4>{best_xi_html}</div>
        <div class="d-col"><h4>U23 Stars</h4>{u23_html}</div>
        <div class="d-col"><h4>Breakdown</h4>{breakdown_html}</div>
      </div>
    </div>
  </td>
</tr>"""

        rows.append(team_row)
        rows.append(detail_row)

    return "\n".join(rows)


def build_controls(n_teams: int) -> str:
    conf_btns = '<button class="fbtn on" onclick="onFilter(this,\'all\',\'\')">All</button>\n'
    conf_btns += "\n".join(
        f'<button class="fbtn" onclick="onFilter(this,\'conf\',\'{c}\')">{c}</button>'
        for c in ALL_CONFS
    )
    group_btns = "\n".join(
        f'<button class="fbtn" onclick="onFilter(this,\'grp\',\'{g}\')">Group {g}</button>'
        for g in ALL_GROUPS
    )
    return f"""<div class="controls" id="controls">
  <div class="search-wrap">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
    <input class="search-input" id="search" placeholder="Search nation…" oninput="onSearch(this.value)" autocomplete="off">
  </div>
  <div class="filters" id="fbar-conf">
    {conf_btns}
  </div>
  <div class="filter-sep"></div>
  <div class="filters" id="fbar-group">
    {group_btns}
  </div>
  <span class="rcount" id="rcount">{n_teams} nations</span>
</div>

<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th class="tc sorted" data-col="rank" onclick="onSort(this)"># <span class="si">&#8593;</span></th>
        <th data-col="team" onclick="onSort(this)">Nation <span class="si">&#8597;</span></th>
        <th data-col="sci"  onclick="onSort(this)">SCI Score <span class="si">&#8597;</span></th>
        <th data-col="sq"   onclick="onSort(this)" style="text-align:right">Sq. Quality <span class="si">&#8597;</span></th>
        <th data-col="xf"   onclick="onSort(this)" style="text-align:right">X-Factor <span class="si">&#8597;</span></th>
        <th data-col="sd"   onclick="onSort(this)" style="text-align:right">Depth <span class="si">&#8597;</span></th>
        <th data-col="size" onclick="onSort(this)" style="text-align:right">Squad <span class="si">&#8597;</span></th>
        <th style="width:36px"></th>
      </tr>
    </thead>
    <tbody id="tbody">
<!-- TABLE_ROWS -->
    </tbody>
  </table>
</div>
<div class="no-res" id="nores">No nations match your search.</div>"""


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    print("[generate_html] Loading power index data...")
    if not INPUT_FILE.exists():
        print(f"[generate_html] Input not found: {INPUT_FILE}. Run calculate_index first.")
        return

    entries: List[Dict[str, Any]] = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    print(f"[generate_html] Loaded {len(entries)} teams")

    if not TEMPLATE.exists():
        print(f"[generate_html] Template not found: {TEMPLATE}")
        return

    template = TEMPLATE.read_text(encoding="utf-8")

    # Build the controls + table HTML
    table_rows = build_table_rows(entries)
    controls_and_table = build_controls(len(entries)).replace("<!-- TABLE_ROWS -->", table_rows)

    # Inject into template
    html = template.replace("<!-- RANKINGS_TABLE -->", controls_and_table)
    html = html.replace("<!-- UPDATED_DATE -->", TODAY)

    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"[generate_html] Written {len(html):,} bytes → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
