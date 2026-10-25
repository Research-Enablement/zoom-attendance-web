#!/usr/bin/env python3
"""
Refreshes the master people registry from a program's newest final workbook,
then rewrites the mailing list from the registry.

    python3 tools/refresh_master.py workbooks/data_workshop_final.xlsx   # refresh registry, then mailing list
    python3 tools/refresh_master.py                            # mailing list only, from the current registry

Run the second form after editing master_people_db.csv by hand.

Writes, overwriting in place (no dated copies pile up):
    data/master_people_db.csv      the workbook's people_db sheet
    data/master_name_aliases.csv   the workbook's name_aliases sheet
    data/mailing_list.csv          one column, "email": every unique address in the registry

It never touches master_dept_to_parent.csv. That mapping is maintained by hand, and the
workbooks' own dept_to_parent sheet is empty, so copying it over would wipe the real mapping.

Safety: if the workbook is missing anyone (or any alias) already in the current registry,
for example because an older workbook was picked by mistake, it stops without changing
anything. Pass --force only if the removal is intentional.
"""

import argparse
import os
import re
import sys

import pandas as pd

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _read(path):
    return pd.read_csv(path, dtype=str).fillna("")


def refresh_registry(workbook, people_path, aliases_path, force):
    xl = pd.ExcelFile(workbook)
    for sheet in ("people_db", "name_aliases"):
        if sheet not in xl.sheet_names:
            sys.exit(f"ERROR: {workbook} has no '{sheet}' sheet.")
    new_people = xl.parse("people_db", dtype=str).fillna("")
    new_aliases = xl.parse("name_aliases", dtype=str).fillna("")

    if not force:
        checks = [(people_path, "person_id", new_people, "people"),
                  (aliases_path, "zoom_display_name", new_aliases, "aliases")]
        for path, key, new_df, label in checks:
            if os.path.exists(path):
                missing = (set(_read(path)[key]) - set(new_df[key])) - {""}
                if missing:
                    sys.exit(f"ERROR: {workbook} is missing {len(missing)} of the {label} already in {path}. "
                             f"That usually means an older workbook was picked. Use the newest final workbook, "
                             f"or pass --force if removing them is intentional. Nothing was changed.")

    new_people.to_csv(people_path, index=False)
    new_aliases.to_csv(aliases_path, index=False)
    return len(new_people), len(new_aliases)


def write_mailing_list(people_path, out_path):
    people = _read(people_path)
    if "email" not in people.columns:
        sys.exit(f"ERROR: {people_path} has no 'email' column.")

    emails = people["email"].str.strip().str.lower()
    has_email = emails != ""
    looks_valid = emails.apply(lambda e: bool(EMAIL_RE.match(e)))
    good = emails[has_email & looks_valid]

    unique = sorted(set(good))
    pd.DataFrame({"email": unique}).to_csv(out_path, index=False)

    return {
        "people": len(people),
        "addresses": len(unique),
        "no_email": int((~has_email).sum()),
        "malformed": int((has_email & ~looks_valid).sum()),
        "shared_addresses": int(good[good.duplicated()].nunique()),
    }


def main():
    parser = argparse.ArgumentParser(description="Refresh the master registry and rewrite the mailing list")
    parser.add_argument("workbook", nargs="?", default=None,
                        help="Newest final workbook. Omit to rebuild only the mailing list.")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--force", action="store_true", help="Allow the refresh to remove people or aliases")
    args = parser.parse_args()

    people_path = os.path.join(args.data_dir, "master_people_db.csv")
    aliases_path = os.path.join(args.data_dir, "master_name_aliases.csv")
    list_path = os.path.join(args.data_dir, "mailing_list.csv")

    if args.workbook:
        n_people, n_aliases = refresh_registry(args.workbook, people_path, aliases_path, args.force)
        print(f"Refreshed from {args.workbook}: {n_people} people, {n_aliases} aliases")
    elif not os.path.exists(people_path):
        sys.exit(f"ERROR: {people_path} not found. Pass a final workbook to create it.")

    s = write_mailing_list(people_path, list_path)
    print(f"Mailing list: {s['addresses']} unique addresses -> {list_path}")
    print(f"  not on the list: {s['no_email']} with no email"
          + (f", {s['malformed']} with an address that doesn't look valid" if s["malformed"] else "")
          + f" (of {s['people']} people)")
    if s["shared_addresses"]:
        print(f"  NOTE: {s['shared_addresses']} address(es) belong to more than one person_id. "
              f"Each is listed once, but it may mean the same person is registered twice.")


if __name__ == "__main__":
    main()