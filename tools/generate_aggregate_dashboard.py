#!/usr/bin/env python3
"""
Builds one overview page across several programs from their final workbooks.
No individual names appear on the page.

Usage:
    python3 tools/generate_aggregate_dashboard.py \\
        --program "Data Workshop=workbooks/data_workshop_final.xlsx=data_workshop" \\
        --program "Coding Club=workbooks/coding_club_final.xlsx" \\
        --people-db master/master_people_db.csv --dept master/master_dept_to_parent.csv \\
        --topics sessions.csv --dept-name "Example Unit" --title "Program Attendance Overview" \\
        --out reports/aggregate.html

Each --program is NAME=WORKBOOK or NAME=WORKBOOK=FOLDER. NAME must match the
"program" column in sessions.csv. FOLDER (relative to the output file) is where
that program's session pages live; a session links to its page only if that
page file exists.
"""

import argparse
import html
import json
import os
import sys
from collections import Counter
from datetime import date as _date
from itertools import combinations

import pandas as pd

from new_returning import tag_new_returning
from session_stats import load_program_data, org_unit_for, status_for

BLUE = "#13294B"
ORANGE = "#E84A27"
PROGRAM_COLORS = [BLUE, ORANGE, "#5C9E6E", "#8B5CF6", "#2E82C5", "#F59E0B"]
STATUS_COLORS = {"staff": "#1D58A7", "student": ORANGE, "faculty": "#5C9E6E",
                 "postdoc": "#8B5CF6", "external": "#F59E0B", "unknown": "#6B7280"}
UNIT_PALETTE = [BLUE, "#1D58A7", "#2E82C5", ORANGE, "#5C9E6E", "#8B5CF6", "#EC4899",
                "#F59E0B", "#10B981", "#6366F1", "#D97706", "#14B8A6"]
UNKNOWN_COLOR = "#6B7280"
OTHER_COLOR = "#CBD5E1"
TOP_UNITS = 12


def parse_program_arg(text):
    parts = text.split("=")
    if len(parts) not in (2, 3) or not parts[0].strip() or not parts[1].strip():
        sys.exit(f"ERROR: --program must look like NAME=WORKBOOK or NAME=WORKBOOK=FOLDER, got: {text}")
    folder = parts[2].strip() if len(parts) == 3 and parts[2].strip() else None
    return parts[0].strip(), parts[1].strip(), folder


def _pretty_date(iso):
    d = _date.fromisoformat(iso)
    return f"{d:%b} {d.day}, {d.year}"


def _pct(part, whole):
    return round(100 * part / whole) if whole else 0


def _js(obj):
    return json.dumps(obj).replace("</", "<\\/")


def compute_program(name, folder, loaded, out_dir):
    log, summary, _people, _dept, topics = loaded
    tagged = tag_new_returning(log)

    topic_by_date = {}
    if topics is not None and not topics.empty:
        topic_by_date = dict(zip(topics["session_date"], topics["topic"]))

    summary_by_date = summary.set_index("session_date")
    warnings = []

    stray = sorted(set(log["session_date"]) - set(summary["session_date"]))
    if stray:
        warnings.append(f"{name}: attendance rows exist for dates missing from sessions_summary: {stray}")

    sessions = []
    for d in sorted(summary["session_date"]):
        g = tagged[tagged["session_date"] == d]
        attendees = len(g)
        avg = round(float(g["total_minutes"].mean()), 1) if attendees else None
        new = int(g["is_new"].sum())

        # Cross-check against the workbook's own summary so this page cannot
        # silently disagree with the per-session pages.
        s_att = int(summary_by_date.at[d, "total_attendees"])
        s_avg = float(summary_by_date.at[d, "avg_duration_minutes"])
        if s_att != attendees or (avg is not None and abs(s_avg - avg) > 0.05):
            warnings.append(f"{name} {d}: sessions_summary says {s_att} attendees / {s_avg} min, "
                            f"attendance_log gives {attendees} / {avg}")

        link = None
        if folder:
            rel = f"{folder}/session_{d}.html"
            if os.path.exists(os.path.join(out_dir, rel)):
                link = rel

        sessions.append({"date": d, "topic": topic_by_date.get(d, ""), "attendees": attendees,
                         "avg_minutes": avg, "new": new, "link": link})

    person_counts = dict(Counter(log["person_id"]))
    attendee_counts = [s["attendees"] for s in sessions]
    session_avgs = [s["avg_minutes"] for s in sessions if s["avg_minutes"] is not None]
    n_people = len(person_counts)
    returning_people = sum(1 for c in person_counts.values() if c >= 2)

    return {
        "name": name,
        "sessions": sessions,
        "person_counts": person_counts,
        "n_people": n_people,
        "total_attendances": int(sum(attendee_counts)),
        "n_sessions": len(sessions),
        "avg_attendance": round(sum(attendee_counts) / len(attendee_counts), 1) if attendee_counts else 0,
        "avg_minutes": round(sum(session_avgs) / len(session_avgs), 1) if session_avgs else 0,
        "repeat_pct": _pct(returning_people, n_people),
        "first_date": sessions[0]["date"] if sessions else None,
        "last_date": sessions[-1]["date"] if sessions else None,
        "warnings": warnings,
    }


def compute_aggregate(programs, people, dept):
    for i, p in enumerate(programs):
        p["color"] = PROGRAM_COLORS[i % len(PROGRAM_COLORS)]

    sets = {p["name"]: set(p["person_counts"]) for p in programs}
    all_people = set().union(*sets.values()) if sets else set()
    n_unique = len(all_people)

    total_per_person = Counter()
    programs_per_person = Counter()
    for p in programs:
        for pid, c in p["person_counts"].items():
            total_per_person[pid] += c
            programs_per_person[pid] += 1

    dist = Counter(programs_per_person.values())
    dist_rows = [(k, dist.get(k, 0)) for k in range(1, len(programs) + 1)]

    overlaps = [(a, b, len(sets[a] & sets[b])) for a, b in combinations(sets, 2)]

    dept_of = dict(zip(people["person_id"], people["department"]))
    status_of = dict(zip(people["person_id"], people["status"]))
    dept_lookup = dict(zip(dept["department"], dept["parent_unit"]))

    unit_counts = Counter(org_unit_for(dept_of.get(pid, ""), dept_lookup) for pid in all_people)
    status_counts = Counter(status_for(status_of.get(pid, "")) for pid in all_people)

    unknown_units = unit_counts.get("Unknown", 0)
    known = sorted(((u, c) for u, c in unit_counts.items() if u != "Unknown"),
                   key=lambda kv: (-kv[1], kv[0]))
    shown = known[:TOP_UNITS]
    rest = known[TOP_UNITS:]
    unit_rows = list(shown)
    if unknown_units:
        unit_rows.append(("Unknown", unknown_units))
    unit_rows.sort(key=lambda kv: (-kv[1], kv[0]))
    if rest:
        unit_rows.append((f"Other ({len(rest)} units)", sum(c for _, c in rest)))

    return {
        "programs": programs,
        "n_unique": n_unique,
        "total_attendances": sum(p["total_attendances"] for p in programs),
        "n_sessions": sum(p["n_sessions"] for p in programs),
        "returning_pct": _pct(sum(1 for c in total_per_person.values() if c >= 2), n_unique),
        "multi_program": sum(1 for c in programs_per_person.values() if c >= 2),
        "dist_rows": dist_rows,
        "overlaps": overlaps,
        "unit_rows": unit_rows,
        "status_rows": sorted(status_counts.items(), key=lambda kv: (-kv[1], kv[0])),
        "unknown_units": unknown_units,
        "warnings": [w for p in programs for w in p["warnings"]],
    }


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>@@PAGE_TITLE@@</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #f0f2f5; color: #333; font-size: 14px; }
  header { background: @@BLUE@@; color: #fff; padding: 24px 32px; border-bottom: 5px solid @@ORANGE@@; }
  .label { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.12em;
           color: @@ORANGE@@; font-weight: 700; margin-bottom: 6px; }
  header h1 { font-size: 1.7rem; font-weight: 800; line-height: 1.2; }
  .meta { margin-top: 8px; font-size: 0.82rem; opacity: 0.7; }
  .stats { display: flex; background: #fff; border-bottom: 1px solid #e5e5e5;
           box-shadow: 0 2px 6px rgba(0,0,0,.06); flex-wrap: wrap; }
  .stat { flex: 1; min-width: 150px; text-align: center; padding: 18px 8px 14px; border-right: 1px solid #eee; }
  .stat:last-child { border-right: none; }
  .stat .n { font-size: 2.2rem; font-weight: 900; color: @@BLUE@@; line-height: 1; }
  .stat .n.hi { color: @@ORANGE@@; }
  .stat .l { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.07em;
             color: #888; margin-top: 5px; font-weight: 600; }
  .stat .sub { font-size: 0.67rem; color: #bbb; margin-top: 3px; }
  .container { max-width: 1220px; margin: 0 auto; padding: 24px 20px; }
  .row { display: flex; gap: 20px; margin-bottom: 20px; flex-wrap: wrap; }
  .card { background: #fff; border-radius: 8px; padding: 20px;
          box-shadow: 0 1px 5px rgba(0,0,0,.07); flex: 1; min-width: 280px; }
  .card h2 { font-size: 0.75rem; font-weight: 700; color: #666; text-transform: uppercase;
             letter-spacing: 0.07em; border-left: 4px solid @@ORANGE@@; padding-left: 9px; margin-bottom: 16px; }
  .card.wide { flex: 2; }
  .note { font-size: 0.72rem; color: #999; margin-top: 10px; font-style: italic; }
  .ch-wrap { position: relative; }
  .h320 { height: 320px; } .h260 { height: 260px; } .h220 { height: 220px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  table.kv td { padding: 6px 0; border-bottom: 1px solid #f2f2f2; }
  table.kv td:last-child { text-align: right; font-weight: 700; color: @@BLUE@@; }
  table.sessions th { background: @@BLUE@@; color: #fff; padding: 9px 10px; text-align: left; font-size: 0.68rem;
                      text-transform: uppercase; letter-spacing: 0.05em; white-space: nowrap; }
  table.sessions td { padding: 8px 10px; border-bottom: 1px solid #f2f2f2; vertical-align: middle; }
  table.sessions td.num { text-align: right; white-space: nowrap; }
  table.sessions a { color: @@BLUE@@; font-weight: 600; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; }
  .tbl-wrap { overflow-x: auto; }
  footer { text-align: center; padding: 20px; color: #bbb; font-size: 0.72rem; }
</style>
</head>
<body>

<header>
  <div class="label">@@DEPT_NAME@@</div>
  <h1>@@TITLE@@</h1>
  <div class="meta">@@DATE_RANGE@@ &nbsp;&middot;&nbsp; generated @@GENERATED@@</div>
</header>

<div class="stats">
  <div class="stat"><div class="n hi">@@N_UNIQUE@@</div><div class="l">Unique People</div><div class="sub">across all programs</div></div>
  <div class="stat"><div class="n">@@TOTAL_ATT@@</div><div class="l">Total Attendances</div><div class="sub">a person counts once per session</div></div>
  <div class="stat"><div class="n">@@N_SESSIONS@@</div><div class="l">Sessions Held</div><div class="sub">@@N_PROGRAMS@@ programs</div></div>
  <div class="stat"><div class="n">@@RETURNING_PCT@@%</div><div class="l">Came Back</div><div class="sub">attended more than once</div></div>
  <div class="stat"><div class="n hi">@@MULTI@@</div><div class="l">In 2+ Programs</div><div class="sub">people who crossed over</div></div>
</div>

<div class="container">

  <div class="row">
@@PROGRAM_CARDS@@
  </div>

  <div class="row">
    <div class="card wide">
      <h2>Attendance per Session</h2>
      <div class="ch-wrap h320"><canvas id="attChart"></canvas></div>
    </div>
  </div>

  <div class="row">
    <div class="card">
      <h2>First-Time Share of Attendees</h2>
      <div class="ch-wrap h260"><canvas id="newChart"></canvas></div>
      <p class="note">First-time = first session ever attended within that same program.</p>
    </div>
    <div class="card">
      <h2>Average Minutes Stayed</h2>
      <div class="ch-wrap h260"><canvas id="durChart"></canvas></div>
    </div>
  </div>

  <div class="row">
    <div class="card">
      <h2>People Reached by Program</h2>
      <div class="ch-wrap h220"><canvas id="reachChart"></canvas></div>
    </div>
    <div class="card">
      <h2>How Many Programs Each Person Attended</h2>
      <div class="ch-wrap h220"><canvas id="distChart"></canvas></div>
      <p class="note">@@OVERLAP_NOTE@@</p>
    </div>
  </div>

  <div class="row">
    <div class="card wide">
      <h2>Unique People by College / Unit</h2>
      <div class="ch-wrap h320"><canvas id="unitChart"></canvas></div>
      <p class="note">@@UNKNOWN_NOTE@@</p>
    </div>
    <div class="card">
      <h2>People by Status</h2>
      <div class="ch-wrap h260"><canvas id="statusChart"></canvas></div>
    </div>
  </div>

  <div class="row">
    <div class="card">
      <h2>All Sessions</h2>
      <div class="tbl-wrap">
        <table class="sessions">
          <thead><tr><th>Program</th><th>Date</th><th>Topic</th><th>Attendees</th><th>Avg Min</th><th>First-Time</th></tr></thead>
          <tbody>
@@SESSION_ROWS@@
          </tbody>
        </table>
      </div>
    </div>
  </div>

</div>

<footer>@@FOOTER@@</footer>

<script>
const LABELS = @@LABELS@@;
const PROGRAMS = @@PROGRAMS@@;

function lineChart(id, key, yTitle, yMax) {
  const yScale = { beginAtZero: true, title: { display: true, text: yTitle } };
  if (yMax) { yScale.max = yMax; }
  new Chart(document.getElementById(id), {
    type: 'line',
    data: { labels: LABELS, datasets: PROGRAMS.map(p => ({
      label: p.name, data: p[key], borderColor: p.color, backgroundColor: p.color,
      spanGaps: true, tension: 0, pointRadius: 4, borderWidth: 2 })) },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom' } },
      scales: { y: yScale, x: { ticks: { maxRotation: 60, minRotation: 45 }, grid: { display: false } } } }
  });
}
lineChart('attChart', 'attendees', 'Attendees', 0);
lineChart('newChart', 'newShare', '% first-time', 100);
lineChart('durChart', 'avgMin', 'Minutes', 0);

new Chart(document.getElementById('reachChart'), {
  type: 'bar',
  data: { labels: @@REACH_LABELS@@,
          datasets: [{ data: @@REACH_VALUES@@, backgroundColor: @@REACH_COLORS@@, borderRadius: 5 }] },
  options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
             scales: { y: { beginAtZero: true, title: { display: true, text: 'Unique people' } },
                       x: { grid: { display: false } } } }
});

new Chart(document.getElementById('distChart'), {
  type: 'doughnut',
  data: { labels: @@DIST_LABELS@@,
          datasets: [{ data: @@DIST_VALUES@@, backgroundColor: @@DIST_COLORS@@, borderWidth: 2, borderColor: '#fff' }] },
  options: { responsive: true, maintainAspectRatio: false, cutout: '60%', plugins: { legend: { position: 'bottom' } } }
});

new Chart(document.getElementById('unitChart'), {
  type: 'bar',
  data: { labels: @@UNIT_LABELS@@,
          datasets: [{ data: @@UNIT_VALUES@@, backgroundColor: @@UNIT_COLORS@@, borderRadius: 4 }] },
  options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
             scales: { x: { ticks: { stepSize: 1 }, title: { display: true, text: 'Unique people' } },
                       y: { grid: { display: false } } } }
});

new Chart(document.getElementById('statusChart'), {
  type: 'doughnut',
  data: { labels: @@STATUS_LABELS@@,
          datasets: [{ data: @@STATUS_VALUES@@, backgroundColor: @@STATUS_COLORS@@, borderWidth: 2, borderColor: '#fff' }] },
  options: { responsive: true, maintainAspectRatio: false, cutout: '60%', plugins: { legend: { position: 'bottom' } } }
});
</script>
</body>
</html>
"""


def _program_card(p):
    esc = html.escape
    dates = (f"{_pretty_date(p['first_date'])} to {_pretty_date(p['last_date'])}"
             if p["first_date"] else "no sessions")
    rows = [("Sessions", p["n_sessions"]), ("Unique people", p["n_people"]),
            ("Avg attendees per session", p["avg_attendance"]), ("Avg minutes stayed", p["avg_minutes"]),
            ("Came back (2+ sessions)", f"{p['repeat_pct']}%"), ("Dates", dates)]
    body = "".join(f"<tr><td>{esc(str(k))}</td><td>{esc(str(v))}</td></tr>" for k, v in rows)
    return (f'    <div class="card" style="border-top: 4px solid {p["color"]};">\n'
            f'      <h2 style="border-left-color: {p["color"]};">{esc(p["name"])}</h2>\n'
            f'      <table class="kv">{body}</table>\n    </div>')


def _session_rows(programs):
    esc = html.escape
    rows = []
    for p in programs:
        for s in p["sessions"]:
            rows.append((s["date"], p, s))
    rows.sort(key=lambda r: (r[0], r[1]["name"]), reverse=True)
    out = []
    for iso, p, s in rows:
        date_cell = esc(iso)
        if s["link"]:
            date_cell = f'<a href="{esc(s["link"], quote=True)}">{esc(iso)}</a>'
        avg = "" if s["avg_minutes"] is None else s["avg_minutes"]
        out.append(
            f'            <tr><td><span class="dot" style="background:{p["color"]};"></span>{esc(p["name"])}</td>'
            f'<td>{date_cell}</td><td>{esc(s["topic"])}</td>'
            f'<td class="num">{s["attendees"]}</td><td class="num">{avg}</td><td class="num">{s["new"]}</td></tr>')
    return "\n".join(out)


def render_aggregate(agg, generated_on, dept_name="", title="Program Attendance Overview"):
    programs = agg["programs"]
    esc = html.escape

    dates = sorted({s["date"] for p in programs for s in p["sessions"]})
    labels = [_pretty_date(d) for d in dates]

    prog_js = []
    for p in programs:
        by = {s["date"]: s for s in p["sessions"]}
        prog_js.append({
            "name": p["name"], "color": p["color"],
            "attendees": [by[d]["attendees"] if d in by else None for d in dates],
            "newShare": [_pct(by[d]["new"], by[d]["attendees"]) if d in by and by[d]["attendees"] else None for d in dates],
            "avgMin": [by[d]["avg_minutes"] if d in by else None for d in dates],
        })

    dist_labels = [f"{k} program{'s' if k != 1 else ''}" for k, _ in agg["dist_rows"]]
    dist_values = [v for _, v in agg["dist_rows"]]
    dist_colors = [PROGRAM_COLORS[i % len(PROGRAM_COLORS)] for i in range(len(dist_values))]

    if agg["overlaps"]:
        overlap_note = "Shared between programs: " + "; ".join(
            f"{a} and {b}: {n}" for a, b, n in agg["overlaps"]) + "."
    else:
        overlap_note = "Only one program included."

    unit_labels = [u for u, _ in agg["unit_rows"]]
    unit_values = [c for _, c in agg["unit_rows"]]
    unit_colors = []
    palette_i = 0
    for u in unit_labels:
        if u == "Unknown":
            unit_colors.append(UNKNOWN_COLOR)
        elif u.startswith("Other ("):
            unit_colors.append(OTHER_COLOR)
        else:
            unit_colors.append(UNIT_PALETTE[palette_i % len(UNIT_PALETTE)])
            palette_i += 1

    unknown_note = (f"{agg['unknown_units']} of {agg['n_unique']} people "
                    f"({_pct(agg['unknown_units'], agg['n_unique'])}%) have no department on file and are shown as Unknown.")

    first = min((p["first_date"] for p in programs if p["first_date"]), default=None)
    last = max((p["last_date"] for p in programs if p["last_date"]), default=None)
    date_range = f"{_pretty_date(first)} to {_pretty_date(last)}" if first else "no sessions"

    status_labels = [s for s, _ in agg["status_rows"]]
    status_values = [c for _, c in agg["status_rows"]]
    status_colors = [STATUS_COLORS.get(s, "#999999") for s in status_labels]

    footer = " &nbsp;&middot;&nbsp; ".join(esc(x) for x in ("University of Illinois Urbana-Champaign", dept_name) if x)
    tokens = {
        "BLUE": BLUE, "ORANGE": ORANGE,
        "PAGE_TITLE": esc(f"{dept_name}: {title}" if dept_name else title),
        "DEPT_NAME": esc(dept_name), "TITLE": esc(title),
        "FOOTER": footer + " &nbsp;&middot;&nbsp; no individual names are shown on this page",
        "DATE_RANGE": esc(date_range), "GENERATED": esc(generated_on),
        "N_UNIQUE": agg["n_unique"], "TOTAL_ATT": agg["total_attendances"],
        "N_SESSIONS": agg["n_sessions"], "N_PROGRAMS": len(programs),
        "RETURNING_PCT": agg["returning_pct"], "MULTI": agg["multi_program"],
        "PROGRAM_CARDS": "\n".join(_program_card(p) for p in programs),
        "SESSION_ROWS": _session_rows(programs),
        "OVERLAP_NOTE": esc(overlap_note), "UNKNOWN_NOTE": esc(unknown_note),
        "LABELS": _js(labels), "PROGRAMS": _js(prog_js),
        "REACH_LABELS": _js([p["name"] for p in programs]),
        "REACH_VALUES": _js([p["n_people"] for p in programs]),
        "REACH_COLORS": _js([p["color"] for p in programs]),
        "DIST_LABELS": _js(dist_labels), "DIST_VALUES": _js(dist_values), "DIST_COLORS": _js(dist_colors),
        "UNIT_LABELS": _js(unit_labels), "UNIT_VALUES": _js(unit_values), "UNIT_COLORS": _js(unit_colors),
        "STATUS_LABELS": _js(status_labels), "STATUS_VALUES": _js(status_values), "STATUS_COLORS": _js(status_colors),
    }
    page = TEMPLATE
    for key, value in tokens.items():
        page = page.replace(f"@@{key}@@", str(value))
    return page


def main():
    parser = argparse.ArgumentParser(description="Build the cross-program attendance overview page")
    parser.add_argument("--program", action="append", required=True,
                        help='NAME=WORKBOOK or NAME=WORKBOOK=FOLDER (repeat once per program)')
    parser.add_argument("--people-db", required=True)
    parser.add_argument("--dept", required=True)
    parser.add_argument("--topics", default=None)
    parser.add_argument("--out", required=True)
    parser.add_argument("--dept-name", default="", help="Shown above the title and in the footer")
    parser.add_argument("--title", default="Program Attendance Overview")
    args = parser.parse_args()

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)

    programs, people, dept = [], None, None
    for spec in args.program:
        name, workbook, folder = parse_program_arg(spec)
        loaded = load_program_data(workbook, args.people_db, args.dept, args.topics, program=name)
        if people is None:
            people, dept = loaded[2], loaded[3]
        programs.append(compute_program(name, folder, loaded, out_dir))

    agg = compute_aggregate(programs, people, dept)
    page = render_aggregate(agg, _date.today().strftime("%b %d, %Y").replace(" 0", " "),
                            dept_name=args.dept_name, title=args.title)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(page)

    print(f"Wrote {args.out}")
    print(f"  {len(programs)} programs, {agg['n_sessions']} sessions, {agg['n_unique']} unique people, "
          f"{agg['total_attendances']} attendances")
    if agg["warnings"]:
        print("\nNOTE: the overview disagrees with a workbook's own sessions_summary in these places:")
        for w in agg["warnings"]:
            print(f"  {w}")


if __name__ == "__main__":
    main()