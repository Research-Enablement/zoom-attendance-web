"""Engine tests with made-up people only. Run: python3 tests/test_engine.py"""

import csv
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "tools"), os.path.join(ROOT, "engine")]

import web_engine as we  # noqa: E402

HEADER = ["Name (original name)", "Email", "Join time", "Leave time", "Duration (minutes)", "Guest"]


def participants(path, date, rows, meeting_block=False):
    """rows: (name, email, minutes). meeting_block: Zoom's 'export with meeting data' layout."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        if meeting_block:
            w.writerow(["Topic", "ID", "Host name", "Start time"])
            w.writerow(["Test meeting", "123", "Host", f"{date} 10:00:00 AM"])
            w.writerow([])
        w.writerow(HEADER)
        for name, email, minutes in rows:
            w.writerow([name, email, f"{date} 10:00:00 AM", f"{date} 11:00:00 AM", minutes, "No"])


def usage_report(path, date, rows):
    """The two-section usage report: no join or leave times."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Topic", "ID", "Start time", "End time"])
        w.writerow(["Test meeting", "123", f"{date} 10:00:00 AM", f"{date} 11:30:00 AM"])
        w.writerow([])
        w.writerow(["Name (original name)", "Email", "Total duration (minutes)", "Guest"])
        for name, email, minutes in rows:
            w.writerow([name, email, minutes, "No"])


def check(cond, label):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        raise SystemExit(1)


def call(fn, *args):
    res = json.loads(fn(*args))
    if not res["ok"]:
        raise AssertionError(res["error"])
    return res


def main():
    tmp = tempfile.mkdtemp()
    f1, f2, f3, f4 = (os.path.join(tmp, n) for n in ("s1.csv", "s2.csv", "s3.csv", "s4.csv"))
    participants(f1, "09/10/2026", [
        ("Ada Example", "ada@example.edu", 55), ("Ben Example", "ben@example.edu", 40),
        ("Cy Phone", "", 30), ("Cy Phone", "", 20),          # reconnected, no email
        ("dee's iPad", "", 12)])
    participants(f2, "10/08/2026", [("Ada Example", "ada@example.edu", 60), ("Cy Phone", "", 45),
                                    ("Eve Example", "eve@example.edu", 10)], meeting_block=True)
    usage_report(f3, "10/22/2026", [("Ben Example", "ben@example.edu", 80), ("Ada E.", "", 20)])
    with open(f4, "w") as f:
        f.write("Meeting summary\nnothing useful here\n")

    s = call(we.api_new, "Example Unit", "")["summary"]
    check(s["settings"] == {"dept_name": "Example Unit", "title": we.DEFAULT_TITLE}, "new history keeps settings")
    call(we.api_add_type, "Data Workshop", True, True)
    call(we.api_add_type, "Coding Club", False, False)
    bad = json.loads(we.api_add_type("data workshop", True, True))
    check(not bad["ok"], "duplicate type name is refused")

    # --- session 1: two Zoom names need a person
    r = call(we.api_prepare, f1, "Data Workshop", False)["review"]
    keys = {u["key"]: u for u in r["unmatched"]}
    check(set(keys) == {"Cy Phone", "dee's iPad"}, "unmatched grouped by Zoom name")
    check(keys["Cy Phone"]["rows"] == [2, 3] and keys["Cy Phone"]["minutes"] == 50, "reconnects grouped")
    check(len(r["new_people"]) == 2, "people with email auto-added")
    check(r["defaults"]["length"] == "60", "length defaults to 60")

    missing = json.loads(we.api_commit(json.dumps({"match": {"Cy Phone": {"new": {"name": "Cy Phone"}}},
                                                   "length": "60"})))
    check(not missing["ok"] and "dee's iPad" in missing["error"], "save refused while someone is unmatched")

    ben = next(p["id"] for p in r["people"] if p["name"] == "Ben Example")
    ada = next(p["id"] for p in r["people"] if p["name"] == "Ada Example")
    rep = call(we.api_commit, json.dumps({
        "match": {"Cy Phone": {"new": {"name": "Cy Phone", "department": "Example Dept", "status": "staff"}},
                  "dee's iPad": ben},
        "details": {ada: {"department": "Example Dept", "status": "student"}},
        "topic": "Intro", "presenter": "Jo Example", "length": "90"}))
    report, s = rep["report"], rep["summary"]
    check(report["attendees"] == 3, "3 people: Ada, Ben (+ iPad), Cy")
    h = we._STATE["h"]
    ben_minutes = h["attendance_log"].set_index("person_id").at[ben, "total_minutes"]
    check(ben_minutes == 52, "iPad minutes added to Ben")
    check(set(h["name_aliases"]["zoom_display_name"]) == {"Cy Phone", "dee's iPad"}, "Zoom names remembered")
    check(h["people_db"].set_index("person_id").at[ada, "status"] == "student", "optional details saved")
    check(next(t for t in s["types"] if t["name"] == "Data Workshop")["length"] == 90, "length remembered")

    # --- session 2: meeting-data layout; Cy matches by alias now
    r = call(we.api_prepare, f2, "Data Workshop", False)["review"]
    check(r["unmatched"] == [] and r["defaults"]["length"] == "90", "alias matched, length suggested")
    call(we.api_commit, json.dumps({"match": {}, "topic": "Second", "length": "90"}))
    dup = json.loads(we.api_prepare(f2, "Data Workshop", False))
    check(not dup["ok"] and "already has" in dup["error"], "same date refused without replace")

    # --- session 3: usage report with a suggested match
    r = call(we.api_prepare, f3, "Coding Club", False)["review"]
    check(r["shape"] == "two_section" and r["approximated"] == 2, "usage report converted")
    u = r["unmatched"][0]
    check(u["zoom_name"] == "Ada E." and ada in u["suggestions"], "suggestion offered for a look-alike name")
    check(r["asks"] == {"topic": False, "presenter": False}, "type without topics or presenters")
    call(we.api_commit, json.dumps({"match": {"Ada E.": ada}, "topic": "ignored", "length": "30"}))
    check(h["sessions"].iloc[-1]["topic"] == "", "topic not stored for a type without topics")

    bad = json.loads(we.api_prepare(f4, "Data Workshop", False))
    check(not bad["ok"] and "s4.csv" in bad["error"], "unknown file shape gives a readable error")

    # --- pages
    page = call(we.api_session_page, "Data Workshop", "2026-09-10")["html"]
    check("Stayed &ge; 75 min" in page and "Example Unit" in page, "session page uses length and dept name")
    check("Cy Phone" not in page and "Ada Example" not in page, "no names on the session page")
    dash = call(we.api_dashboard, json.dumps(["Data Workshop", "Coding Club"]))["html"]
    check("Example Unit" in dash and "Ada Example" not in dash, "overview has dept name, no names")

    # --- rename and hide
    call(we.api_update_type, "Data Workshop", "Data Skills", True, True, False)
    check(set(h["attendance_log"]["program"]) == {"Data Skills", "Coding Club"}, "rename moves sessions")
    check("Data Skills" in set(h["sessions"]["program"]), "rename moves topics")
    s = call(we.api_update_type, "Coding Club", "Coding Club", False, False, True)["summary"]
    check(next(t for t in s["types"] if t["name"] == "Coding Club")["hidden"], "hide saved")

    # --- save, load, and an older experiment file
    out = os.path.join(tmp, "history.xlsx")
    call(we.api_save, out)
    s = call(we.api_load, out)["summary"]
    check(s["settings"]["dept_name"] == "Example Unit" and s["aliases"] == 3, "round trip keeps everything")
    check(next(t for t in s["types"] if t["name"] == "Coding Club")["hidden"], "round trip keeps hidden")

    import pandas as pd
    old = os.path.join(tmp, "old.xlsx")
    with pd.ExcelWriter(old, engine="openpyxl") as w:
        pd.DataFrame({"name": ["Old Type"], "notes": [""]}).to_excel(w, sheet_name="meeting_types", index=False)
        for sheet, cols in we.SHEETS.items():
            if sheet != "meeting_types":
                pd.DataFrame(columns=[c for c in cols if c != "length_minutes"]).to_excel(
                    w, sheet_name=sheet, index=False)
    s = call(we.api_load, old)["summary"]
    t = s["types"][0]
    check(t["topics"] and t["presenters"] and not t["hidden"] and t["length"] == 60, "older history file loads")
    print("\nAll engine tests passed.")


if __name__ == "__main__":
    main()
