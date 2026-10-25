"""
Browser adapter for the zoom-attendance-template pipeline.

Runs inside Pyodide, or in plain Python for tests. Conversion, matching,
QC, stats and page rendering are done by the pipeline's own code in tools/,
called as-is. This module only keeps the history workbook in memory, hands
the pipeline the files it expects, and turns its sys.exit() errors into
readable messages.

Adding a session takes two calls, so a person can match attendees in between:
    prepare_session  convert the Zoom file, match everyone it can, list the rest
    commit_session   apply the person's choices, compile, add to the history

History workbook sheets (one file holds every meeting type):
    settings        key, value (dept_name, title, ...)
    meeting_types   name, topics, presenters, length, hidden, notes
    people_db       the pipeline's people registry columns
    dept_to_parent  department, parent_unit
    name_aliases    zoom_display_name, person_id, notes
    sessions        program, session_date, topic, presenter, length_minutes
    attendance_log  program + the pipeline's attendance_log columns
A history file from the earlier experiment (no settings sheet, no meeting
type options) still loads; the missing values get defaults.
"""

import contextlib
import difflib
import io
import json
import os
import tempfile
from datetime import date as _date

import pandas as pd

import init_workbook as iw
import normalize_to_participants_report as nz
import recompile_workbook as rw
from generate_aggregate_dashboard import compute_program, compute_aggregate, render_aggregate
from render_session_page import render_session_page
from session_stats import load_program_data, compute_session_stats

FORMAT_VERSION = "2"
DEFAULT_TITLE = "Program Attendance Overview"
DEFAULT_LENGTH = 60
STATUSES = ["staff", "student", "faculty", "postdoc", "external"]
SEED_DEPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "master_dept_to_parent.csv")

TYPE_COLS = ["name", "topics", "presenters", "length", "hidden", "notes"]
TOPIC_COLS = ["program", "session_date", "topic", "presenter", "length_minutes"]
LOG_COLS = ["program", "session_month", "session_date", "person_id", "name", "email", "total_minutes"]
SHEETS = {
    "meeting_types": TYPE_COLS,
    "people_db": iw.PEOPLE_COLS,
    "dept_to_parent": iw.DEPT_COLS,
    "name_aliases": iw.ALIAS_COLS,
    "sessions": TOPIC_COLS,
    "attendance_log": LOG_COLS,
}
# QC sections that are information only; anything else stops the save.
NOTE_ONLY = ("no parent_unit", "aliases learned")


class PipelineError(Exception):
    pass


@contextlib.contextmanager
def _pipeline_call():
    """The pipeline stops with sys.exit("ERROR: ...") and prints progress. Catch both."""
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            yield out
    except SystemExit as e:
        raise PipelineError(str(e.code).replace("ERROR: ", "")) from None


def _columns(df, cols):
    df = df.copy()
    for col in cols:
        if col not in df.columns:
            df[col] = ""
    return df[cols]


def _flag(value, default):
    text = str(value).strip().lower()
    return default if text == "" else text in ("yes", "true", "1", "y")


def _yn(value):
    return "yes" if value else "no"


# ---------------------------------------------------------------- history

def new_history(dept_name="", title=""):
    h = {name: pd.DataFrame(columns=cols) for name, cols in SHEETS.items()}
    if os.path.exists(SEED_DEPT):
        h["dept_to_parent"] = _columns(pd.read_csv(SEED_DEPT, dtype=str).fillna(""), iw.DEPT_COLS)
    h["settings"] = {"dept_name": dept_name.strip(), "title": title.strip() or DEFAULT_TITLE}
    return h


def load_history(path):
    xl = pd.ExcelFile(path)
    missing = [s for s in SHEETS if s not in xl.sheet_names]
    if missing:
        raise PipelineError("This doesn't look like an attendance history file "
                            f"(missing sheets: {', '.join(missing)}).")
    h = {name: _columns(xl.parse(name, dtype=str).fillna(""), cols) for name, cols in SHEETS.items()}
    h["attendance_log"]["total_minutes"] = pd.to_numeric(h["attendance_log"]["total_minutes"],
                                                         errors="coerce").fillna(0)
    types = h["meeting_types"]
    types["topics"] = types["topics"].map(lambda v: _yn(_flag(v, True)))
    types["presenters"] = types["presenters"].map(lambda v: _yn(_flag(v, True)))
    types["hidden"] = types["hidden"].map(lambda v: _yn(_flag(v, False)))
    types["length"] = types["length"].map(lambda v: v if str(v).strip().isdigit() else str(DEFAULT_LENGTH))
    settings = {}
    if "settings" in xl.sheet_names:
        s = xl.parse("settings", dtype=str).fillna("")
        if {"key", "value"} <= set(s.columns):
            settings = dict(zip(s["key"], s["value"]))
    h["settings"] = {"dept_name": settings.get("dept_name", ""), "title": settings.get("title") or DEFAULT_TITLE}
    return h


def save_history(h, path):
    rows = {**h["settings"], "format_version": FORMAT_VERSION, "saved_on": _date.today().isoformat(),
            "made_by": "zoom-attendance-web"}
    settings = pd.DataFrame({"key": list(rows), "value": list(rows.values())})
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        settings.to_excel(writer, sheet_name="settings", index=False)
        for name in SHEETS:
            h[name].to_excel(writer, sheet_name=name, index=False)


def set_settings(h, dept_name, title):
    h["settings"] = {"dept_name": dept_name.strip(), "title": title.strip() or DEFAULT_TITLE}


def _type_index(h, name):
    match = h["meeting_types"].index[h["meeting_types"]["name"] == name]
    if not len(match):
        raise PipelineError(f"Unknown meeting type '{name}'.")
    return match[0]


def _name_taken(h, name, skip=None):
    names = h["meeting_types"]["name"]
    if skip is not None:
        names = names[names != skip]
    return name.strip().lower() in set(names.str.strip().str.lower())


def add_meeting_type(h, name, topics=True, presenters=True):
    name = name.strip()
    if not name:
        raise PipelineError("Type a name for the meeting type.")
    if _name_taken(h, name):
        raise PipelineError(f"There is already a meeting type called '{name}' (it may be hidden).")
    row = {"name": name, "topics": _yn(topics), "presenters": _yn(presenters),
           "length": str(DEFAULT_LENGTH), "hidden": "no", "notes": ""}
    h["meeting_types"] = pd.concat([h["meeting_types"], pd.DataFrame([row])], ignore_index=True)


def update_meeting_type(h, name, new_name, topics, presenters, hidden):
    """Rename and change options. A rename also moves its sessions and topics to the new name."""
    i = _type_index(h, name)
    new_name = new_name.strip()
    if not new_name:
        raise PipelineError("Type a name for the meeting type.")
    if new_name != name and _name_taken(h, new_name, skip=name):
        raise PipelineError(f"There is already a meeting type called '{new_name}'.")
    types = h["meeting_types"]
    types.at[i, "name"] = new_name
    types.at[i, "topics"], types.at[i, "presenters"] = _yn(topics), _yn(presenters)
    types.at[i, "hidden"] = _yn(hidden)
    if new_name != name:
        for sheet in ("attendance_log", "sessions"):
            df = h[sheet]
            df.loc[df["program"] == name, "program"] = new_name


def _type_row(h, name):
    return h["meeting_types"].loc[_type_index(h, name)]


def summary(h):
    log = h["attendance_log"]
    types = []
    for _, t in h["meeting_types"].iterrows():
        rows = log[log["program"] == t["name"]]
        dates = sorted(rows["session_date"].unique())
        types.append({"name": t["name"], "topics": _flag(t["topics"], True),
                      "presenters": _flag(t["presenters"], True), "hidden": _flag(t["hidden"], False),
                      "length": int(t["length"]), "sessions": len(dates),
                      "people": int(rows["person_id"].nunique()),
                      "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
                      "dates": dates})
    return {"types": types, "people": len(h["people_db"]), "aliases": len(h["name_aliases"]),
            "settings": h["settings"]}


# ---------------------------------------------------------------- adding a session

def _convert(csv_path, tmp):
    """Any of the Zoom exports the pipeline knows -> Participants-report shape."""
    with _pipeline_call():
        rows = nz.read_raw_rows(csv_path)
        if not rows:
            raise SystemExit("The file is empty.")
        shape = nz.detect_shape(rows)
        header = [c.strip() for c in rows[0]]
        if shape == "meetinglistdetails" and "Name (original name)" not in header:
            shape = "two_section"  # a meeting-info block above the participant table, not a meeting list
        if shape == "meetinglistdetails":
            out = nz.convert_meetinglistdetails(csv_path)
        elif shape == "flat_participants":
            out = nz.convert_flat_participants(csv_path)
        else:
            out = nz.convert_two_section(csv_path)
    converted = os.path.join(tmp, "converted.csv")
    out[nz.OUT_COLS].to_csv(converted, index=False)
    approx = int((out["Notes"] == "approximated_from_usage_report").sum())
    return converted, shape, approx


def _people_choices(people_df):
    rows = people_df[people_df["person_id"].str.strip() != ""]
    return [{"id": r["person_id"], "name": r["name"], "email": r["email"], "department": r["department"]}
            for _, r in rows.sort_values("name", key=lambda s: s.str.lower()).iterrows()]


def _suggest(name, people_df, limit=3):
    """People whose name looks like this Zoom name, best first. Only a hint; nothing is assigned."""
    target = name.strip().lower()
    if not target:
        return []
    scored = []
    for _, r in people_df.iterrows():
        candidate = r["name"].strip().lower()
        if not candidate or not r["person_id"]:
            continue
        score = difflib.SequenceMatcher(None, target, candidate).ratio()
        if set(target.split()) & set(candidate.split()):
            score += 0.2
        if score >= 0.6:
            scored.append((score, r["person_id"]))
    return [pid for _, pid in sorted(scored, reverse=True)[:limit]]


def prepare_session(h, csv_path, program, replace=False):
    """Convert and match. Nothing is added to the history until commit_session."""
    t = _type_row(h, program)
    display = os.path.basename(csv_path)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            converted, shape, approx = _convert(csv_path, tmp)
            with _pipeline_call():
                session_df, session_date = iw.load_raw_session(converted)
        except PipelineError as e:
            text = str(e).replace(csv_path, display)
            raise PipelineError(f"{display} could not be read: {text} "
                                "Check that it is one of the Zoom attendance exports listed above.") from None
    if session_date is None:
        raise PipelineError("Could not find a session date in the file's Join time column.")

    log = h["attendance_log"]
    already = bool(((log["program"] == program) & (log["session_date"] == session_date)).any())
    if already and not replace:
        raise PipelineError(f"{program} already has a session on {session_date}. "
                            "Tick 'Replace' to load this file over it.")

    people_df = iw.assign_ids_to_people_db(h["people_db"])
    notes = []
    with _pipeline_call():
        resolved_df, new_people = iw.resolve_session(session_df, people_df, h["name_aliases"], notes)
    if new_people:
        people_df = pd.concat([people_df, pd.DataFrame(new_people)], ignore_index=True)
    resolved_df = resolved_df.reset_index(drop=True)

    # One question per distinct Zoom name (someone who reconnected shows up on several rows).
    groups = {}
    for idx, row in resolved_df[resolved_df["person_id"].str.strip() == ""].iterrows():
        name = row["Name (original name)"].strip()
        key = name if name else f"(blank name, row {idx + 1})"
        g = groups.setdefault(key, {"key": key, "zoom_name": name, "rows": [], "minutes": 0.0})
        g["rows"].append(int(idx))
        g["minutes"] += float(row["Total duration (minutes)"])
    unmatched = [{**g, "minutes": round(g["minutes"], 1), "suggestions": _suggest(g["zoom_name"], people_df)}
                 for g in groups.values()]

    topics = h["sessions"]
    prior = topics[(topics["program"] == program) & (topics["session_date"] == session_date)]
    prior = prior.iloc[0] if not prior.empty else None

    h["_pending"] = {"program": program, "session_date": session_date, "resolved": resolved_df,
                     "people": people_df, "replaced": already, "groups": groups}
    return {
        "program": program, "session_date": session_date, "replaced": already,
        "shape": shape, "approximated": approx, "rows": len(resolved_df),
        "new_people": [{"id": p["person_id"], "name": p["name"], "email": p["email"]} for p in new_people],
        "unmatched": unmatched,
        "people": _people_choices(people_df),
        "departments": sorted((set(h["dept_to_parent"]["department"]) | set(people_df["department"])) - {""},
                              key=str.lower),
        "statuses": sorted((set(STATUSES) | set(people_df["status"])) - {""}),
        "asks": {"topic": _flag(t["topics"], True), "presenter": _flag(t["presenters"], True)},
        "defaults": {"topic": prior["topic"] if prior is not None else "",
                     "presenter": prior["presenter"] if prior is not None else "",
                     "length": (prior["length_minutes"] if prior is not None and prior["length_minutes"]
                                else str(t["length"]))},
    }


def cancel_session(h):
    h.pop("_pending", None)


def commit_session(h, choices):
    """choices: {"match": {group key: person_id or {"new": {name, department, status}}},
                 "details": {person_id: {department, status}}  (optional, for new people),
                 "topic", "presenter", "length"}"""
    p = h.get("_pending")
    if not p:
        raise PipelineError("Read a Zoom file first.")
    program, session_date = p["program"], p["session_date"]
    resolved, people_df = p["resolved"].copy(), p["people"].copy()
    match = choices.get("match", {})

    missing = [k for k in p["groups"] if not match.get(k)]
    if missing:
        raise PipelineError(f"Match or add every attendee first. Still open: {', '.join(missing)}.")

    known_ids = set(people_df["person_id"]) - {""}
    for key, choice in match.items():
        if key not in p["groups"]:
            continue
        if isinstance(choice, dict):
            info = choice.get("new", {})
            name = str(info.get("name", "")).strip()
            if not name:
                raise PipelineError(f"Type a name for the new person added for '{key}'.")
            pid = iw.next_person_id(known_ids)
            known_ids.add(pid)
            people_df = pd.concat([people_df, pd.DataFrame([{
                "person_id": pid, "email": "", "name": name,
                "department": str(info.get("department", "")).strip(),
                "status": str(info.get("status", "")).strip(), "title": "",
                "lookup_source": "added_in_web_page", "notes": ""}])], ignore_index=True)
        else:
            pid = str(choice).strip()
            if pid not in known_ids:
                raise PipelineError(f"'{key}' was matched to {pid}, who is not in the people list.")
        for idx in p["groups"][key]["rows"]:
            resolved.at[idx, "person_id"] = pid

    for pid, info in (choices.get("details") or {}).items():
        rows = people_df.index[people_df["person_id"] == pid]
        for col in ("department", "status"):
            value = str(info.get(col, "")).strip()
            if value and len(rows):
                people_df.loc[rows, col] = value

    length = str(choices.get("length", "")).strip()
    if not length.isdigit() or int(length) <= 0:
        raise PipelineError("Type the meeting length as a whole number of minutes, like 60.")

    qc = rw.QCReport()
    alias_df = h["name_aliases"]
    with tempfile.TemporaryDirectory() as tmp:
        cleanup = os.path.join(tmp, "cleanup.xlsx")
        with pd.ExcelWriter(cleanup, engine="openpyxl") as writer:
            people_df.to_excel(writer, sheet_name="people_db", index=False)
            h["dept_to_parent"].to_excel(writer, sheet_name="dept_to_parent", index=False)
            alias_df.to_excel(writer, sheet_name="name_aliases", index=False)
            resolved.to_excel(writer, sheet_name=session_date, index=False)
        xl = pd.ExcelFile(cleanup)
        with _pipeline_call():
            people_df = rw.load_people_db(xl, qc)
            rw.load_dept_to_parent(xl, qc)
            attendance, metrics = rw.process_session(xl, session_date, set(people_df["person_id"]), qc)
            raw = xl.parse(session_date, dtype=str).fillna("")
            alias_df = rw.harvest_new_aliases({session_date: raw}, people_df, alias_df, qc)

    blocking = [title for title, _ in qc.sections if not any(k in title.lower() for k in NOTE_ONLY)]
    if blocking or attendance is None:
        raise PipelineError("The session was not saved because of these problems:\n" + qc.render())

    attendance = attendance.rename(columns={"Name (original name)": "name", "Email": "email"})
    attendance["program"] = program
    log = h["attendance_log"]
    same = (log["program"] == program) & (log["session_date"] == session_date)
    log = pd.concat([log[~same], attendance[LOG_COLS]], ignore_index=True)
    # concat onto an empty frame leaves an object column in older pandas (Pyodide)
    log["total_minutes"] = pd.to_numeric(log["total_minutes"], errors="coerce").fillna(0)
    h["attendance_log"], h["people_db"], h["name_aliases"] = log, people_df, alias_df

    t = _type_row(h, program)
    topics = h["sessions"]
    same = (topics["program"] == program) & (topics["session_date"] == session_date)
    row = {"program": program, "session_date": session_date,
           "topic": str(choices.get("topic", "")).strip() if _flag(t["topics"], True) else "",
           "presenter": str(choices.get("presenter", "")).strip() if _flag(t["presenters"], True) else "",
           "length_minutes": length}
    h["sessions"] = pd.concat([topics[~same], pd.DataFrame([row])], ignore_index=True)
    h["meeting_types"].at[_type_index(h, program), "length"] = length
    h.pop("_pending", None)

    notes = [f"{title} ({len(items)}): {', '.join(items)}" for title, items in qc.sections]
    return {"program": program, "session_date": session_date, "replaced": p["replaced"],
            "attendees": metrics["total_attendees"], "avg_minutes": metrics["avg_duration_minutes"],
            "notes": notes}


# ---------------------------------------------------------------- pages

def _sessions_summary(program_log):
    minutes = pd.to_numeric(program_log["total_minutes"], errors="coerce").fillna(0)
    g = minutes.groupby(program_log["session_date"])
    out = pd.DataFrame({"total_attendees": g.size(), "avg_duration_minutes": g.mean().round(1)}).reset_index()
    out["session_month"] = out["session_date"].str[:7].str.replace("-", "_")
    return out[["session_month", "session_date", "total_attendees", "avg_duration_minutes"]]


def _write_program_inputs(h, program, tmp):
    """Write the files the page code reads: a final workbook for this program,
    the people and department lists, and sessions.csv."""
    log = h["attendance_log"]
    program_log = log[log["program"] == program].drop(columns=["program"])
    if program_log.empty:
        raise PipelineError(f"{program} has no sessions in this history file yet.")
    workbook = os.path.join(tmp, f"{_type_index(h, program)}_final.xlsx")
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        program_log.to_excel(writer, sheet_name="attendance_log", index=False)
        _sessions_summary(program_log).to_excel(writer, sheet_name="sessions_summary", index=False)
    paths = {"workbook": workbook,
             "people": os.path.join(tmp, "master_people_db.csv"),
             "dept": os.path.join(tmp, "master_dept_to_parent.csv"),
             "topics": os.path.join(tmp, "sessions.csv")}
    h["people_db"].to_csv(paths["people"], index=False)
    h["dept_to_parent"].to_csv(paths["dept"], index=False)
    h["sessions"].to_csv(paths["topics"], index=False)
    return paths


def session_page(h, program, session_date):
    t = _type_row(h, program)
    with tempfile.TemporaryDirectory() as tmp, _pipeline_call():
        p = _write_program_inputs(h, program, tmp)
        log, summ, people, dept, topics = load_program_data(p["workbook"], p["people"], p["dept"],
                                                            p["topics"], program=program)
        all_dates = sorted(summ["session_date"].tolist())
        stats = compute_session_stats(session_date, log, summ, people, dept, topics)
        if not _flag(t["topics"], True):
            stats["topic"] = ""
        if not _flag(t["presenters"], True):
            stats["presenter"] = ""
        return render_session_page(stats, program, session_number=all_dates.index(session_date) + 1,
                                   total_in_series=len(all_dates), dept_name=h["settings"]["dept_name"])


def combined_dashboard(h, programs):
    if not programs:
        raise PipelineError("Pick at least one meeting type.")
    with tempfile.TemporaryDirectory() as tmp, _pipeline_call():
        computed, people, dept = [], None, None
        for name in programs:
            p = _write_program_inputs(h, name, tmp)
            loaded = load_program_data(p["workbook"], p["people"], p["dept"], p["topics"], program=name)
            people, dept = loaded[2], loaded[3]
            computed.append(compute_program(name, None, loaded, tmp))
        agg = compute_aggregate(computed, people, dept)
        return render_aggregate(agg, _date.today().strftime("%b %d, %Y").replace(" 0", " "),
                                dept_name=h["settings"]["dept_name"], title=h["settings"]["title"])


# ---------------------------------------------------------------- page API
# Thin JSON wrappers the web page calls; the history lives in this module.

_STATE = {"h": None}


def _ok(**data):
    return json.dumps({"ok": True, **data})


def _guard(fn):
    def wrapped(*args):
        try:
            return fn(*args)
        except PipelineError as e:
            return json.dumps({"ok": False, "error": str(e)})
        except Exception as e:  # unexpected: show it rather than fail silently
            return json.dumps({"ok": False, "error": f"Unexpected error: {type(e).__name__}: {e}"})
    return wrapped


def _h():
    if _STATE["h"] is None:
        raise PipelineError("Load a history file or start a new one first.")
    return _STATE["h"]


@_guard
def api_new(dept_name, title):
    _STATE["h"] = new_history(dept_name, title)
    return _ok(summary=summary(_STATE["h"]))


@_guard
def api_load(path):
    _STATE["h"] = load_history(path)
    return _ok(summary=summary(_STATE["h"]))


@_guard
def api_settings(dept_name, title):
    set_settings(_h(), dept_name, title)
    return _ok(summary=summary(_h()))


@_guard
def api_add_type(name, topics, presenters):
    add_meeting_type(_h(), name, bool(topics), bool(presenters))
    return _ok(summary=summary(_h()))


@_guard
def api_update_type(name, new_name, topics, presenters, hidden):
    update_meeting_type(_h(), name, new_name, bool(topics), bool(presenters), bool(hidden))
    return _ok(summary=summary(_h()))


@_guard
def api_prepare(csv_path, program, replace):
    return _ok(review=prepare_session(_h(), csv_path, program, bool(replace)))


@_guard
def api_cancel():
    cancel_session(_h())
    return _ok()


@_guard
def api_commit(choices_json):
    report = commit_session(_h(), json.loads(choices_json))
    return _ok(report=report, summary=summary(_h()))


@_guard
def api_save(path):
    save_history(_h(), path)
    return _ok()


@_guard
def api_session_page(program, session_date):
    return _ok(html=session_page(_h(), program, session_date))


@_guard
def api_dashboard(programs_json):
    return _ok(html=combined_dashboard(_h(), json.loads(programs_json)))
