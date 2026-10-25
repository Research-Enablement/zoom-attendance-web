"""
New-vs-returning: whether an attendance row is a person's first-ever
appearance in this program's attendance_log, or a repeat visit.

Scope is deliberately per-program. Someone's first session of one program
counts as "new" for that program even if they've attended ten sessions of
another -- this module only ever looks at one program's own log, since
that's the log its caller passes in.
"""

import pandas as pd


def tag_new_returning(attendance_log):
    """attendance_log: one program's full attendance log (every session),
    columns must include person_id and session_date. Returns a copy with
    an added 'is_new' boolean column: True on a person's first appearance
    in the whole log, False on every later one."""
    df = attendance_log.copy()
    df["is_new"] = ~df.sort_values("session_date").duplicated("person_id", keep="first")
    return df


def session_new_returning_counts(tagged_log, session_date):
    """tagged_log: output of tag_new_returning. Returns (new_count, returning_count)
    for one session_date."""
    session_rows = tagged_log[tagged_log["session_date"] == session_date]
    new_count = int(session_rows["is_new"].sum())
    returning_count = int((~session_rows["is_new"]).sum())
    return new_count, returning_count