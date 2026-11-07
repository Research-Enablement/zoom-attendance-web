"""Internal per-session page: full named attendee roster. Never publish."""

import html

BLUE = "#13294B"
ORANGE = "#E84A27"


def build_roster(session_date, attendance_log, people, dept):
    from new_returning import tag_new_returning

    tagged = tag_new_returning(attendance_log)
    rows = tagged[tagged["session_date"] == session_date]
    sessions_attended = attendance_log[attendance_log["person_id"] != ""].groupby("person_id").size().to_dict()

    people_lookup = people.set_index("person_id")
    dept_lookup = dict(zip(dept["department"], dept["parent_unit"]))  # empty if no mapping file

    roster = []
    for _, r in rows.iterrows():
        pid = r["person_id"]
        known = pid in people_lookup.index
        name = (people_lookup.at[pid, "name"] if known else "").strip() or r["name"].strip() or "(no name)"
        department = people_lookup.at[pid, "department"].strip() if known else ""
        unit = (dept_lookup.get(department, department) or department) if department else "Unknown"
        status = (people_lookup.at[pid, "status"].strip() if known else "") or "unknown"
        if not pid:
            attendance = "unresolved"
        else:
            attendance = "new" if r["is_new"] else "returning"
        roster.append({
            "name": name, "department": department, "unit": unit, "status": status,
            "attendance": attendance, "minutes": float(r["total_minutes"]),
            "sessions": sessions_attended.get(pid, 0),
        })
    roster.sort(key=lambda x: (-x["minutes"], x["name"].lower()))
    return roster


def _row_html(i, p, max_minutes):
    width = round(100 * p["minutes"] / max_minutes) if max_minutes else 0
    badge = {"new": '<span class="badge b-new">New</span>',
             "returning": '<span class="badge b-ret">Returning</span>',
             "unresolved": '<span class="badge b-unknown">Unresolved</span>'}[p["attendance"]]
    sessions = (f'<span class="sessions-n">{p["sessions"]} sessions</span>'
                if p["attendance"] == "returning" else "")
    status_class = {"staff": "b-staff", "faculty": "b-faculty", "student": "b-student"}.get(p["status"], "b-unknown")
    e = html.escape
    return f"""<tr>
  <td class="rank">{i}</td>
  <td class="name-cell">{e(p['name'])}</td>
  <td class="dept-cell">{e(p['department']) or '&mdash;'}</td>
  <td><span class="badge b-unit">{e(p['unit'])}</span></td>
  <td><span class="badge {status_class}">{e(p['status'])}</span></td>
  <td>{badge}{sessions}</td>
  <td><div class="dur-wrap"><div class="dur-bar-bg"><div class="dur-bar" style="width:{width}%;background:{BLUE}"></div></div><span class="dur-val">{p['minutes']:g} min</span></div></td>
</tr>"""


def render_internal_page(stats, roster, program_name, session_number=None, total_in_series=None, dept_name=""):
    from render_session_page import render_session_page

    max_minutes = max((p["minutes"] for p in roster), default=0)
    body = "\n".join(_row_html(i, p, max_minutes) for i, p in enumerate(roster, 1))
    card = f"""
  <div class="row">
    <div class="card full">
      <h2>Full Attendee Roster &mdash; Sorted by Time in Session</h2>
      <div class="tbl-wrap"><table>
        <thead><tr><th>#</th><th>Name</th><th>Department</th><th>Unit / College</th><th>Status</th><th>Attendance</th><th>Time in Session</th></tr></thead>
        <tbody>
{body}
        </tbody>
      </table></div>
    </div>
  </div>
"""
    return render_session_page(stats, program_name, session_number, total_in_series,
                               roster_section=card, internal=True, dept_name=dept_name)