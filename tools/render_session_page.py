"""
Renders one session_stats dict (from session_stats.compute_session_stats)
into a standalone HTML page. No external files needed at view time except
the Chart.js CDN script -- everything else is inlined.
"""

import json

BLUE = "#13294B"
ORANGE = "#E84A27"

_TIER_ORDER = ["full", "substantial", "brief"]
_STATUS_COLORS = {"staff": "#1D58A7", "student": ORANGE, "faculty": "#5C9E6E",
                   "postdoc": "#8B5CF6", "external": "#F59E0B", "unknown": "#6B7280"}
_UNIT_PALETTE = [BLUE, "#1D58A7", "#2E82C5", ORANGE, "#aaaaaa", "#5C9E6E",
                 "#8B5CF6", "#EC4899", "#F59E0B", "#10B981", "#6366F1", "#D97706"]


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def render_session_page(stats, program_name, session_number=None, total_in_series=None,
                        roster_section="", internal=False, dept_name=""):
    """stats: the dict returned by compute_session_stats.
    program_name: e.g. "Data Workshop".
    dept_name: optional, shown in the footer.
    session_number/total_in_series: optional, e.g. 15 of 15, for the header label.
    roster_section/internal: set only by the internal page (adds the named roster)."""

    header_label = program_name
    if session_number is not None and total_in_series is not None:
        header_label = f"{program_name} · Session #{session_number} of {total_in_series}"

    title = stats["topic"] or header_label
    label = "Presenters" if "," in (stats["presenter"] or "") else "Presenter"
    presenter_html = f" &nbsp;·&nbsp; {label}: {stats['presenter']}" if stats["presenter"] else ""
    if internal:
        header_label = f"INTERNAL · {header_label}"
        title = f"INTERNAL · {title}"
    banner = ('<div class="warn">Contains names. Internal use only. Do not publish or share outside the team.</div>'
              if internal else "")
    robots = '<meta name="robots" content="noindex">' if internal else ""

    units_sorted = sorted(stats["org_unit_counts"].items(), key=lambda kv: kv[1], reverse=True)
    unit_labels = [u for u, _ in units_sorted]
    unit_values = [v for _, v in units_sorted]
    unit_colors = [_UNIT_PALETTE[i % len(_UNIT_PALETTE)] for i in range(len(unit_labels))]

    status_items = list(stats["status_counts"].items())
    status_labels = [s for s, _ in status_items]
    status_values = [v for _, v in status_items]
    status_colors = [_STATUS_COLORS.get(s, "#999") for s in status_labels]

    tier_values = [stats["duration_tier_counts"].get(t, 0) for t in _TIER_ORDER]
    full, sub = stats["full_cutoff"], stats["substantial_cutoff"]
    tier_labels = [f"≥ {full} min (most of the session)", f"{sub}–{full - 1} min (substantial)",
                   f"< {sub} min (brief)"]
    footer = " &nbsp;&middot;&nbsp; ".join(x for x in (program_name, dept_name,
                                                      "University of Illinois Urbana-Champaign") if x)

    total = stats["total_attendees"]
    stayed_full_pct = round(100 * stats["duration_tier_counts"].get("full", 0) / total) if total else 0
    new_pct = round(100 * stats["new_count"] / total) if total else 0
    returning_pct = round(100 * stats["returning_count"] / total) if total else 0

    rank_sub = (f"series high: {stats['series_high_attendees']} ({stats['series_high_date']})"
                if stats["session_date"] != stats["series_high_date"] else "highest in the series")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
{robots}
<title>{title} — {stats['session_date']}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #f0f2f5; color: #333; font-size: 14px; }}
  header {{ background: {BLUE}; color: #fff; padding: 24px 32px; border-bottom: 5px solid {ORANGE}; }}
  .session-label {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.12em;
                    color: {ORANGE}; font-weight: 700; margin-bottom: 6px; }}
  header h1 {{ font-size: 1.7rem; font-weight: 800; line-height: 1.2; }}
  .meta {{ margin-top: 8px; font-size: 0.82rem; opacity: 0.7; }}
  .stats {{ display: flex; background: #fff; border-bottom: 1px solid #e5e5e5;
           box-shadow: 0 2px 6px rgba(0,0,0,.06); flex-wrap: wrap; }}
  .stat {{ flex: 1; min-width: 140px; text-align: center; padding: 18px 8px 14px; border-right: 1px solid #eee; }}
  .stat:last-child {{ border-right: none; }}
  .stat .n {{ font-size: 2.2rem; font-weight: 900; color: {BLUE}; line-height: 1; }}
  .stat .n.hi {{ color: {ORANGE}; }}
  .stat .l {{ font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.07em;
             color: #888; margin-top: 5px; font-weight: 600; }}
  .stat .sub {{ font-size: 0.67rem; color: #bbb; margin-top: 3px; }}
  .container {{ max-width: 1220px; margin: 0 auto; padding: 24px 20px; }}
  .row {{ display: flex; gap: 20px; margin-bottom: 20px; flex-wrap: wrap; }}
  .card {{ background: #fff; border-radius: 8px; padding: 20px;
          box-shadow: 0 1px 5px rgba(0,0,0,.07); flex: 1; min-width: 280px; }}
  .card h2 {{ font-size: 0.75rem; font-weight: 700; color: #666; text-transform: uppercase;
             letter-spacing: 0.07em; border-left: 4px solid {ORANGE}; padding-left: 9px; margin-bottom: 16px; }}
  .card.wide {{ flex: 2; }}
  .note {{ font-size: 0.7rem; color: #bbb; margin-top: 10px; font-style: italic; }}
  .ch-wrap {{ position: relative; }}
  .ch-wrap.h320 {{ height: 320px; }}
  .ch-wrap.h220 {{ height: 220px; }}
  .warn {{ background: #FFF3E0; color: #BF360C; padding: 10px 32px; font-size: 0.8rem; font-weight: 600; }}
  .card.full {{ min-width: 100%; }}
  .tbl-wrap {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
  thead th {{ background: {BLUE}; color: #fff; padding: 9px 10px; text-align: left; font-size: 0.68rem;
             font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; white-space: nowrap; }}
  thead th:first-child {{ width: 32px; text-align: center; }}
  tbody tr {{ border-bottom: 1px solid #f2f2f2; }}
  tbody tr:hover {{ background: #fafbff; }}
  tbody td {{ padding: 7px 10px; vertical-align: middle; }}
  td.rank {{ text-align: center; color: #ccc; font-size: 0.72rem; }}
  .badge {{ display: inline-block; padding: 2px 7px; border-radius: 12px; font-size: 0.62rem; font-weight: 700;
           text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap; }}
  .b-staff {{ background: #E3F2FD; color: #1565C0; }}
  .b-faculty {{ background: #E8F5E9; color: #2E7D32; }}
  .b-student {{ background: #FFF3E0; color: #BF360C; }}
  .b-unknown {{ background: #F3F4F6; color: #6B7280; }}
  .b-new {{ background: #FCE4EC; color: #B71C1C; }}
  .b-ret {{ background: #EDE7F6; color: #4527A0; }}
  .b-unit {{ background: #F3F4F6; color: #555; font-weight: 500; font-size: 0.62rem; }}
  .name-cell {{ font-weight: 600; color: {BLUE}; }}
  .dept-cell {{ color: #666; font-size: 0.75rem; }}
  .dur-wrap {{ display: flex; align-items: center; gap: 6px; min-width: 130px; }}
  .dur-bar-bg {{ flex: 1; height: 6px; background: #eee; border-radius: 3px; overflow: hidden; }}
  .dur-bar {{ height: 100%; border-radius: 3px; }}
  .dur-val {{ font-weight: 700; color: #555; font-size: 0.75rem; white-space: nowrap; }}
  .sessions-n {{ font-size: 0.62rem; color: #aaa; display: block; margin-top: 1px; }}
  footer {{ text-align: center; padding: 20px; color: #bbb; font-size: 0.72rem; }}
</style>
</head>
<body>

<header>
  <div class="session-label">{header_label}</div>
  <h1>{title}</h1>
  <div class="meta">{stats['session_date']}{presenter_html}</div>
</header>
{banner}

<div class="stats">
  <div class="stat">
    <div class="n hi">{total}</div>
    <div class="l">Attendees</div>
    <div class="sub">&nbsp;</div>
  </div>
  <div class="stat">
    <div class="n">{stats['avg_duration_minutes']}</div>
    <div class="l">Avg Min in Session</div>
    <div class="sub">series avg: {stats['series_avg_duration_minutes']} min</div>
  </div>
  <div class="stat">
    <div class="n hi">{stats['new_count']}</div>
    <div class="l">First-Time Attendees</div>
    <div class="sub">{new_pct}% of attendees</div>
  </div>
  <div class="stat">
    <div class="n">{stats['returning_count']}</div>
    <div class="l">Returning Attendees</div>
    <div class="sub">{returning_pct}% of attendees</div>
  </div>
  <div class="stat">
    <div class="n">{stayed_full_pct}%</div>
    <div class="l">Stayed &ge; {full} min</div>
    <div class="sub">{stats['duration_tier_counts'].get('full', 0)} of {total} attendees</div>
  </div>
  <div class="stat">
    <div class="n">{_ordinal(stats['rank_by_attendance'])}</div>
    <div class="l">Of {stats['total_sessions']} Sessions</div>
    <div class="sub">{rank_sub}</div>
  </div>
</div>

<div class="container">
  <div class="row">
    <div class="card wide">
      <h2>Attendees by Organizational Unit</h2>
      <div class="ch-wrap h320"><canvas id="orgChart"></canvas></div>
    </div>
    <div class="card">
      <h2>New vs. Returning</h2>
      <div class="ch-wrap h220"><canvas id="newRetChart"></canvas></div>
      <p class="note">New = first session ever attended in this program. {stats['returning_count']} returning, {stats['new_count']} first-time.</p>
    </div>
  </div>

  <div class="row">
    <div class="card">
      <h2>Engagement by Time in Session</h2>
      <div class="ch-wrap h220"><canvas id="durChart"></canvas></div>
      <p class="note">For a {stats['length_minutes']} min session: {full}+ min = attended most of it. &lt;{sub} min = brief check-in.</p>
    </div>
    <div class="card">
      <h2>Attendees by Status</h2>
      <div class="ch-wrap h220"><canvas id="statusChart"></canvas></div>
    </div>
  </div>
{roster_section}
</div>

<footer>{footer}</footer>

<script>
const BLUE = '{BLUE}';
const ORANGE = '{ORANGE}';

new Chart(document.getElementById('orgChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(unit_labels)},
    datasets: [{{ data: {json.dumps(unit_values)}, backgroundColor: {json.dumps(unit_colors)}, borderRadius: 4 }}]
  }},
  options: {{
    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ x: {{ ticks: {{ stepSize: 1 }} }}, y: {{ grid: {{ display: false }} }} }}
  }}
}});

new Chart(document.getElementById('newRetChart'), {{
  type: 'doughnut',
  data: {{
    labels: ['First-Time (New)', 'Returning'],
    datasets: [{{ data: [{stats['new_count']}, {stats['returning_count']}],
                 backgroundColor: [ORANGE, BLUE], borderWidth: 2, borderColor: '#fff' }}]
  }},
  options: {{ responsive: true, maintainAspectRatio: false, cutout: '60%',
             plugins: {{ legend: {{ position: 'bottom' }} }} }}
}});

new Chart(document.getElementById('durChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(tier_labels)},
    datasets: [{{ label: 'Attendees', data: {json.dumps(tier_values)},
                 backgroundColor: [BLUE, '#2E82C5', '#aaa'], borderRadius: 5 }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ y: {{ beginAtZero: true, ticks: {{ stepSize: 1 }} }} }}
  }}
}});

new Chart(document.getElementById('statusChart'), {{
  type: 'doughnut',
  data: {{
    labels: {json.dumps(status_labels)},
    datasets: [{{ data: {json.dumps(status_values)}, backgroundColor: {json.dumps(status_colors)},
                 borderWidth: 2, borderColor: '#fff' }}]
  }},
  options: {{ responsive: true, maintainAspectRatio: false, cutout: '60%',
             plugins: {{ legend: {{ position: 'bottom' }} }} }}
}});
</script>
</body>
</html>"""