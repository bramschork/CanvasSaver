#!/usr/bin/env python3
"""
download_modules.py

Interactive Canvas module downloader.

Options on startup:
  1) All courses you’ve ever taken (any enrollment + any workflow state, minus unpublished)
  2) Select specific course(s) by number (only published)

Downloads go into:
  downloads/
    <Course Name>/
      <Module Name>/
        <File>
"""

import os
import sys
import time
import requests
from requests.exceptions import HTTPError
from dotenv import load_dotenv
from tqdm import tqdm

# ─── Requirements ────────────────────────────────────────────────────
# pip install requests python-dotenv tqdm

# ─── Load API token ───────────────────────────────────────────────────
load_dotenv()
API_TOKEN = os.getenv("CANVAS_API_TOKEN")
if not API_TOKEN:
    print("Error: set CANVAS_API_TOKEN in your .env", file=sys.stderr)
    sys.exit(1)

BASE_URL = "https://caltech.instructure.com/api/v1"
HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def get_json(path, params=None):
    """Perform a GET request and return parsed JSON."""
    resp = requests.get(path, headers=HEADERS, params=params)
    resp.raise_for_status()
    return resp.json()


def download_file(url, dest):
    """Download a file from `url` and save it to `dest`."""
    resp = requests.get(url, headers=HEADERS, stream=True)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(1024):
            f.write(chunk)


def get_all_courses():
    """
    Fetch EVERY course (any enrollment + any workflow state)
    by paging through /api/v1/courses until no more results.
    """
    all_courses = []
    page = 1
    while True:
        params = [
            ("per_page", "100"),
            ("page", str(page)),
            ("include[]", "term"),
            # include every enrollment state
            ("enrollment_state[]", "active"),
            ("enrollment_state[]", "invited_or_pending"),
            ("enrollment_state[]", "completed"),
            # include every workflow state
            ("state[]", "all"),
        ]
        batch = get_json(f"{BASE_URL}/courses", params=params)
        if not batch:
            break
        all_courses.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return all_courses


# ─── 1. Fetch your courses ─────────────────────────────────────────────
print("Fetching all courses (any enrollment + any workflow state)…")
courses = get_all_courses()
if not courses:
    print("No courses found. Check your token/permissions.", file=sys.stderr)
    sys.exit(1)

# Filter out unpublished courses and report count
unpublished = [c for c in courses if c.get("workflow_state") == "unpublished"]
if unpublished:
    print(f"⚠️  Skipping {len(unpublished)} unpublished course(s).")
courses = [c for c in courses if c.get("workflow_state") != "unpublished"]

# ─── 2. Prompt user for which courses to download ──────────────────────
print("\nDownload options:")
print("  1) All published courses you’ve ever taken")
print("  2) Select specific published course(s)\n")

opt = input("Enter 1 or 2: ").strip()
selected = []

if opt == "1":
    selected = courses

elif opt == "2":
    # List all published courses with index
    for i, c in enumerate(courses, 1):
        term = c.get("term", {}).get("name", "No Term")
        print(f"  {i}) {c['name']} [{term}]")
    picks = input("\nEnter course numbers separated by spaces: ").strip()
    try:
        idxs = [int(x) - 1 for x in picks.split() if x.isdigit()]
        selected = [courses[i] for i in idxs]
    except Exception:
        print("Invalid selection, exiting.", file=sys.stderr)
        sys.exit(1)

else:
    print("Unknown option; exiting.", file=sys.stderr)
    sys.exit(1)

if not selected:
    print("No courses selected; exiting.", file=sys.stderr)
    sys.exit(1)

print(f"\n→ Will download from {len(selected)} course(s).\n")

# ─── 3. Download modules & files ──────────────────────────────────────
for course in tqdm(selected, desc="Courses"):
    cid = course["id"]
    cname = course["name"].replace("/", "-")
    cdir = os.path.join(DOWNLOAD_DIR, cname)
    os.makedirs(cdir, exist_ok=True)

    # Try fetching modules; skip if access is forbidden
    try:
        modules = get_json(
            f"{BASE_URL}/courses/{cid}/modules",
            params=[("per_page", "100"), ("include[]", "items")]
        )
    except HTTPError as e:
        if e.response.status_code == 403:
            tqdm.write(
                f"Warning: cannot access modules for course {cid} ({cname}), skipping.")
            continue
        else:
            raise

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

            # Skip if already exists
            if os.path.exists(dest):
                tqdm.write(f"Skipping (exists): {cname}/{mname}/{fname}")
                continue

            try:
                download_file(furl, dest)
                time.sleep(0.5)
            except Exception as e:
                tqdm.write(f"Failed: {cname}/{mname}/{fname} → {e}")

print("\n✅ Done! Check your `downloads/` folder.")
