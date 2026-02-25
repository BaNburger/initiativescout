from __future__ import annotations

import html
import json
import webbrowser
from pathlib import Path
from typing import Any

from rich.box import ROUNDED
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from initiative_tracker.config import Settings


def _read_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def _redact(value: str) -> str:
    if "@" in value:
        user, _, domain = value.partition("@")
        return f"{user[:2]}***@{domain}"
    return value


def _find_dossier(dossiers: list[dict[str, Any]], initiative_id: int | None = None) -> dict[str, Any] | None:
    if initiative_id is not None:
        for row in dossiers:
            if int(row.get("initiative_id", -1)) == initiative_id:
                return row
    return dossiers[0] if dossiers else None


def _find_initiative_id_by_name(dossiers: list[dict[str, Any]], name: str) -> int | None:
    key = name.casefold().strip()
    for row in dossiers:
        if str(row.get("initiative_name", "")).casefold().strip() == key:
            return int(row.get("initiative_id"))
    for row in dossiers:
        if key in str(row.get("initiative_name", "")).casefold():
            return int(row.get("initiative_id"))
    return None


def load_result_payload(
    settings: Settings,
    *,
    top_n: int,
    lens: str = "outreach",
    initiative_id: int | None = None,
    include_private: bool = False,
) -> dict[str, Any]:
    exports = settings.exports_dir

    technologies = _read_json(exports / "top_technologies.json")[:top_n]
    markets = _read_json(exports / "top_market_opportunities.json")[:top_n]
    teams = _read_json(exports / "top_teams.json")[:top_n]
    outreach = _read_json(exports / "top_outreach_targets.json")[:top_n]
    upside = _read_json(exports / "top_venture_upside.json")[:top_n]
    talent_operators = _read_json(exports / "top_talent_operators.json")[:top_n]
    talent_alumni = _read_json(exports / "top_talent_alumni_angels.json")[:top_n]
    dd_investable = _read_json(exports / "investable_rankings.json")[:top_n]
    dd_watchlist = _read_json(exports / "watchlist_rankings.json")[:top_n]
    team_capability_matrix = _read_json(exports / "team_capability_matrix.json")
    initiatives = _read_json(exports / "initiatives_master.json")
    dossiers = _read_json(exports / "initiative_dossiers.json")

    for row in technologies:
        if row.get("score") is None and row.get("opportunity_score") is not None:
            row["score"] = row.get("opportunity_score")
    for row in markets:
        if row.get("score") is None and row.get("opportunity_score") is not None:
            row["score"] = row.get("opportunity_score")
    for row in teams:
        if row.get("score") is None and row.get("team_strength") is not None:
            row["score"] = row.get("team_strength")
        if row.get("legacy_composite") is None and row.get("composite_score") is not None:
            row["legacy_composite"] = row.get("composite_score")

    if not include_private:
        for item in talent_operators:
            channels = item.get("contact_channels")
            if isinstance(channels, list):
                item["contact_channels"] = [_redact(str(channel)) for channel in channels]
        for item in talent_alumni:
            channels = item.get("contact_channels")
            if isinstance(channels, list):
                item["contact_channels"] = [_redact(str(channel)) for channel in channels]
        for dossier in dossiers:
            people = dossier.get("top_talent") or []
            if not isinstance(people, list):
                continue
            for person in people:
                channels = person.get("contact_channels")
                if isinstance(channels, list):
                    person["contact_channels"] = [_redact(str(channel)) for channel in channels]

    lens_key = "outreach_now_score" if lens == "outreach" else "venture_upside_score"
    sorted_initiatives = sorted(
        initiatives,
        key=lambda row: ((row.get("scores") or {}).get(lens_key) or -1),
        reverse=True,
    )
    top_initiatives = sorted_initiatives[:top_n]

    shortlist = outreach if lens == "outreach" else upside
    shortlist_with_ids: list[dict[str, Any]] = []
    capability_by_id: dict[int, dict[str, Any]] = {}
    for row in team_capability_matrix:
        identifier = row.get("initiative_id")
        if isinstance(identifier, int):
            capability_by_id[identifier] = row
        elif str(identifier).isdigit():
            capability_by_id[int(str(identifier))] = row
    for row in shortlist:
        initiative_name = str(row.get("initiative_name") or row.get("name") or "")
        resolved_id = row.get("initiative_id")
        if resolved_id is None and initiative_name:
            resolved_id = _find_initiative_id_by_name(dossiers, initiative_name)
        enriched = dict(row)
        enriched["initiative_id"] = int(resolved_id) if isinstance(resolved_id, int) or str(resolved_id).isdigit() else None
        capability = capability_by_id.get(enriched["initiative_id"]) if isinstance(enriched["initiative_id"], int) else None
        if capability:
            enriched["strong_in"] = capability.get("strong_in", [])
            enriched["need_help_in"] = capability.get("need_help_in", [])
            enriched["support_priority"] = capability.get("support_priority")
        shortlist_with_ids.append(enriched)

    selected_dossier = _find_dossier(dossiers, initiative_id=initiative_id)
    if selected_dossier is None and shortlist_with_ids:
        first_id = shortlist_with_ids[0].get("initiative_id")
        if isinstance(first_id, int):
            selected_dossier = _find_dossier(dossiers, initiative_id=first_id)

    return {
        "top_n": top_n,
        "lens": lens,
        "technologies": technologies,
        "markets": markets,
        "teams": teams,
        "outreach": outreach,
        "upside": upside,
        "shortlist": shortlist_with_ids,
        "talent_operators": talent_operators,
        "talent_alumni": talent_alumni,
        "dd_investable": dd_investable,
        "dd_watchlist": dd_watchlist,
        "team_capability_matrix": team_capability_matrix,
        "initiatives": top_initiatives,
        "dossiers": dossiers,
        "selected_dossier": selected_dossier,
        "total_initiatives": len(initiatives),
    }


def render_cli_results(console: Console, payload: dict[str, Any], *, lens: str) -> None:
    summary = Table(show_header=True, header_style="bold cyan", box=ROUNDED)
    summary.add_column("Metric", style="bold")
    summary.add_column("Value")
    summary.add_row("top_n", str(payload["top_n"]))
    summary.add_row("lens", lens)
    summary.add_row("total_initiatives", str(payload["total_initiatives"]))
    summary.add_row("shortlist_items", str(len(payload["shortlist"])))
    summary.add_row("technology_items", str(len(payload["technologies"])))
    summary.add_row("market_items", str(len(payload["markets"])))
    summary.add_row("talent_operators", str(len(payload["talent_operators"])))
    summary.add_row("talent_alumni", str(len(payload["talent_alumni"])))
    summary.add_row("dd_investable", str(len(payload.get("dd_investable", []))))
    summary.add_row("dd_watchlist", str(len(payload.get("dd_watchlist", []))))
    console.print(Panel(summary, title="Venture Scout Summary", border_style="cyan"))

    shortlist = Table(show_header=True, header_style="bold green", box=ROUNDED)
    shortlist.add_column("#", style="dim")
    shortlist.add_column("Initiative", style="bold")
    shortlist.add_column("Score", justify="right")
    shortlist.add_column("Team", justify="right")
    shortlist.add_column("Market", justify="right")
    for idx, row in enumerate(payload["shortlist"], start=1):
        score_key = "score"
        shortlist.add_row(
            str(idx),
            str(row.get("initiative_name", "")),
            f"{(row.get(score_key) or 0):.4f}",
            f"{(row.get('team_strength') or 0):.4f}" if isinstance(row.get("team_strength"), (float, int)) else "-",
            f"{(row.get('market_opportunity') or 0):.4f}" if isinstance(row.get("market_opportunity"), (float, int)) else "-",
        )
    console.print(Panel(shortlist, title=f"Top {lens.title()} Targets", border_style="green"))

    dd_investable = payload.get("dd_investable") or []
    if dd_investable:
        dd_table = Table(show_header=True, header_style="bold cyan", box=ROUNDED)
        dd_table.add_column("#", style="dim")
        dd_table.add_column("Initiative", style="bold")
        dd_table.add_column("Conviction", justify="right")
        dd_table.add_column("Strong In")
        dd_table.add_column("Need Help")
        dd_table.add_column("Support")
        for idx, row in enumerate(dd_investable[: payload["top_n"]], start=1):
            dd_table.add_row(
                str(idx),
                str(row.get("initiative_name", "")),
                f"{(row.get('conviction_score') or row.get('score') or 0):.4f}",
                ", ".join(row.get("strong_in") or []) or "-",
                ", ".join(row.get("need_help_in") or []) or "-",
                str(row.get("support_priority") or "-"),
            )
        console.print(Panel(dd_table, title="DD Investable Ranking", border_style="cyan"))

    tech_table = Table(show_header=True, header_style="bold blue", box=ROUNDED)
    tech_table.add_column("#", style="dim")
    tech_table.add_column("Technology Domain", style="bold")
    tech_table.add_column("Score", justify="right")
    tech_table.add_column("Evidence", justify="right")
    for idx, row in enumerate(payload["technologies"], start=1):
        tech_table.add_row(
            str(idx),
            str(row.get("technology_domain", "")),
            f"{(row.get('score') or row.get('opportunity_score') or 0):.4f}",
            str(row.get("evidence_count", 0)),
        )
    console.print(Panel(tech_table, title="Top Technologies", border_style="blue"))

    talent_table = Table(show_header=True, header_style="bold magenta", box=ROUNDED)
    talent_table.add_column("#", style="dim")
    talent_table.add_column("Talent", style="bold")
    talent_table.add_column("Type")
    talent_table.add_column("Score", justify="right")
    merged_talent = [
        *[{"type": "operator", **row} for row in payload["talent_operators"]],
        *[{"type": "alumni", **row} for row in payload["talent_alumni"]],
    ]
    merged_talent = sorted(merged_talent, key=lambda row: row.get("score", 0), reverse=True)[: payload["top_n"]]
    for idx, row in enumerate(merged_talent, start=1):
        talent_table.add_row(
            str(idx),
            str(row.get("person_name", row.get("name", ""))),
            str(row.get("type", "")),
            f"{(row.get('score') or 0):.4f}",
        )
    console.print(Panel(talent_table, title="Top Talent", border_style="magenta"))

    dossier = payload.get("selected_dossier")
    if dossier:
        comp_rows = dossier.get("score_breakdown") or []
        comp_table = Table(show_header=True, header_style="bold yellow", box=ROUNDED)
        comp_table.add_column("Dimension", style="bold")
        comp_table.add_column("Component")
        comp_table.add_column("Norm", justify="right")
        comp_table.add_column("Weight", justify="right")
        comp_table.add_column("Evidence", justify="right")
        for row in comp_rows[:20]:
            comp_table.add_row(
                str(row.get("dimension", "")),
                str(row.get("component_key", "")),
                f"{(row.get('normalized_value') or 0):.4f}",
                f"{(row.get('weight') or 0):.4f}",
                str(len(row.get("evidence_refs") or [])),
            )
        console.print(
            Panel(
                comp_table,
                title=f"Dossier · {dossier.get('initiative_name', '')}",
                border_style="yellow",
            )
        )


def _render_rows(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    rendered: list[str] = []
    for idx, row in enumerate(rows, start=1):
        row_id = row.get("initiative_id")
        attrs = f' data-initiative-id="{row_id}"' if row_id is not None else ""
        cells = [f"<td>{idx}</td>"]
        for key, kind in columns:
            value = row.get(key)
            if kind == "float":
                try:
                    value_str = f"{float(value):.4f}"
                except (TypeError, ValueError):
                    value_str = "-"
            elif kind == "list":
                if isinstance(value, list):
                    value_str = ", ".join(str(item) for item in value[:6])
                else:
                    value_str = ""
            else:
                value_str = "" if value is None else str(value)
            cells.append(f"<td>{html.escape(value_str)}</td>")
        rendered.append(f"<tr{attrs}>" + "".join(cells) + "</tr>")
    return "\n".join(rendered)


def _dossier_html(dossier: dict[str, Any] | None) -> str:
    if not dossier:
        return "<p>Select an initiative row to view dossier.</p>"

    playbook = dossier.get("action_playbook") or {}
    risks = dossier.get("risk_flags") or []
    technologies = dossier.get("technology_profile") or []
    talent = dossier.get("top_talent") or []

    tech_items = "".join(
        f"<li><strong>{html.escape(str(t.get('technology_domain', '')))}</strong> · stage {html.escape(str(t.get('stage', 'research')))}</li>"
        for t in technologies[:10]
    )
    risk_items = "".join(f"<li>{html.escape(str(flag))}</li>" for flag in risks[:8]) or "<li>none</li>"
    talent_items = "".join(
        f"<li>{html.escape(str(item.get('name', '')))} ({html.escape(', '.join(item.get('roles', [])))})</li>"
        for item in talent[:8]
    ) or "<li>none</li>"

    return f"""
      <h3>{html.escape(str(dossier.get('initiative_name', '')))}</h3>
      <p><strong>University:</strong> {html.escape(str(dossier.get('university', '') or '-'))}</p>
      <p><strong>Why now:</strong> {html.escape(str(playbook.get('why_now', '')))}</p>
      <p><strong>Primary contact:</strong> {html.escape(str(playbook.get('primary_contact', '-') or '-'))}</p>
      <p><strong>First meeting goal:</strong> {html.escape(str(playbook.get('first_meeting_goal', '')))}</p>
      <h4>Developed Technologies</h4>
      <ul>{tech_items}</ul>
      <h4>Top Talent</h4>
      <ul>{talent_items}</ul>
      <h4>Risk Flags</h4>
      <ul>{risk_items}</ul>
    """


def build_html_dashboard(
    payload: dict[str, Any],
    output_path: Path,
    *,
    lens: str = "outreach",
    include_private: bool = False,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    shortlist_rows = _render_rows(
        payload["shortlist"],
        [
            ("initiative_name", "text"),
            ("score", "float"),
            ("team_strength", "float"),
            ("market_opportunity", "float"),
            ("support_fit", "float"),
        ],
    )
    tech_rows = _render_rows(
        payload["technologies"],
        [("technology_domain", "text"), ("score", "float"), ("evidence_count", "text")],
    )
    market_rows = _render_rows(
        payload["markets"],
        [("market_domain", "text"), ("score", "float"), ("evidence_count", "text")],
    )
    team_rows = _render_rows(
        payload["teams"],
        [("initiative_name", "text"), ("score", "float"), ("legacy_composite", "float")],
    )
    initiative_rows = _render_rows(
        payload["initiatives"],
        [("name", "text"), ("university", "text"), ("confidence", "float")],
    )

    talent_combined = [
        *[{"type": "operator", **row} for row in payload["talent_operators"]],
        *[{"type": "alumni", **row} for row in payload["talent_alumni"]],
    ]
    talent_combined = sorted(talent_combined, key=lambda row: row.get("score", 0), reverse=True)[: payload["top_n"]]
    talent_rows = _render_rows(
        talent_combined,
        [("person_name", "text"), ("type", "text"), ("score", "float"), ("evidence_count", "text")],
    )
    dd_rows = _render_rows(
        payload.get("dd_investable", [])[: payload["top_n"]],
        [
            ("initiative_name", "text"),
            ("conviction_score", "float"),
            ("strong_in", "list"),
            ("need_help_in", "list"),
            ("support_priority", "text"),
        ],
    )

    selected_dossier_html = _dossier_html(payload.get("selected_dossier"))
    dossiers_json = html.escape(json.dumps(payload.get("dossiers", []), ensure_ascii=False))

    html_page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Initiative Tracker Venture Console</title>
  <style>
    :root {{
      --bg: #edf2f7;
      --card: #ffffff;
      --ink: #13202f;
      --muted: #4c5f73;
      --line: #d6dde5;
      --accent: #0f766e;
      --accent-2: #1d4ed8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      color: var(--ink);
      background: radial-gradient(circle at 10% 0%, #d8e7f4 0%, #edf2f7 35%, #edf2f7 100%);
      padding: 24px;
    }}
    h1 {{ margin: 0; font-size: 30px; }}
    .sub {{ color: var(--muted); margin-top: 6px; font-size: 14px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin: 16px 0; }}
    .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 12px; box-shadow: 0 8px 18px rgba(14, 30, 55, 0.06); }}
    .kpi {{ font-size: 26px; font-weight: 700; color: var(--accent-2); }}
    .label {{ color: var(--muted); font-size: 12px; margin-top: 4px; text-transform: uppercase; }}
    .tabs {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }}
    .tab-btn {{ border: 1px solid var(--line); background: #fff; border-radius: 10px; padding: 8px 12px; font-weight: 600; cursor: pointer; }}
    .tab-btn.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
    .layout {{ display: grid; grid-template-columns: 1.3fr 0.9fr; gap: 14px; }}
    .panel {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 12px; box-shadow: 0 8px 18px rgba(14, 30, 55, 0.06); }}
    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid var(--line); text-align: left; padding: 8px 10px; vertical-align: top; }}
    th {{ background: #f8fafc; font-weight: 700; }}
    tr[data-initiative-id] {{ cursor: pointer; }}
    tr:hover td {{ background: #f8fbff; }}
    .note {{ font-size: 12px; color: var(--muted); margin-top: 6px; }}
    @media (max-width: 1000px) {{ .layout {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <h1>Initiative Tracker Venture Console</h1>
  <div class="sub">Lens: {html.escape(lens)} · Top {payload['top_n']} · Private details {'on' if include_private else 'redacted'}</div>

  <div class="grid">
    <div class="card"><div class="kpi">{payload['total_initiatives']}</div><div class="label">Total Initiatives</div></div>
    <div class="card"><div class="kpi">{len(payload['shortlist'])}</div><div class="label">Shortlist</div></div>
    <div class="card"><div class="kpi">{len(payload['talent_operators'])}</div><div class="label">Operators</div></div>
    <div class="card"><div class="kpi">{len(payload['talent_alumni'])}</div><div class="label">Alumni Angels</div></div>
  </div>

    <div class="tabs">
    <button class="tab-btn active" data-tab="tab-shortlist">{html.escape(lens.title())} Targets</button>
    <button class="tab-btn" data-tab="tab-dd">DD Investable</button>
    <button class="tab-btn" data-tab="tab-tech">Technologies</button>
    <button class="tab-btn" data-tab="tab-market">Markets</button>
    <button class="tab-btn" data-tab="tab-talent">Talent</button>
    <button class="tab-btn" data-tab="tab-team">Teams</button>
    <button class="tab-btn" data-tab="tab-initiative">Initiatives</button>
  </div>

  <div class="layout">
    <div class="panel">
      <section id="tab-shortlist" class="tab-panel active">
        <h3>Top {html.escape(lens.title())} Targets</h3>
        <table id="table-shortlist"><thead><tr><th>#</th><th>Initiative</th><th>Score</th><th>Team</th><th>Market</th><th>Support</th></tr></thead><tbody>{shortlist_rows}</tbody></table>
      </section>
      <section id="tab-dd" class="tab-panel">
        <h3>DD Investable Ranking</h3>
        <table><thead><tr><th>#</th><th>Initiative</th><th>Conviction</th><th>Strong In</th><th>Need Help In</th><th>Support Priority</th></tr></thead><tbody>{dd_rows}</tbody></table>
      </section>
      <section id="tab-tech" class="tab-panel">
        <h3>Top Technologies</h3>
        <table><thead><tr><th>#</th><th>Technology</th><th>Score</th><th>Evidence</th></tr></thead><tbody>{tech_rows}</tbody></table>
      </section>
      <section id="tab-market" class="tab-panel">
        <h3>Top Market Opportunities</h3>
        <table><thead><tr><th>#</th><th>Market</th><th>Score</th><th>Evidence</th></tr></thead><tbody>{market_rows}</tbody></table>
      </section>
      <section id="tab-talent" class="tab-panel">
        <h3>Top Talent</h3>
        <table><thead><tr><th>#</th><th>Name</th><th>Type</th><th>Score</th><th>Evidence</th></tr></thead><tbody>{talent_rows}</tbody></table>
      </section>
      <section id="tab-team" class="tab-panel">
        <h3>Top Teams</h3>
        <table><thead><tr><th>#</th><th>Initiative</th><th>Team Strength</th><th>Legacy Composite</th></tr></thead><tbody>{team_rows}</tbody></table>
      </section>
      <section id="tab-initiative" class="tab-panel">
        <h3>Top Initiatives by Lens</h3>
        <table><thead><tr><th>#</th><th>Name</th><th>University</th><th>Confidence</th></tr></thead><tbody>{initiative_rows}</tbody></table>
      </section>
      <div class="note">Click shortlist rows to open initiative dossier.</div>
    </div>

    <div class="panel" id="dossier-panel">{selected_dossier_html}</div>
  </div>

  <script>
    const dossiers = JSON.parse("{dossiers_json}");

    function renderDossier(initiativeId) {{
      const panel = document.getElementById('dossier-panel');
      const item = dossiers.find(x => Number(x.initiative_id) === Number(initiativeId));
      if (!item) return;

      const technologies = (item.technology_profile || []).slice(0, 10)
        .map(x => `<li><strong>${{x.technology_domain || ''}}</strong> · stage ${{x.stage || 'research'}}</li>`).join('');
      const talent = (item.top_talent || []).slice(0, 8)
        .map(x => `<li>${{x.name || ''}} (${{(x.roles || []).join(', ')}})</li>`).join('');
      const risks = (item.risk_flags || []).slice(0, 8)
        .map(x => `<li>${{x}}</li>`).join('') || '<li>none</li>';

      const playbook = item.action_playbook || {{}};
      panel.innerHTML = `
        <h3>${{item.initiative_name || ''}}</h3>
        <p><strong>University:</strong> ${{item.university || '-'}}</p>
        <p><strong>Why now:</strong> ${{playbook.why_now || ''}}</p>
        <p><strong>Primary contact:</strong> ${{playbook.primary_contact || '-'}}</p>
        <p><strong>First meeting goal:</strong> ${{playbook.first_meeting_goal || ''}}</p>
        <h4>Developed Technologies</h4>
        <ul>${{technologies || '<li>none</li>'}}</ul>
        <h4>Top Talent</h4>
        <ul>${{talent || '<li>none</li>'}}</ul>
        <h4>Risk Flags</h4>
        <ul>${{risks}}</ul>
      `;
    }}

    document.querySelectorAll('#table-shortlist tbody tr[data-initiative-id]').forEach(row => {{
      row.addEventListener('click', () => renderDossier(row.dataset.initiativeId));
    }});

    const tabButtons = document.querySelectorAll('.tab-btn');
    const tabPanels = document.querySelectorAll('.tab-panel');
    tabButtons.forEach(btn => {{
      btn.addEventListener('click', () => {{
        tabButtons.forEach(x => x.classList.remove('active'));
        tabPanels.forEach(x => x.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById(btn.dataset.tab).classList.add('active');
      }});
    }});
  </script>
</body>
</html>
"""

    output_path.write_text(html_page, encoding="utf-8")
    return output_path


def open_html(path: Path) -> None:
    webbrowser.open(path.resolve().as_uri(), new=2)
