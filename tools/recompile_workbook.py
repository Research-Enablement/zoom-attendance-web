#!/usr/bin/env python3
"""Recompile a reviewed cleanup workbook into one final output workbook."""

import argparse
import re
import sys

import pandas as pd

PEOPLE_SHEET = "people_db"
DEPT_SHEET = "dept_to_parent"
ALIAS_SHEET = "name_aliases"

PEOPLE_COLS = ["person_id", "email", "name", "department", "status", "title", "lookup_source", "notes"]
DEPT_COLS = ["department", "parent_unit"]
ALIAS_COLS = ["zoom_display_name", "person_id", "notes"]
SESSION_COLS = ["Name (original name)", "Email", "Total duration (minutes)", "Guest", "person_id"]

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class QCReport:
    def __init__(self):
        self.sections = []

    def add(self, title, items):
        if items:
            self.sections.append((title, items))

    def is_clean(self):
        return not self.sections

    def render(self):
        if self.is_clean():
            return "QC: no issues found.\n"
        lines = ["QC REPORT", "=" * 40, ""]
        for title, items in self.sections:
            lines.append(f"{title}  ({len(items)})")
            for item in items:
                lines.append(f"  - {item}")
            lines.append("")
        return "\n".join(lines)


def load_people_db(xl, qc):
    if PEOPLE_SHEET not in xl.sheet_names:
        sys.exit(f"ERROR: workbook has no '{PEOPLE_SHEET}' sheet.")
    df = xl.parse(PEOPLE_SHEET, dtype=str).fillna("")
    for col in PEOPLE_COLS:
        if col not in df.columns:
            df[col] = ""
    df = df[PEOPLE_COLS]
    df["person_id"] = df["person_id"].str.strip()
    df = df[df["person_id"] != ""]

    dupes = df[df.duplicated("person_id", keep=False)]["person_id"].unique().tolist()
    qc.add("Duplicate person_id in people_db", dupes)

    return df.drop_duplicates("person_id", keep="first")


def load_dept_to_parent(xl, qc):
    if DEPT_SHEET not in xl.sheet_names:
        sys.exit(f"ERROR: workbook has no '{DEPT_SHEET}' sheet.")
    df = xl.parse(DEPT_SHEET, dtype=str).fillna("")
    for col in DEPT_COLS:
        if col not in df.columns:
            df[col] = ""
    df = df[DEPT_COLS]
    missing = df[df["parent_unit"].str.strip() == ""]["department"].tolist()
    qc.add("Departments with no parent_unit", missing)
    return df


def load_aliases(xl):
    if ALIAS_SHEET not in xl.sheet_names:
        return pd.DataFrame(columns=ALIAS_COLS)
    df = xl.parse(ALIAS_SHEET, dtype=str).fillna("")
    for col in ALIAS_COLS:
        if col not in df.columns:
            df[col] = ""
    return df[ALIAS_COLS]


def find_session_sheets(xl):
    known = {PEOPLE_SHEET, DEPT_SHEET, ALIAS_SHEET}
    return sorted(name for name in xl.sheet_names if name not in known and _DATE_RE.match(name.strip()))


def process_session(xl, session_date, people_ids, qc):
    df = xl.parse(session_date, dtype=str).fillna("")
    for col in SESSION_COLS:
        if col not in df.columns:
            sys.exit(f"ERROR: sheet '{session_date}' is missing column '{col}'.")
    df = df[SESSION_COLS].copy()
    df["Total duration (minutes)"] = pd.to_numeric(df["Total duration (minutes)"], errors="coerce").fillna(0)
    df["person_id"] = df["person_id"].str.strip()

    unresolved = df[df["person_id"] == ""]
    if not unresolved.empty:
        for _, row in unresolved.iterrows():
            label = row["Name (original name)"] or row["Email"] or "(blank)"
            qc.add(f"Unresolved rows in session {session_date} — excluded", [label])
        df = df[df["person_id"] != ""]

    unknown = sorted(set(df["person_id"]) - people_ids)
    if unknown:
        qc.add(f"Session {session_date} references person_id not in people_db", unknown)
        df = df[df["person_id"].isin(people_ids)]

    if df.empty:
        return None, {"total_attendees": 0, "avg_duration_minutes": 0.0}

    per_person = df.groupby("person_id")["Total duration (minutes)"].sum()
    first_seen = df.drop_duplicates("person_id")[["person_id", "Name (original name)", "Email"]]
    attendance = first_seen.merge(per_person.rename("total_minutes"), on="person_id")
    attendance["session_date"] = session_date
    attendance["session_month"] = session_date[:7].replace("-", "_")

    metrics = {
        "total_attendees": len(attendance),
        "avg_duration_minutes": round(float(per_person.mean()), 1),
    }
    return attendance, metrics


def harvest_new_aliases(sessions, people_df, alias_df, qc):
    known_pairs = {(row["zoom_display_name"], row["person_id"]) for _, row in alias_df.iterrows()}
    people_ids = set(people_df["person_id"])
    new_rows = []
    conflicts = []

    for date, df in sessions.items():
        no_email = df[(df["Email"].str.strip() == "") & (df["person_id"].str.strip() != "")]
        for _, row in no_email.iterrows():
            name = row["Name (original name)"].strip()
            pid = row["person_id"].strip()
            if not name or pid not in people_ids:
                continue
            existing_for_name = alias_df[alias_df["zoom_display_name"] == name]["person_id"].tolist()
            if existing_for_name and pid not in existing_for_name:
                conflicts.append(f"{name}: session {date} used {pid}, "
                                  f"but name_aliases already maps it to {existing_for_name[0]}")
                continue
            if (name, pid) not in known_pairs:
                new_rows.append({"zoom_display_name": name, "person_id": pid, "notes": f"auto-harvested from {date}"})
                known_pairs.add((name, pid))

    qc.add("New aliases learned from this batch (now saved for future runs)",
           [f"{r['zoom_display_name']} -> {r['person_id']}" for r in new_rows])
    qc.add("Alias conflicts — needs a human decision", conflicts)

    if new_rows:
        alias_df = pd.concat([alias_df, pd.DataFrame(new_rows)], ignore_index=True)
    return alias_df


def main():
    parser = argparse.ArgumentParser(description="Recompile a reviewed cleanup workbook")
    parser.add_argument("workbook")
    parser.add_argument("--out", default="attendance_data.xlsx")
    args = parser.parse_args()

    xl = pd.ExcelFile(args.workbook)
    qc = QCReport()

    people_df = load_people_db(xl, qc)
    dept_df = load_dept_to_parent(xl, qc)
    alias_df = load_aliases(xl)
    session_dates = find_session_sheets(xl)
    if not session_dates:
        sys.exit("ERROR: no session sheets found (names must look like YYYY-MM-DD).")

    people_ids = set(people_df["person_id"])
    attendance_frames, summary_rows, sessions_for_aliasing = [], [], {}

    for date in session_dates:
        attendance, metrics = process_session(xl, date, people_ids, qc)
        raw_session = xl.parse(date, dtype=str).fillna("")
        sessions_for_aliasing[date] = raw_session
        if attendance is not None:
            attendance_frames.append(attendance)
        summary_rows.append({
            "session_month": date[:7].replace("-", "_"),
            "session_date": date,
            **metrics,
        })

    alias_df = harvest_new_aliases(sessions_for_aliasing, people_df, alias_df, qc)

    attendance_log = (pd.concat(attendance_frames, ignore_index=True) if attendance_frames
                       else pd.DataFrame(columns=["person_id", "name", "email", "total_minutes",
                                                   "session_date", "session_month"]))
    attendance_log = attendance_log.rename(columns={"Name (original name)": "name", "Email": "email"})[
        ["session_month", "session_date", "person_id", "name", "email", "total_minutes"]
    ]
    sessions_summary = pd.DataFrame(summary_rows).sort_values("session_date").reset_index(drop=True)

    with pd.ExcelWriter(args.out, engine="openpyxl") as writer:
        people_df.to_excel(writer, sheet_name="people_db", index=False)
        dept_df.to_excel(writer, sheet_name="dept_to_parent", index=False)
        alias_df.to_excel(writer, sheet_name="name_aliases", index=False)
        attendance_log.to_excel(writer, sheet_name="attendance_log", index=False)
        sessions_summary.to_excel(writer, sheet_name="sessions_summary", index=False)

    report_text = qc.render()
    print(f"Wrote {args.out}")
    print()
    print(report_text)


if __name__ == "__main__":
    main()