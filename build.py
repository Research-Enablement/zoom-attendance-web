#!/usr/bin/env python3
"""Build index.html: web/index.src.html with the Python files embedded.

Run after editing anything in web/, engine/, tools/ or config/:
    python3 build.py
Edit those files, never index.html (it is generated).
"""

import glob
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    files = {}
    for folder in ("tools", "engine"):
        for path in sorted(glob.glob(os.path.join(ROOT, folder, "*.py"))):
            with open(path, encoding="utf-8") as f:
                files[f"{folder}/{os.path.basename(path)}"] = f.read()
    seed = os.path.join(ROOT, "config", "master_dept_to_parent.csv")
    if os.path.exists(seed):
        with open(seed, encoding="utf-8-sig") as f:
            files["engine/master_dept_to_parent.csv"] = f.read()
    else:
        print("NOTE: config/master_dept_to_parent.csv not found. New histories will start with an empty department list.")

    with open(os.path.join(ROOT, "web", "index.src.html"), encoding="utf-8") as f:
        page = f.read()
    if page.count("@@PY_FILES@@") != 1:
        raise SystemExit("ERROR: web/index.src.html must contain @@PY_FILES@@ exactly once.")
    # "</" would end the <script> block early, so escape it inside the JSON.
    page = page.replace("@@PY_FILES@@", json.dumps(files).replace("</", "<\\/"))

    out = os.path.join(ROOT, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Wrote index.html ({len(files)} embedded files, {len(page) // 1024} KB)")


if __name__ == "__main__":
    main()
