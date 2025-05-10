from tqdm import tqdm
from dotenv import load_dotenv
from dateutil import parser as dateparser
import requests
from datetime import datetime
import sys
import time
import os

''''
Here’s the ** complete ** `download_modules.py` with:

* **Interactive ** choice of all/current/specific terms
* **Per-module ** folders
* **Skip & log ** any file that already exists in your `downloads /` tree

python

#!/usr/bin/env python3

download_modules.py

Interactive Canvas module downloader.

Options on startup:
  1) All active courses
  2) Current term courses
  3) One or more specific terms

Downloads go into:
  downloads/
    <Course Name>/
      <Module Name>/
        <File>

Before each download it checks:
  • If that exact file path already exists → skips & logs.
'''


# ─── Requirements ────────────────────────────────────────────────────
# pip install requests python-dotenv tqdm python-dateutil

# ─── Load API token ─────────────────────────────────────────────────
load_dotenv()
API_TOKEN = os.getenv("CANVAS_API_TOKEN")
if not API_TOKEN:
    print("Error: set CANVAS_API_TOKEN in your .env", file=sys.stderr)
    sys.exit(1)

BASE_URL = "https://caltech.instructure.com/api/v1"
HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def get_json(path, **params):
    resp = requests.get(path, headers=HEADERS, params=params)
    resp.raise_for_status()
    return resp.json()


def download_file(url, dest):
    resp = requests.get(url, headers=HEADERS, stream=True)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(1024):
            f.write(chunk)


# ─── 1. Fetch all active courses (with term info) ─────────────────────
print("Fetching your active courses (including term data)…")
courses = get_json(
    f"{BASE_URL}/courses",
    enrollment_state="active",
    per_page=100,
    **{"include[]": "term"}
)
if not courses:
    print("No active courses found.", file=sys.stderr)
    sys.exit(1)

# Build map of term → (start, end)
term_info = {}
for c in courses:
    t = c.get("term", {})
    name = t.get("name")
    if not name or name in term_info:
        continue
    start = dateparser.parse(t["start_at"]) if t.get("start_at") else None
    end = dateparser.parse(t["end_at"]) if t.get("end_at") else None
    term_info[name] = {"start": start, "end": end}

# ─── 2. Ask user which set of courses to download ────────────────────
print("\nDownload options:")
print("  1) All active courses")
print("  2) Current term courses")
print("  3) Specific term(s)\n")
opt = input("Enter 1, 2, or 3: ").strip()

selected = []
if opt == "1":
    selected = courses

elif opt == "2":
    now = datetime.utcnow()
    current_terms = [
        name for name, info in term_info.items()
        if info["start"] and info["end"] and info["start"] <= now <= info["end"]
    ]
    if not current_terms:
        print("⚠️  Could not auto‐detect current term; falling back to choice list.")
        opt = "3"
    else:
        ct = current_terms[0]
        print(f"Detected current term: {ct}")
        selected = [c for c in courses if c["term"]["name"] == ct]

if opt == "3":
    names = sorted(term_info.keys())
    for i, name in enumerate(names, 1):
        print(f"  {i}) {name}")
    picks = input("Enter term numbers (comma-separated): ").strip()
    try:
        idxs = [int(x)-1 for x in picks.split(",")]
        chosen = {names[i] for i in idxs}
    except Exception:
        print("Invalid selection.", file=sys.stderr)
        sys.exit(1)
    selected = [c for c in courses if c["term"]["name"] in chosen]

if not selected:
    print("No courses selected. Exiting.", file=sys.stderr)
    sys.exit(1)

print(f"\n→ Will download modules from {len(selected)} course(s).\n")

# ─── 3. Download loop ───────────────────────────────────────────────────
for course in tqdm(selected, desc="Courses"):
    cid = course["id"]
    cname = course["name"].replace("/", "-")
    cdir = os.path.join(DOWNLOAD_DIR, cname)
    os.makedirs(cdir, exist_ok=True)

    modules = get_json(
        f"{BASE_URL}/courses/{cid}/modules",
        include=["items"],
        per_page=100
    )

    for module in modules:
        mname = module.get("name", "Unnamed Module").replace("/", "-")
        mdir = os.path.join(cdir, mname)
        os.makedirs(mdir, exist_ok=True)

        for item in module.get("items", []):
            if item.get("type") != "File":
                continue

            fid = item["content_id"]
            meta = get_json(f"{BASE_URL}/files/{fid}")
            fname = meta["display_name"]
            furl = meta["url"]
            dest = os.path.join(mdir, fname)

            # ── Skip if that exact file already exists ────────────────
            if os.path.exists(dest):
                tqdm.write(f"Skipping (exists): {cname}/{mname}/{fname}")
                continue

            # ── Download & pause to be polite ─────────────────────────
            try:
                download_file(furl, dest)
                time.sleep(0.5)
            except Exception as e:
                tqdm.write(f"Failed: {cname}/{mname}/{fname} → {e}")

print("\n✅ All done! Check your `downloads/` folder.")
