#!/usr/bin/env python3
"""
Build the manual-cleanup workbook that recompile_workbook.py reads.

Reads the Participants-report shape:
    Name (original name), Email, Join time, Leave time,
    Duration (minutes), Guest, [Recording disclaimer response], [In waiting room]
"""

import argparse
import glob
import os
import sys

import pandas as pd

PEOPLE_COLS = ["person_id", "email", "name", "department", "status", "title", "lookup_source", "notes"]
DEPT_COLS = ["department", "parent_unit"]
ALIAS_COLS = ["zoom_display_name", "person_id", "notes"]
SESSION_COLS = ["Name (original name)", "Email", "Total duration (minutes)", "Guest", "person_id"]

_TIME_FORMATS = ["%m/%d/%Y %I:%M:%S %p", "%m/%d/%y %H:%M"]


def load_or_empty(path, columns):
    if path and os.path.exists(path):
        df = pd.read_csv(path, dtype=str).fillna("")
        for col in columns:
            if col not in df.columns:
                df[col] = ""
        return df[columns]
    return pd.DataFrame(columns=columns)


def parse_times(series):
    parsed = pd.to_datetime(series, format=_TIME_FORMATS[0], errors="coerce")
    missing = parsed.isna()
    if missing.any():
        parsed = parsed.where(~missing, pd.to_datetime(series[missing], format=_TIME_FORMATS[1], errors="coerce"))
    return parsed


def load_raw_session(filepath):
    df = pd.read_csv(filepath, dtype=str, encoding="utf-8-sig").fillna("")
    df.columns = df.columns.str.strip()

    required = ["Name (original name)", "Email", "Join time", "Duration (minutes)", "Guest"]
    for col in required:
        if col not in df.columns:
            sys.exit(f"ERROR: {filepath} is missing expected column '{col}'.")

    dt = parse_times(df["Join time"])
    dates = dt.dt.strftime("%Y-%m-%d").dropna()
    date = dates.mode()[0] if not dates.empty else None

    out = pd.DataFrame()
    out["Name (original name)"] = df["Name (original name)"]
    out["Email"] = df["Email"]
    out["Total duration (minutes)"] = pd.to_numeric(df["Duration (minutes)"], errors="coerce").fillna(0)
    out["Guest"] = df["Guest"]
    out["person_id"] = ""
    return out, date


def next_person_id(existing_ids):
    nums = [int(pid[1:]) for pid in existing_ids if pid.startswith("p") and pid[1:].isdigit()]
    return f"p{(max(nums) + 1) if nums else 1:04d}"


def assign_ids_to_people_db(people_df):
    people_df = people_df.copy()
    existing_ids = set(people_df["person_id"]) - {""}
    for idx, row in people_df.iterrows():
        if row["person_id"].strip() == "" and row["email"].strip() != "":
            new_id = next_person_id(existing_ids)
            people_df.at[idx, "person_id"] = new_id
            existing_ids.add(new_id)
    return people_df


def resolve_session(session_df, people_df, alias_df, qc_notes):
    email_to_id = {
        row["email"].strip().lower(): row["person_id"]
        for _, row in people_df.iterrows()
        if row["email"].strip() and row["person_id"].strip()
    }
    alias_to_id = {
        row["zoom_display_name"].strip(): row["person_id"].strip()
        for _, row in alias_df.iterrows()
        if row["zoom_display_name"].strip() and row["person_id"].strip()
    }

    existing_ids = set(people_df["person_id"]) - {""}
    new_people = []

    for idx, row in session_df.iterrows():
        email = row["Email"].strip().lower()
        name = row["Name (original name)"].strip()

        if email and email in email_to_id:
            session_df.at[idx, "person_id"] = email_to_id[email]
            continue

        if email:
            new_id = next_person_id(existing_ids)
            existing_ids.add(new_id)
            email_to_id[email] = new_id
            session_df.at[idx, "person_id"] = new_id
            new_people.append({
                "person_id": new_id, "email": email, "name": name,
                "department": "", "status": "", "title": "",
                "lookup_source": "auto_registered", "notes": "",
            })
            continue

        if name in alias_to_id:
            session_df.at[idx, "person_id"] = alias_to_id[name]
            continue

        qc_notes.append(f"  - {name or '(blank name)'}")

    return session_df, new_people


def main():
    parser = argparse.ArgumentParser(description="Build the manual cleanup workbook")
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--people-db")
    parser.add_argument("--dept-parent")
    parser.add_argument("--aliases")
    parser.add_argument("--out", default="cleanup.xlsx")
    args = parser.parse_args()

    if os.path.exists(args.out):
        sys.exit(f"ERROR: {args.out} already exists. Refusing to overwrite.")

    people_df = load_or_empty(args.people_db, PEOPLE_COLS)
    dept_df = load_or_empty(args.dept_parent, DEPT_COLS)
    alias_df = load_or_empty(args.aliases, ALIAS_COLS)
    people_df = assign_ids_to_people_db(people_df)

    raw_files = sorted(glob.glob(os.path.join(args.raw_dir, "**", "*.csv"), recursive=True))
    if not raw_files:
        sys.exit(f"ERROR: no CSV files found under {args.raw_dir}")

    sessions = {}
    unresolved_notes = []
    all_new_people = []

    for filepath in raw_files:
        session_df, date = load_raw_session(filepath)
        if date is None:
            sys.exit(f"ERROR: could not determine a session date from {filepath}")
        if date in sessions:
            sys.exit(f"ERROR: two raw files both resolve to session date {date} "
                      f"(one is {filepath}) — check for a duplicate export.")

        session_notes = []
        resolved_df, new_people = resolve_session(session_df, people_df, alias_df, session_notes)
        if session_notes:
            unresolved_notes.append(f"{date}:")
            unresolved_notes.extend(session_notes)

        if new_people:
            people_df = pd.concat([people_df, pd.DataFrame(new_people)], ignore_index=True)
            all_new_people.extend(new_people)

        sessions[date] = resolved_df

    with pd.ExcelWriter(args.out, engine="openpyxl") as writer:
        people_df.to_excel(writer, sheet_name="people_db", index=False)
        dept_df.to_excel(writer, sheet_name="dept_to_parent", index=False)
        alias_df.to_excel(writer, sheet_name="name_aliases", index=False)
        for date in sorted(sessions):
            sessions[date].to_excel(writer, sheet_name=date, index=False)

    print(f"Wrote {args.out}")
    print(f"  people_db:       {len(people_df)} rows ({len(all_new_people)} auto-registered)")
    print(f"  dept_to_parent:  {len(dept_df)} rows")
    print(f"  session sheets:  {len(sessions)}  ({', '.join(sorted(sessions))})")
    if unresolved_notes:
        n = sum(1 for line in unresolved_notes if line.startswith("  -"))
        print(f"\n  {n} row(s) still need manual person_id:")
        for line in unresolved_notes:
            print(f"  {line}")
    else:
        print("\n  Nothing left to resolve manually.")


if __name__ == "__main__":
    main()