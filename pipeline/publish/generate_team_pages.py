#!/usr/bin/env python3
"""
Generates one HTML page per team in ~/SquadCheck/teams/{slug}.html.
Reads: sc_power_index.json, rated_players.json, squads.json, groups.json
"""
import json
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT       = Path(__file__).resolve().parent.parent
REPO_ROOT  = ROOT.parent
OUTPUT_DIR = REPO_ROOT / "teams"

IDX_FILE     = ROOT / "data" / "output" / "sc_power_index.json"
PLAYERS_FILE = ROOT / "data" / "cache" / "rated_players.json"
SQUADS_FILE  = ROOT / "data" / "input" / "squads.json"
GROUPS_FILE  = ROOT / "data" / "input" / "groups.json"

POS_ORDER = ["Goalkeeper", "Defender", "Midfielder", "Forward"]
POS_SHORT = {"Goalkeeper": "GK", "Defender": "DF", "Midfielder": "MF", "Forward": "FW"}

FLAGS: Dict[str, str] = {
    "France":"🇫🇷",     "England":"🏴󠁧󠁢󠁥󠁮󠁧󠁿",   "Spain":"🇪🇸",      "Germany":"🇩🇪",
    "Portugal":"🇵🇹",   "Netherlands":"🇳🇱", "Belgium":"🇧🇪",    "Croatia":"🇭🇷",
    "Switzerland":"🇨🇭","Austria":"🇦🇹",    "Scotland":"🏴󠁧󠁢󠁳󠁣󠁴󠁿","Norway":"🇳🇴",
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

INSIGHTS: Dict[str, str] = {
    "Spain":    "Spain combine the tournament's highest-rated U23 player (Lamine Yamal, 88) with elite squad depth. Their X-Factor score leads the entire index — if Yamal stays fit, they are the team to beat.",
    "France":   "France carry the world's top-rated player in Kylian Mbappé (92) and the deepest squad in Europe. Their only vulnerability is a thin U23 pool — just Zaïre-Emery qualifies — which caps their X-Factor.",
    "England":  "England's strength is balance: top-10 squad quality, top-5 depth, and a genuine X-Factor in Bellingham and Mainoo. Consistent underperformers at tournaments, they arrive in 2026 with no excuses.",
    "Portugal": "Ronaldo at 41 remains in the squad but the real story is the engine room: Bruno Fernandes, Bernardo Silva, and João Neves form a midfield that would start for almost any team at this World Cup.",
    "Brazil":   "Brazil have the second-deepest attacking options at the tournament, led by Vinícius Jr. and Rodrygo. The question is midfield control — their defensive structure will determine how far they go.",
    "Germany":  "Post-rebuild Germany are dangerous. Musiala and Wirtz in the same team gives them the most technically gifted midfield-attack transition in the tournament. Home continent advantage matters.",
    "Argentina":"The defending champions rank #7, not #1. Their squad quality remains elite — but with an ageing core and the lowest bench depth of any top-10 side, they need Messi to deliver one more time.",
    "Belgium":  "Belgium's golden generation has one final roll of the dice. De Bruyne is the fulcrum of everything. Without him at his best, their scoring routes narrow dramatically.",
    "Türkiye":  "Turkey are the index's biggest X-Factor story. Arda Güler (22) and Kenan Yıldız (21) give them two top-80 rated U23 players — a combination only Spain and England can match.",
    "Morocco":  "Morocco's ranking undervalues their tournament pedigree. Achraf Hakimi scores 79 but their collective pressing system and tactical discipline go beyond what individual market values capture.",
}
DEFAULT_INSIGHT = "Full team analysis coming soon. Check back as we add editorial insights for every team before the June 11 kickoff."


# ── Helpers ────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_name.lower().replace(" ", "-").replace("'", "").replace(",", "").replace(".", "")


def esc(s: Any) -> str:
    return (str(s)
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def fmt_mv(v: Optional[float]) -> str:
    if not v:
        return "—"
    if v >= 1_000_000:
        m = v / 1_000_000
        return f"€{int(m)}M" if m == int(m) else f"€{m:.1f}M"
    if v >= 1_000:
        k = v / 1_000
        return f"€{int(k)}K" if k == int(k) else f"€{k:.0f}K"
    return f"€{int(v)}"


def bar_w(val: float, max_val: float = 100.0) -> int:
    return max(1, min(100, int(val / max_val * 100)))


def squad_rtg_cls(r: float) -> str:
    if r >= 80: return "rtg-elite"
    if r >= 60: return "rtg-good"
    return "rtg-dim"


def xi_rtg_cls(r: float) -> str:
    if r >= 75: return "xi-rtg-e"
    if r >= 55: return "xi-rtg-g"
    return "xi-rtg-o"


def conf_cls(conf: str) -> str:
    return {"UEFA":"c-uefa","CONMEBOL":"c-csb","CONCACAF":"c-ccf","AFC":"c-afc","CAF":"c-caf"}.get(conf,"c-oth")


def hero_summary(team: str, best_xi: List[Dict], entry: Dict) -> str:
    if best_xi:
        top_p = max(best_xi, key=lambda p: float(p.get("rating", 0) or 0))
        top_name = top_p.get("name", "")
        top_rtg = float(top_p.get("rating", 0) or 0)
    else:
        return f"{team} are one of 48 nations competing at World Cup 2026."
    xf = float(entry.get("xfactor", 0) or 0)
    sd = float(entry.get("squad_depth", 0) or 0)
    sq = float(entry.get("squad_quality", 0) or 0)
    if xf >= 60:
        trait = "exceptional U23 talent"
    elif sd >= 70:
        trait = "strong bench depth"
    elif sq >= 70:
        trait = "elite squad quality across all positions"
    elif xf <= 25:
        trait = "a battle-tested but ageing core"
    elif sd <= 40:
        trait = "limited options beyond the first eleven"
    else:
        trait = "a balanced profile across all four metrics"
    return f"{team}'s squad is built around {top_name} ({top_rtg:.0f}) with {trait}."


# ── Score Breakdown (4 cards, 2×2 grid) ────────────────────────────────────

def build_breakdown_cards(entry: Dict, leader: Dict) -> str:
    sq  = float(entry.get("squad_quality", 0) or 0)
    xf  = float(entry.get("xfactor",      0) or 0)
    sd  = float(entry.get("squad_depth",  0) or 0)
    rf  = float(entry.get("recent_form",  50) or 50)
    u23_stars = entry.get("u23_stars", [])

    l_sq  = float(leader.get("squad_quality", 0) or 0)
    l_xf  = float(leader.get("xfactor",      0) or 0)
    l_sd  = float(leader.get("squad_depth",  0) or 0)
    l_rf  = float(leader.get("recent_form",  50) or 50)
    l_name = esc(leader.get("team", "#1"))

    def card(title: str, weight: str, val: float, leader_val: float, note: str, extra: str = "") -> str:
        w = bar_w(val)
        cmp_cls = "cmp-up" if val >= leader_val else "cmp-dn"
        cmp_txt = f"vs {l_name}'s {leader_val:.1f}"
        return f"""<div class="bk-card">
  <div class="bk-card-head">
    <span class="bk-card-title">{title}</span>
    <span class="bk-card-weight">{weight}</span>
  </div>
  <div class="bk-card-score">{val:.1f}</div>
  <div class="bk-bar-track"><div class="bk-bar-fill" style="width:{w}%"></div></div>
  <div class="bk-card-note">{note}</div>
  <div class="bk-card-cmp {cmp_cls}">{cmp_txt}</div>
  {extra}
</div>"""

    u23_extra = ""
    if u23_stars:
        chips = "".join(
            f'<span class="u23-chip">{esc(p.get("name",""))} <em>{float(p.get("rating",0)):.0f}</em></span>'
            for p in u23_stars[:4]
        )
        u23_extra = f'<div class="u23-chips">{chips}</div>'

    return f"""<div class="bk-grid">
  {card("Squad Quality", "50%", sq, l_sq, "Best XI average rating")}
  {card("Squad Depth", "20%", sd, l_sd, "Positions 12–26 average")}
  {card("X-Factor", "15%", xf, l_xf, "U23 talent density", u23_extra)}
  {card("Recent Form", "15%", rf, l_rf, "Live updates from June 11")}
</div>"""


# ── Group Rivals ───────────────────────────────────────────────────────────

def _matchup_line(team_name: str, rival_name: str,
                  te: Dict, re: Dict) -> str:
    t_sci = float(te.get("sci", 0) or 0)
    r_sci = float(re.get("sci", 0) or 0)
    favoured   = team_name if t_sci >= r_sci else rival_name
    underdog   = rival_name if t_sci >= r_sci else team_name
    und_entry  = re if t_sci >= r_sci else te
    fav_entry  = te if t_sci >= r_sci else re
    sci_diff   = abs(t_sci - r_sci)

    metrics = [
        ("squad quality", "squad_quality"),
        ("squad depth", "squad_depth"),
        ("X-Factor", "xfactor"),
        ("recent form", "recent_form"),
    ]
    upsets = []
    for label, key in metrics:
        u_val = float(und_entry.get(key, 0) or 0)
        f_val = float(fav_entry.get(key, 0) or 0)
        if u_val > f_val:
            upsets.append(f"{underdog} leads in {label} ({u_val:.1f} vs {f_val:.1f})")

    base = f"<strong>{esc(favoured)}</strong> favoured by +{sci_diff:.1f} SCI"
    if upsets:
        base += f", but {upsets[0]}"
    elif sci_diff < 3:
        base += " — too close to call"
    return base


def build_group_section(team_name: str, group: str,
                        groups_data: Dict, idx_by_team: Dict) -> str:
    group_info = groups_data.get(group, {})
    all_teams  = group_info.get("teams", [])
    if not all_teams:
        return ""

    team_entry = idx_by_team.get(team_name, {})

    # ── Group cards (all 4 teams) ──────────────────────────────────────────
    cards_html = ""
    for t in all_teams:
        e     = idx_by_team.get(t, {})
        flag  = FLAGS.get(t, "🏳️")
        rank  = e.get("rank", "?")
        sci   = float(e.get("sci", 0) or 0)
        slug  = slugify(t)
        is_current = "gc-current" if t == team_name else ""
        xi    = e.get("best_xi", [])
        key_p = ""
        if xi:
            top = max(xi, key=lambda p: float(p.get("rating", 0) or 0))
            key_p = f'<div class="gc-player">{esc(top.get("name",""))} <span class="gc-pr">{float(top.get("rating",0)):.0f}</span></div>'
        sci_w = bar_w(sci)
        cards_html += f"""<a href="/teams/{slug}.html" class="group-card {is_current}">
  <div class="gc-top">
    <span class="gc-flag">{flag}</span>
    <div>
      <div class="gc-name">{esc(t)}</div>
      <div class="gc-rank">#{rank}</div>
    </div>
    <div class="gc-sci">{sci:.1f}</div>
  </div>
  <div class="gc-bar-track"><div class="gc-bar-fill" style="width:{sci_w}%"></div></div>
  {key_p}
</a>"""

    # ── Matchup comparison lines (current team vs each rival) ─────────────
    rivals = [t for t in all_teams if t != team_name]
    matchups_html = ""
    for rival in rivals:
        rival_entry = idx_by_team.get(rival, {})
        line = _matchup_line(team_name, rival, team_entry, rival_entry)
        matchups_html += f'<div class="mu-row"><span class="mu-label">{esc(team_name)} vs {esc(rival)}</span><span class="mu-line">{line}</span></div>'

    venues = group_info.get("venues", [])
    venue_tag = f'<div class="group-venues">Group venues: {esc(" · ".join(venues[:3]))}</div>' if venues else ""

    return f"""<div class="group-cards-row">{cards_html}</div>
{venue_tag}
<div class="matchups-card">{matchups_html}</div>"""


# ── Best XI Formation (FWD top → GK bottom) ────────────────────────────────

def build_best_xi_formation(best_xi: List[Dict]) -> str:
    by_pos: Dict[str, List[Dict]] = {}
    for p in best_xi:
        pos = p.get("position", "Forward")
        by_pos.setdefault(pos, []).append(p)

    max_rtg = max((float(p.get("rating", 0) or 0) for p in best_xi), default=0)

    rows_html = ""
    # Display order: FWD → MID → DEF → GK (attacking end at top)
    for pos in reversed(POS_ORDER):
        players = by_pos.get(pos, [])
        if not players:
            continue
        cards = ""
        for p in players:
            rtg     = float(p.get("rating", 0) or 0)
            top_cls = " top-player" if rtg == max_rtg and rtg > 0 else ""
            cards  += f"""<div class="player-card{top_cls}">
  <span class="pc-pos">{POS_SHORT.get(pos, '??')}</span>
  <span class="pc-name">{esc(p.get('name', ''))}</span>
  <span class="pc-rtg {xi_rtg_cls(rtg)}">{rtg:.0f}</span>
</div>"""
        label = {"Goalkeeper":"GK","Defender":"Defenders","Midfielder":"Midfielders","Forward":"Forwards"}[pos]
        rows_html += f'<div class="formation-row"><div class="fr-label">{label}</div><div class="fr-cards">{cards}</div></div>'

    return f'<div class="formation-pitch">{rows_html}</div>'


# ── Full Squad Table ───────────────────────────────────────────────────────

def build_squad_table(players: List[Dict]) -> str:
    by_pos: Dict[str, List[Dict]] = {}
    for p in players:
        pos = p.get("position", "Forward")
        by_pos.setdefault(pos, []).append(p)

    rows = ""
    for pos in POS_ORDER:
        group = sorted(by_pos.get(pos, []), key=lambda x: float(x.get("rating", 0) or 0), reverse=True)
        for p in group:
            rtg = float(p.get("rating", 0) or 0)
            mv  = fmt_mv(p.get("market_value_eur"))
            g   = p.get("goals", 0) or 0
            a   = p.get("assists", 0) or 0
            ga  = f"{g}/{a}" if (g or a) else "—"
            w   = bar_w(rtg)
            cls = squad_rtg_cls(rtg)
            rows += (
                f'<tr>'
                f'<td><span class="sq-pos">{POS_SHORT.get(pos, "?")}</span></td>'
                f'<td class="sq-name">{esc(p.get("name", ""))}</td>'
                f'<td style="text-align:right"><span class="sq-rtg {cls}">{rtg:.0f}</span></td>'
                f'<td class="sq-bar-cell"><div class="sq-bar-track"><div class="sq-bar-fill {cls}-bg" style="width:{w}%"></div></div></td>'
                f'<td style="text-align:right;color:var(--gr);font-size:12px">{mv}</td>'
                f'<td style="text-align:right;color:var(--gr);font-size:12px">{ga}</td>'
                f'</tr>'
            )

    return f"""<div class="squad-wrap">
  <table class="squad-table">
    <thead><tr>
      <th>Pos</th><th>Player</th>
      <th style="text-align:right">Rating</th>
      <th style="min-width:80px"></th>
      <th style="text-align:right">Value</th>
      <th style="text-align:right">G/A</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""


# ── Structured Data (JSON-LD) ──────────────────────────────────────────────

def build_structured_data(entry: Dict, team_players: List[Dict],
                           groups_data: Dict, idx_by_team: Dict) -> str:
    team   = entry.get("team", "")
    rank   = entry.get("rank", 0)
    sci    = float(entry.get("sci", 0) or 0)
    sq     = float(entry.get("squad_quality", 0) or 0)
    sd     = float(entry.get("squad_depth", 0) or 0)
    xf     = float(entry.get("xfactor", 0) or 0)
    group  = entry.get("group", "?")
    best_xi = entry.get("best_xi", [])
    slug   = slugify(team)

    # Top 3 players by rating
    top3 = sorted(best_xi, key=lambda p: float(p.get("rating", 0) or 0), reverse=True)[:3]
    top3_str = ", ".join(f"{p.get('name','')} ({float(p.get('rating',0)):.0f})" for p in top3)
    avg_rtg = (sum(float(p.get("rating",0) or 0) for p in best_xi) / len(best_xi)) if best_xi else 0
    key_player = top3[0].get("name", "") if top3 else ""
    key_rtg    = float(top3[0].get("rating", 0) or 0) if top3 else 0

    # Group rivals
    group_info = groups_data.get(group, {})
    rivals = [t for t in group_info.get("teams", []) if t != team]
    rivals_str = ", ".join(
        f"{r} (#{idx_by_team.get(r,{}).get('rank','?')})" for r in rivals
    )
    group_pos_labels = ["first", "second", "third", "fourth"]
    group_teams_ranked = sorted(
        group_info.get("teams", []),
        key=lambda t: int(idx_by_team.get(t, {}).get("rank", 99) or 99)
    )
    pos_in_group = group_teams_ranked.index(team) if team in group_teams_ranked else 0
    group_pos_label = group_pos_labels[pos_in_group] if pos_in_group < 4 else "among the groups"

    win_chance = "realistic" if rank <= 8 else ("possible" if rank <= 16 else "a long shot")
    comparison = f"They rank {group_pos_label} in Group {group} by SC Power Index."

    # Build athletes list
    athletes = []
    for p in sorted(best_xi, key=lambda p: float(p.get("rating",0) or 0), reverse=True)[:5]:
        athletes.append({"@type": "Person", "name": p.get("name", "")})

    faq = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": f"How good is {team} for World Cup 2026?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (f"{team} is ranked #{rank} of 48 teams with an SC Power Index score of {sci:.2f}. "
                             f"Their squad quality is {sq:.1f}, depth is {sd:.1f}, and X-Factor is {xf:.1f}. "
                             f"Key player: {key_player} rated {key_rtg:.0f}.")
                }
            },
            {
                "@type": "Question",
                "name": f"Who are {team}'s best players for World Cup 2026?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (f"{team}'s highest rated players are {top3_str}. "
                             f"Their Best XI averages a rating of {avg_rtg:.1f}.")
                }
            },
            {
                "@type": "Question",
                "name": f"Can {team} win World Cup 2026?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (f"Based on the SC Power Index, {team} is ranked #{rank} of 48 teams, making them {win_chance} contenders. "
                             f"They are in Group {group} alongside {rivals_str}. {comparison}")
                }
            },
            {
                "@type": "Question",
                "name": f"Who is in {team}'s World Cup 2026 group?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (f"{team} is in Group {group} with {rivals_str}. "
                             f"{team}'s SC Power Index of {sci:.2f} ranks them {group_pos_label} in the group.")
                }
            }
        ]
    }

    sports_team = {
        "@context": "https://schema.org",
        "@type": "SportsTeam",
        "name": f"{team} national football team",
        "sport": "Football",
        "memberOf": {"@type": "SportsOrganization", "name": "FIFA World Cup 2026"},
        "athlete": athletes,
        "url": f"https://www.squadcheck.club/teams/{slug}.html"
    }

    return (f'<script type="application/ld+json">{json.dumps(faq, ensure_ascii=False)}</script>\n'
            f'<script type="application/ld+json">{json.dumps(sports_team, ensure_ascii=False)}</script>')


# ── CSS ────────────────────────────────────────────────────────────────────

CSS = """
:root{
  --lime:#CCFF00;--lime-d:#99BB00;--lime-dim:rgba(204,255,0,.08);
  --dark:#0A0A0C;--d2:#111114;--d3:#1A1A1F;--d4:#252530;
  --wh:#F0F0F2;--gr:#8A8A96;--coral:#FF6B4A;--blue:#4A9EFF;
  --bd:rgba(255,255,255,.07);
}
*{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{font-family:'Outfit',sans-serif;background:var(--dark);color:var(--wh);-webkit-font-smoothing:antialiased;overflow-x:hidden}
a{color:inherit}

/* ── NAV ── */
nav{position:sticky;top:0;z-index:100;display:flex;justify-content:space-between;align-items:center;padding:0 5%;height:64px;background:rgba(10,10,12,.97);backdrop-filter:blur(20px);border-bottom:.5px solid var(--bd)}
.nav-logo{font-weight:900;font-size:18px;text-decoration:none;color:var(--wh);letter-spacing:-.02em}
.nav-logo span{color:var(--lime)}
.nav-links{display:flex;gap:8px;align-items:center}
.nav-links a{font-size:13px;font-weight:600;color:rgba(255,255,255,.45);text-decoration:none;padding:6px 14px;border-radius:20px;transition:all .2s}
.nav-links a:hover{color:var(--wh)}
.nav-cta{background:var(--lime)!important;color:#000!important;font-weight:800!important}
.back-link{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;color:var(--gr);text-decoration:none;padding:20px 5% 0;transition:color .2s;max-width:1200px;margin:0 auto;display:block}
.back-link:hover{color:var(--lime)}

/* ── HERO ── */
.hero{padding:48px 5% 52px;max-width:1200px;margin:0 auto;position:relative;overflow:hidden}
.hero::after{content:'';position:absolute;top:-120px;right:-200px;width:600px;height:600px;border-radius:50%;background:radial-gradient(circle,rgba(204,255,0,.05) 0%,transparent 65%);pointer-events:none}
.hero-big-rank{font-family:'DM Mono',monospace;font-size:clamp(80px,15vw,140px);font-weight:500;color:var(--lime);line-height:1;opacity:.9;display:block;margin-bottom:-8px}
.hero-title-row{display:flex;align-items:center;gap:16px;margin-bottom:8px;flex-wrap:wrap}
.hero-flag{font-size:clamp(40px,6vw,60px);line-height:1}
h1.hero-name{font-size:clamp(32px,5vw,62px);font-weight:900;letter-spacing:-.03em;line-height:1.05}
.hero-of48{font-size:14px;color:var(--gr);margin-bottom:24px;font-weight:500}
.hero-sci-row{display:flex;align-items:baseline;gap:10px;margin-bottom:6px}
.hero-sci-num{font-family:'DM Mono',monospace;font-size:clamp(42px,6vw,72px);font-weight:500;color:var(--lime);line-height:1}
.hero-sci-label{font-size:11px;text-transform:uppercase;letter-spacing:2.5px;color:var(--gr);font-weight:700;align-self:center}
.hero-badges{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0 16px}
.rank-badge{font-family:'DM Mono',monospace;font-size:13px;font-weight:700;padding:5px 14px;border-radius:100px;background:rgba(204,255,0,.1);border:.5px solid rgba(204,255,0,.25);color:var(--lime)}
.conf-badge{font-size:11px;font-weight:700;padding:5px 12px;border-radius:6px;text-transform:uppercase;letter-spacing:.8px}
.c-uefa{background:rgba(74,158,255,.15);color:#4A9EFF}
.c-csb{background:rgba(255,184,74,.15);color:#FFB84A}
.c-ccf{background:rgba(255,107,74,.15);color:#FF6B4A}
.c-afc{background:rgba(74,255,158,.15);color:#4AFF9E}
.c-caf{background:rgba(255,154,74,.15);color:#FF9A4A}
.c-oth{background:rgba(150,150,150,.15);color:#aaa}
.grp-badge{font-family:'DM Mono',monospace;font-size:11px;font-weight:700;padding:5px 12px;border-radius:6px;background:rgba(204,255,0,.1);color:var(--lime);border:.5px solid rgba(204,255,0,.2)}
.hero-summary{font-size:16px;color:rgba(255,255,255,.65);line-height:1.6;max-width:600px}

/* ── SECTIONS ── */
.section{padding:56px 5%;border-top:.5px solid var(--bd);max-width:1200px;margin:0 auto}
.eyebrow{font-size:10px;text-transform:uppercase;letter-spacing:3px;color:var(--lime);font-weight:700;margin-bottom:10px}
.sec-h2{font-size:clamp(20px,3vw,30px);font-weight:800;letter-spacing:-.02em;margin-bottom:28px;line-height:1.15}
.sec-h2 em{color:var(--lime);font-style:normal}

/* ── INSIGHT CARD ── */
.insight-card{background:var(--d3);border:.5px solid var(--bd);border-left:3px solid var(--lime);border-radius:0 12px 12px 0;padding:22px 28px;max-width:720px}
.insight-text{font-size:15px;color:rgba(255,255,255,.8);line-height:1.8}

/* ── BREAKDOWN CARDS (2×2 grid) ── */
.bk-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.bk-card{background:var(--d3);border:.5px solid var(--bd);border-radius:14px;padding:20px 22px;display:flex;flex-direction:column;gap:8px;transition:border-color .2s}
.bk-card:hover{border-color:rgba(204,255,0,.2)}
.bk-card-head{display:flex;justify-content:space-between;align-items:center}
.bk-card-title{font-size:13px;font-weight:700;color:var(--wh)}
.bk-card-weight{font-size:11px;color:var(--gr);font-weight:600}
.bk-card-score{font-family:'DM Mono',monospace;font-size:36px;font-weight:500;color:var(--lime);line-height:1}
.bk-bar-track{height:5px;background:var(--d4);border-radius:4px;overflow:hidden}
.bk-bar-fill{height:100%;background:var(--lime);border-radius:4px}
.bk-card-note{font-size:12px;color:var(--gr)}
.bk-card-cmp{font-size:12px;font-weight:600;margin-top:2px}
.cmp-up{color:var(--lime)}
.cmp-dn{color:var(--gr)}
.u23-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
.u23-chip{background:rgba(204,255,0,.08);border:.5px solid rgba(204,255,0,.2);border-radius:100px;padding:3px 10px;font-size:11px;font-weight:600;color:var(--wh)}
.u23-chip em{color:var(--lime);font-style:normal;margin-left:4px}

/* ── GROUP RIVALS ── */
.group-cards-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:14px}
.group-card{display:flex;flex-direction:column;gap:10px;padding:16px 18px;background:var(--d3);border:.5px solid var(--bd);border-radius:14px;text-decoration:none;color:var(--wh);transition:all .2s}
.group-card:hover{border-color:rgba(204,255,0,.2);background:var(--d4)}
.gc-current{border-color:rgba(204,255,0,.4)!important;background:rgba(204,255,0,.04)!important}
.gc-top{display:flex;align-items:center;gap:10px}
.gc-flag{font-size:26px;line-height:1}
.gc-name{font-weight:700;font-size:14px;line-height:1.2}
.gc-rank{font-size:11px;color:var(--gr);margin-top:2px}
.gc-sci{font-family:'DM Mono',monospace;font-size:18px;font-weight:500;color:var(--lime);margin-left:auto;flex-shrink:0}
.gc-bar-track{height:3px;background:var(--d4);border-radius:2px;overflow:hidden}
.gc-bar-fill{height:100%;background:var(--lime);border-radius:2px}
.gc-player{font-size:12px;color:var(--gr)}
.gc-pr{color:var(--lime);font-weight:700;margin-left:4px}
.group-venues{font-size:12px;color:var(--gr);padding:6px 0 10px}
.matchups-card{background:var(--d3);border:.5px solid var(--bd);border-radius:12px;padding:16px 20px;display:flex;flex-direction:column;gap:12px}
.mu-row{display:flex;flex-direction:column;gap:4px;padding-bottom:12px;border-bottom:.5px solid var(--bd)}
.mu-row:last-child{padding-bottom:0;border-bottom:none}
.mu-label{font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:var(--gr);font-weight:600}
.mu-line{font-size:14px;color:var(--wh);line-height:1.5}
.mu-line strong{color:var(--lime)}

/* ── BEST XI FORMATION ── */
.formation-pitch{display:flex;flex-direction:column;gap:20px;padding:24px;background:var(--d3);border:.5px solid var(--bd);border-radius:16px;max-width:720px}
.formation-row{display:flex;align-items:center;gap:16px}
.fr-label{font-size:10px;text-transform:uppercase;letter-spacing:2px;color:var(--gr);font-weight:600;width:80px;text-align:right;flex-shrink:0}
.fr-cards{display:flex;gap:10px;flex-wrap:wrap}
.player-card{display:flex;flex-direction:column;align-items:center;gap:5px;padding:12px 14px;background:var(--d4);border:.5px solid var(--bd);border-radius:12px;min-width:90px;transition:border-color .2s}
.player-card:hover{border-color:rgba(204,255,0,.2)}
.top-player{border-color:rgba(204,255,0,.5)!important;background:rgba(204,255,0,.04)!important}
.pc-pos{font-family:'DM Mono',monospace;font-size:9px;color:var(--gr);background:rgba(255,255,255,.06);padding:2px 6px;border-radius:4px;letter-spacing:.5px}
.pc-name{font-size:12px;font-weight:600;text-align:center;line-height:1.3}
.pc-rtg{font-family:'DM Mono',monospace;font-size:20px;font-weight:500}
.xi-rtg-e{color:var(--lime)}
.xi-rtg-g{color:var(--wh)}
.xi-rtg-o{color:var(--gr)}

/* ── SQUAD TABLE ── */
.squad-wrap{overflow-x:auto}
.squad-table{width:100%;border-collapse:collapse;min-width:480px}
.squad-table thead th{padding:10px 14px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:2px;color:var(--gr);font-weight:600;border-bottom:.5px solid var(--bd)}
.squad-table tbody tr{border-bottom:.5px solid rgba(255,255,255,.03);transition:background .12s}
.squad-table tbody tr:hover{background:rgba(255,255,255,.025)}
.squad-table td{padding:11px 14px;vertical-align:middle}
.sq-pos{font-family:'DM Mono',monospace;font-size:9px;color:var(--gr);background:var(--d4);padding:2px 6px;border-radius:4px}
.sq-name{font-weight:500;font-size:14px}
.sq-rtg{font-family:'DM Mono',monospace;font-size:14px;font-weight:500}
.rtg-elite{color:var(--lime)}
.rtg-good{color:var(--wh)}
.rtg-dim{color:var(--gr)}
.sq-bar-cell{width:90px;padding-right:0}
.sq-bar-track{height:4px;background:var(--d4);border-radius:3px;overflow:hidden}
.sq-bar-fill{height:100%;border-radius:3px}
.rtg-elite-bg{background:var(--lime)}
.rtg-good-bg{background:rgba(240,240,242,.4)}
.rtg-dim-bg{background:rgba(138,138,150,.3)}

/* ── PREV/NEXT NAV ── */
.pn-nav{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:32px 5% 48px;max-width:1200px;margin:0 auto}
.pn-link{display:flex;flex-direction:column;gap:4px;padding:18px 22px;background:var(--d3);border:.5px solid var(--bd);border-radius:14px;text-decoration:none;color:var(--wh);transition:all .2s}
.pn-link:hover{border-color:rgba(204,255,0,.3)}
.pn-link.pn-next{text-align:right}
.pn-dir{font-size:10px;text-transform:uppercase;letter-spacing:2px;color:var(--gr);font-weight:700}
.pn-team{font-size:15px;font-weight:700;margin-top:2px}
.pn-score{font-family:'DM Mono',monospace;font-size:13px;color:var(--lime)}

/* ── FOOTER ── */
footer{padding:28px 5%;text-align:center;border-top:.5px solid var(--bd)}
footer a{color:var(--gr);text-decoration:none;font-size:13px;transition:color .2s}
footer a:hover{color:var(--lime)}
.f-copy{font-size:11px;color:var(--gr);opacity:.3;margin-top:8px}

/* ── RESPONSIVE ── */
@media(max-width:900px){
  .bk-grid{grid-template-columns:1fr}
  .group-cards-row{grid-template-columns:repeat(2,1fr)}
}
@media(max-width:600px){
  .group-cards-row{grid-template-columns:1fr 1fr}
  .formation-pitch{padding:16px}
  .fr-label{width:56px;font-size:9px}
  .player-card{min-width:72px;padding:10px 10px}
  .pn-nav{grid-template-columns:1fr}
  .hero{padding:32px 5% 36px}
  .section{padding:36px 5%}
  .hero-big-rank{font-size:clamp(72px,18vw,120px)}
}
@media(max-width:380px){
  .group-cards-row{grid-template-columns:1fr}
}
"""


# ── Full page builder ──────────────────────────────────────────────────────

def build_page(
    entry: Dict[str, Any],
    all_players: List[Dict[str, Any]],
    groups_data: Dict[str, Any],
    idx_by_team: Dict[str, Dict],
    sorted_entries: List[Dict],
    leader: Dict[str, Any],
) -> str:
    team      = entry["team"]
    flag      = FLAGS.get(team, "🏳️")
    sci       = float(entry.get("sci", 0) or 0)
    group     = entry.get("group", "?")
    conf      = entry.get("confederation", "Other")
    rank      = entry.get("rank", 0)
    best_xi   = entry.get("best_xi", [])
    u23_stars = entry.get("u23_stars", [])
    slug      = slugify(team)

    team_players = sorted(
        [p for p in all_players if p.get("team") == team],
        key=lambda p: (
            POS_ORDER.index(p.get("position", "Forward")) if p.get("position") in POS_ORDER else 99,
            -float(p.get("rating", 0) or 0)
        )
    )

    # Prev / next by rank
    rank_int   = int(rank) if rank else 0
    prev_entry = next((e for e in sorted_entries if e["rank"] == rank_int - 1), None)
    next_entry = next((e for e in sorted_entries if e["rank"] == rank_int + 1), None)

    # Meta content
    key_player = best_xi[0].get("name", "") if best_xi else ""
    if best_xi:
        top_p = max(best_xi, key=lambda p: float(p.get("rating", 0) or 0))
        key_player = top_p.get("name", "")
        key_rtg    = float(top_p.get("rating", 0) or 0)
    else:
        key_rtg = 0

    title_tag = f"{esc(team)} World Cup 2026: Squad, Player Ratings &amp; Chances | SquadCheck"
    desc_tag  = (f"{esc(team)} ranked #{rank} of 48 for World Cup 2026. "
                 f"Best XI, full squad ratings, Group {esc(group)} rivals compared. "
                 f"SCI score: {sci:.2f}. Key player: {esc(key_player)} ({key_rtg:.0f}).")
    canon_url = f"https://www.squadcheck.club/teams/{slug}.html"

    summary_line    = hero_summary(team, best_xi, entry)
    breakdown_cards = build_breakdown_cards(entry, leader)
    group_section   = build_group_section(team, group, groups_data, idx_by_team)
    formation_html  = build_best_xi_formation(best_xi)
    squad_html      = build_squad_table(team_players)
    ld_json         = build_structured_data(entry, team_players, groups_data, idx_by_team)

    # Prev/next links
    if prev_entry:
        ps = slugify(prev_entry["team"])
        prev_html = f'<a href="/teams/{ps}.html" class="pn-link pn-prev"><span class="pn-dir">← #{prev_entry["rank"]}</span><span class="pn-team">{FLAGS.get(prev_entry["team"],"🏳️")} {esc(prev_entry["team"])}</span><span class="pn-score">{float(prev_entry.get("sci",0)):.2f}</span></a>'
    else:
        prev_html = '<div></div>'
    if next_entry:
        ns = slugify(next_entry["team"])
        next_html = f'<a href="/teams/{ns}.html" class="pn-link pn-next"><span class="pn-dir">#{next_entry["rank"]} →</span><span class="pn-team">{esc(next_entry["team"])} {FLAGS.get(next_entry["team"],"🏳️")}</span><span class="pn-score">{float(next_entry.get("sci",0)):.2f}</span></a>'
    else:
        next_html = '<div></div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title_tag}</title>
<meta name="description" content="{desc_tag}">
<link rel="canonical" href="{canon_url}">
<meta property="og:title" content="{title_tag}">
<meta property="og:description" content="{desc_tag}">
<meta property="og:url" content="{canon_url}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="SquadCheck">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title_tag}">
<meta name="twitter:description" content="{desc_tag}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800;900&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
{ld_json}
<style>{CSS}</style>
</head>
<body>

<nav>
  <a href="/" class="nav-logo">Squad<span>Check</span></a>
  <div class="nav-links">
    <a href="/power-index.html">Power Index</a>
    <a href="/" class="nav-cta">Home</a>
  </div>
</nav>

<a href="/power-index.html" class="back-link">← Back to Power Index</a>

<section class="hero">
  <span class="hero-big-rank">#{rank}</span>
  <div class="hero-title-row">
    <span class="hero-flag">{flag}</span>
    <h1 class="hero-name">{esc(team)}</h1>
  </div>
  <div class="hero-of48">of 48 teams</div>
  <div class="hero-sci-row">
    <span class="hero-sci-num">{sci:.2f}</span>
    <span class="hero-sci-label">SC Power Index</span>
  </div>
  <div class="hero-badges">
    <span class="rank-badge">Ranked #{rank}</span>
    <span class="conf-badge {conf_cls(conf)}">{esc(conf)}</span>
    <span class="grp-badge">Group {esc(group)}</span>
  </div>
  <p class="hero-summary">{esc(summary_line)}</p>
</section>

<div style="border-top:.5px solid var(--bd)">

<section class="section">
  <div class="eyebrow">Key Insight</div>
  <h2 class="sec-h2">The Story Behind <em>the Score</em></h2>
  <div class="insight-card"><p class="insight-text">{esc(INSIGHTS.get(team, DEFAULT_INSIGHT))}</p></div>
</section>

<section class="section">
  <div class="eyebrow">Score Breakdown</div>
  <h2 class="sec-h2">SC Power Index <em>Breakdown</em></h2>
  {breakdown_cards}
</section>

<section class="section">
  <div class="eyebrow">Group {esc(group)}</div>
  <h2 class="sec-h2">Group {esc(group)}: Can <em>{esc(team)}</em> Advance?</h2>
  {group_section}
</section>

<section class="section">
  <div class="eyebrow">Formation</div>
  <h2 class="sec-h2">Best XI — <em>Starting Formation</em></h2>
  {formation_html}
</section>

<section class="section">
  <div class="eyebrow">Full Roster</div>
  <h2 class="sec-h2">Full Squad — <em>All {len(team_players)} Players</em> Rated</h2>
  {squad_html}
</section>

</div>

<div class="pn-nav">
  {prev_html}
  {next_html}
</div>

<footer>
  <a href="/power-index.html">Power Index</a> &nbsp;·&nbsp;
  <a href="/">Home</a> &nbsp;·&nbsp;
  <a href="https://instagram.com/squadcheck.club" target="_blank" rel="noopener">Instagram</a>
  <div class="f-copy">SquadCheck © 2026 · Market values from Transfermarkt · Stats from football-data.org</div>
</footer>

</body>
</html>"""


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    print("[generate_team_pages] Loading data...")
    idx_entries: List[Dict]  = json.loads(IDX_FILE.read_text(encoding="utf-8"))
    all_players: List[Dict]  = json.loads(PLAYERS_FILE.read_text(encoding="utf-8"))
    groups_data: Dict        = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))

    idx_by_team: Dict[str, Dict] = {e["team"]: e for e in idx_entries}
    sorted_entries = sorted(idx_entries, key=lambda x: x["rank"])
    leader = sorted_entries[0]  # #1 team for comparison

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    created = []
    for entry in sorted_entries:
        sl       = slugify(entry["team"])
        out_file = OUTPUT_DIR / f"{sl}.html"
        html     = build_page(entry, all_players, groups_data, idx_by_team, sorted_entries, leader)
        out_file.write_text(html, encoding="utf-8")
        created.append(f"  #{entry['rank']:2d}  {entry['team']:<34}  →  teams/{sl}.html")

    print(f"[generate_team_pages] Created {len(created)} team pages in {OUTPUT_DIR}")
    print("\n".join(created))


if __name__ == "__main__":
    main()
