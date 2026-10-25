#!/usr/bin/env python3
"""
Convert meetinglistdetails_*.csv or Usage-report participants_*.csv files
into the Participants-report shape:
    Name (original name), Email, Join time, Leave time,
    Duration (minutes), Guest, Recording disclaimer response, In waiting room

meetinglistdetails -> lossless conversion, real Join/Leave/Duration carried through.

Usage-report (two-section, duration-only, no timestamps) -> Join time is set
to the meeting's real Start time; Leave time is APPROXIMATED as Start time +
that person's total duration. This assumes one continuous connection, which
is wrong for anyone who reconnected mid-session. These rows are marked
"approximated_from_usage_report" in a Notes column so this is never mistaken
for real captured timestamp data.

Already in Participants-report shape -> passed through with missing optional
columns filled blank.

Usage:
    python3 normalize_to_participants_report.py INPUT.csv --out OUTPUT.csv
"""

import argparse
import csv
import sys

import pandas as pd

_TIME_FORMATS = ["%m/%d/%Y %I:%M:%S %p", "%m/%d/%y %H:%M", "%m/%d/%Y %H:%M"]
OUT_COLS = ["Name (original name)", "Email", "Join time", "Leave time",
            "Duration (minutes)", "Guest", "Recording disclaimer response",
            "In waiting room", "Notes"]


def parse_times(series):
    parsed = pd.to_datetime(series, format=_TIME_FORMATS[0], errors="coerce")
    for fmt in _TIME_FORMATS[1:]:
        missing = parsed.isna()
        if not missing.any():
            break
        parsed = parsed.where(~missing, pd.to_datetime(series[missing], format=fmt, errors="coerce"))
    return parsed


def read_raw_rows(filepath):
    with open(filepath, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def detect_shape(rows):
    header = [h.strip() for h in rows[0]]
    if "Topic" in header and "Host name" in header:
        return "meetinglistdetails"
    if "Join time" in header and "Leave time" in header:
        return "flat_participants"
    if any(all(c.strip() == "" for c in row) for row in rows[:10]):
        return "two_section"
    sys.exit(f"ERROR: unrecognized header shape: {header}")


def convert_meetinglistdetails(filepath):
    df = pd.read_csv(filepath, dtype=str, encoding="utf-8-sig").fillna("")
    df.columns = df.columns.str.strip()
    if "Duration (minutes).1" in df.columns:
        df["Duration (minutes)"] = df["Duration (minutes).1"]

    out = pd.DataFrame()
    out["Name (original name)"] = df["Name (original name)"]
    out["Email"] = df["Email"]
    out["Join time"] = df["Join time"]
    out["Leave time"] = df["Leave time"]
    out["Duration (minutes)"] = df["Duration (minutes)"]
    out["Guest"] = df.get("Guest", "")
    out["Recording disclaimer response"] = df.get("Recording disclaimer response", "")
    out["In waiting room"] = df.get("In waiting room", "")
    out["Notes"] = ""
    return out


def convert_flat_participants(filepath):
    df = pd.read_csv(filepath, dtype=str, encoding="utf-8-sig").fillna("")
    df.columns = df.columns.str.strip()
    out = pd.DataFrame()
    for col in OUT_COLS[:-1]:
        out[col] = df.get(col, "")
    out["Notes"] = ""
    return out


def convert_two_section(filepath):
    rows = read_raw_rows(filepath)
    blank_idx = next(i for i, row in enumerate(rows) if all(c.strip() == "" for c in row))
    summary_rows = rows[:blank_idx]
    participant_rows = [r for r in rows[blank_idx + 1:] if any(c.strip() != "" for c in r)]

    header = [h.strip() for h in participant_rows[0]]
    width = len(header)
    body = [(r + [""] * width)[:width] for r in participant_rows[1:]]
    participant_df = pd.DataFrame(body, columns=header)

    if "Name (original name)" not in header:
        sys.exit(f"ERROR: {filepath} has no 'Name (original name)' column. Columns found: {header}")

    if "Join time" in header and "Leave time" in header:
        out = pd.DataFrame()
        for col in OUT_COLS[:-1]:
            out[col] = participant_df[col] if col in header else ""
        out["Notes"] = ""
        return out

    summary_df = pd.DataFrame(summary_rows[1:], columns=[h.strip() for h in summary_rows[0]])
    if "Start time" not in summary_df.columns or summary_df.empty:
        sys.exit(f"ERROR: {filepath} has no 'Start time' in its summary row.")
    start_time = parse_times(pd.Series([summary_df.iloc[0]["Start time"]])).iloc[0]

    duration_col = next((c for c in ("Total duration (minutes)", "Duration (minutes)") if c in header), None)
    if duration_col is None:
        sys.exit(f"ERROR: {filepath} has no duration column. Columns found: {header}")
    duration = pd.to_numeric(participant_df[duration_col], errors="coerce").fillna(0)
    leave_time = start_time + pd.to_timedelta(duration, unit="m")

    out = pd.DataFrame()
    out["Name (original name)"] = participant_df["Name (original name)"]
    out["Email"] = participant_df["Email"] if "Email" in header else ""
    out["Join time"] = start_time.strftime("%m/%d/%Y %I:%M:%S %p")
    out["Leave time"] = leave_time.dt.strftime("%m/%d/%Y %I:%M:%S %p")
    out["Duration (minutes)"] = duration
    out["Guest"] = participant_df["Guest"] if "Guest" in header else ""
    out["Recording disclaimer response"] = ""
    out["In waiting room"] = ""
    out["Notes"] = "approximated_from_usage_report"
    return out


def main():
    parser = argparse.ArgumentParser(description="Convert a raw Zoom export to Participants-report shape")
    parser.add_argument("input")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rows = read_raw_rows(args.input)
    shape = detect_shape(rows)

    if shape == "meetinglistdetails":
        out = convert_meetinglistdetails(args.input)
    elif shape == "flat_participants":
        out = convert_flat_participants(args.input)
    else:
        out = convert_two_section(args.input)

    out[OUT_COLS].to_csv(args.out, index=False)
    approx = (out["Notes"] == "approximated_from_usage_report").sum()
    print(f"Converted ({shape}) -> {args.out}")
    if approx:
        print(f"  NOTE: {approx} row(s) have approximated timestamps (no real join/leave data existed)")


if __name__ == "__main__":
    main()