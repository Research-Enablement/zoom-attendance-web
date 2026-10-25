"""
Computes all stats for one session page: attendee counts, new/returning,
org unit breakdown, status breakdown, duration tiers, and this session's
rank within the series. Produces plain numbers only -- no HTML, no charts.
"""

import os

import pandas as pd


def load_program_data(final_workbook_path, master_people_path, master_dept_path,
                      topics_path=None, program=None):
    """topics_path: the data folder's sessions.csv (columns: program, session_date,
    topic, presenter, length_minutes). program: only rows for this program are
    kept, matched case-insensitively against the program column.
    A missing department mapping file is treated as empty."""
    xl = pd.ExcelFile(final_workbook_path)
    attendance_log = xl.parse("attendance_log", dtype=str).fillna("")
    attendance_log["total_minutes"] = pd.to_numeric(attendance_log["total_minutes"], errors="coerce").fillna(0)

    sessions_summary = xl.parse("sessions_summary", dtype=str).fillna("")
    sessions_summary["total_attendees"] = pd.to_numeric(sessions_summary["total_attendees"], errors="coerce")
    sessions_summary["avg_duration_minutes"] = pd.to_numeric(sessions_summary["avg_duration_minutes"], errors="coerce")

    people = pd.read_csv(master_people_path, dtype=str).fillna("")
    if master_dept_path and os.path.exists(master_dept_path):
        dept = pd.read_csv(master_dept_path, dtype=str).fillna("")
    else:
        dept = pd.DataFrame(columns=["department", "parent_unit"])

    topics = None
    if topics_path:
        topics = pd.read_csv(topics_path, dtype=str).fillna("")
        topics = topics[topics["session_date"].str.strip() != ""].copy()  # ignore blank rows
        topics["session_date"] = _normalize_dates(topics["session_date"].str.strip())
        if program is not None:
            if "program" not in topics.columns:
                raise ValueError(f"{topics_path} has no 'program' column")
            topics = topics[topics["program"].str.strip().str.lower() == program.strip().lower()]

    return attendance_log, sessions_summary, people, dept, topics


_DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y"]


def _normalize_dates(series):
    """Excel silently reformats YYYY-MM-DD to M/D/YY (or similar) if a csv
    is ever opened and saved there. Normalize whatever shows up back to
    YYYY-MM-DD so a topic/exclusion lookup can't silently break."""
    parsed = pd.to_datetime(series, format=_DATE_FORMATS[0], errors="coerce")
    for fmt in _DATE_FORMATS[1:]:
        missing = parsed.isna()
        if not missing.any():
            break
        parsed = parsed.where(~missing, pd.to_datetime(series[missing], format=fmt, errors="coerce"))
    if parsed.isna().any():
        bad = series[parsed.isna()].tolist()
        raise ValueError(f"Could not parse session_date value(s), unrecognized format: {bad}")
    return parsed.dt.strftime("%Y-%m-%d")


_BLANK = {"", "unknown", "n/a", "na", "none", "-"}


def org_unit_for(department, dept_lookup):
    """The one rule for turning a person's department into the college/unit
    shown on charts. No department on file (or one typed as "unknown") -> "Unknown".
    A department whose parent unit is missing or blank falls back to the
    department's own name."""
    d = (department or "").strip()
    if d.lower() in _BLANK:
        return "Unknown"
    unit = (dept_lookup.get(d, d) or d).strip()
    return "Unknown" if unit.lower() in _BLANK else unit


def status_for(status):
    """Status as shown on charts: lowercase, and "unknown" when blank."""
    s = (status or "").strip().lower()
    return "unknown" if s in _BLANK else s


DEFAULT_LENGTH = 60


def tier_cutoffs(length_minutes):
    """(full, substantial) cutoffs in minutes. A 60 minute session gives 50 and 25,
    and other lengths scale the same way."""
    return int(length_minutes * 50 / 60 + 0.5), int(length_minutes * 25 / 60 + 0.5)


def _duration_tier(minutes, cutoffs):
    if minutes >= cutoffs[0]:
        return "full"
    if minutes >= cutoffs[1]:
        return "substantial"
    return "brief"


def compute_session_stats(session_date, attendance_log, sessions_summary, people, dept, topics=None):
    from new_returning import tag_new_returning, session_new_returning_counts

    session_rows = attendance_log[attendance_log["session_date"] == session_date].copy()
    if session_rows.empty:
        raise ValueError(f"No attendance rows found for session_date {session_date}")

    people_lookup = people.set_index("person_id")
    session_rows["department"] = session_rows["person_id"].map(
        lambda pid: people_lookup.at[pid, "department"] if pid in people_lookup.index else ""
    )
    session_rows["status"] = session_rows["person_id"].map(
        lambda pid: people_lookup.at[pid, "status"] if pid in people_lookup.index else ""
    )

    dept_lookup = dict(zip(dept["department"], dept["parent_unit"]))
    session_rows["org_unit"] = session_rows["department"].map(lambda d: org_unit_for(d, dept_lookup))
    session_rows["status_display"] = session_rows["status"].map(status_for)

    topic, presenter, length = "", "", DEFAULT_LENGTH
    if topics is not None:
        match = topics[topics["session_date"] == session_date]
        if not match.empty:
            topic = match.iloc[0].get("topic", "")
            presenter = match.iloc[0].get("presenter", "")
            saved = pd.to_numeric(match.iloc[0].get("length_minutes", ""), errors="coerce")
            if pd.notna(saved) and saved > 0:
                length = float(saved)
    cutoffs = tier_cutoffs(length)
    session_rows["duration_tier"] = session_rows["total_minutes"].apply(lambda m: _duration_tier(m, cutoffs))

    tagged = tag_new_returning(attendance_log)
    new_count, returning_count = session_new_returning_counts(tagged, session_date)

    row = sessions_summary[sessions_summary["session_date"] == session_date].iloc[0]
    total_attendees = int(row["total_attendees"])
    avg_duration = float(row["avg_duration_minutes"])
    series_avg_duration = float(sessions_summary["avg_duration_minutes"].mean())

    # Competition ranking: ties share the same rank (count of strictly-greater + 1),
    # matching how the old dashboard ranked sessions.
    rank = int((sessions_summary["total_attendees"] > total_attendees).sum()) + 1
    total_sessions = len(sessions_summary)
    sorted_summary = sessions_summary.sort_values("total_attendees", ascending=False).reset_index(drop=True)
    series_high = int(sorted_summary.iloc[0]["total_attendees"])
    series_high_date = sorted_summary.iloc[0]["session_date"]

    return {
        "session_date": session_date,
        "topic": topic,
        "presenter": presenter,
        "total_attendees": total_attendees,
        "avg_duration_minutes": avg_duration,
        "series_avg_duration_minutes": round(series_avg_duration, 1),
        "new_count": new_count,
        "returning_count": returning_count,
        "org_unit_counts": session_rows["org_unit"].value_counts().to_dict(),
        "status_counts": session_rows["status_display"].value_counts().to_dict(),
        "duration_tier_counts": session_rows["duration_tier"].value_counts().to_dict(),
        "length_minutes": round(length),
        "full_cutoff": cutoffs[0],
        "substantial_cutoff": cutoffs[1],
        "rank_by_attendance": rank,
        "total_sessions": total_sessions,
        "series_high_attendees": series_high,
        "series_high_date": series_high_date,
    }