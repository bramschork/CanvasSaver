#!/usr/bin/env python3
"""
download_submissions.py

Interactive Canvas submission downloader.

On startup you get two options:
  1) Download submissions from all courses you’ve ever taken
  2) Download submissions from specific course(s) you pick

Submissions are saved under:
  downloads/submissions/<Course Name>/<Assignment Name>/
"""

import os
import sys
import time
import requests
from requests.exceptions import HTTPError
from dotenv import load_dotenv

# ─── Requirements ────────────────────────────────────────────────────
# pip install requests python-dotenv

# ─── Load .env ────────────────────────────────────────────────────────
load_dotenv()  # reads CANVAS_URL and CANVAS_TOKEN from .env
CANVAS_URL = os.getenv("CANVAS_URL")
API_TOKEN = os.getenv("CANVAS_TOKEN")
if not CANVAS_URL or not API_TOKEN:
    print("Error: set CANVAS_URL and CANVAS_TOKEN in your .env", file=sys.stderr)
    sys.exit(1)

HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}
DOWNLOAD_ROOT = os.path.join(os.getcwd(), "downloads", "submissions")


def sanitize(name: str) -> str:
    """Make a string safe for use as a filename."""
    return "".join(c for c in name if c.isalnum() or c in " ._-").strip()


def get_json(url: str, params=None):
    """
    GET with Canvas paging support.
    Accepts `params` either as a dict or as a list of (key, value) tuples.
    Always ensures per_page=100 on the first request.
    """
    results = []

    # normalize incoming params to list of tuples
    if isinstance(params, dict):
        p_list = list(params.items())
    elif isinstance(params, list):
        p_list = list(params)
    else:
        p_list = []

    # ensure per_page=100
    if not any(k == "per_page" for k, _ in p_list):
        p_list.append(("per_page", "100"))

    # paging loop
    while url:
        resp = requests.get(url, headers=HEADERS, params=p_list)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            results.extend(data)
        else:
            results.append(data)

        # find next link
        link = resp.headers.get("link", "")
        next_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                next_url = part[part.find("<") + 1: part.find(">")]
        url = next_url

        # clear params after first request
        p_list = []
    return results


def get_all_courses():
    """
    Fetch ALL courses you’ve ever been enrolled in (active, invited, completed)
    across every workflow state.
    """
    all_courses = []
    page = 1
    while True:
        params = [
            ("per_page", "100"),
            ("page", str(page)),
            ("include[]", "term"),
            ("enrollment_state[]", "active"),
            ("enrollment_state[]", "invited_or_pending"),
            ("enrollment_state[]", "completed"),
            ("state[]", "all"),
        ]
        batch = get_json(f"{CANVAS_URL}/api/v1/courses", params=params)
        if not batch:
            break
        all_courses.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return all_courses


def fetch_my_submissions(course_id: int):
    """
    List all your submissions in a course, including history & assignment info.
    """
    url = f"{CANVAS_URL}/api/v1/courses/{course_id}/students/submissions"
    params = {
        "student_ids[]": "self",
        "include[]": ["submission_history", "assignment"],
    }
    return get_json(url, params=params)


def download_file(url: str, path: str):
    """Download file if not present already."""
    if os.path.exists(path):
        print(f"  Skipping (exists): {path}")
        return
    resp = requests.get(url, headers=HEADERS, stream=True)
    resp.raise_for_status()
    with open(path, "wb") as f:
        for chunk in resp.iter_content(4096):
            f.write(chunk)
    print(f"  Downloaded: {path}")


def main():
    # 1) Fetch all courses
    print("Fetching all courses…")
    courses = get_all_courses()
    if not courses:
        print("No courses found. Check your token/permissions.", file=sys.stderr)
        sys.exit(1)

    # 2) Prompt for option
    print(f"\nFound {len(courses)} course(s).")
    print("Download options:")
    print("  1) All courses")
    print("  2) Select specific course(s)\n")
    opt = input("Enter 1 or 2: ").strip()

    if opt == "1":
        selected = courses

    elif opt == "2":
        # list courses with index and term
        for i, c in enumerate(courses, 1):
            term = c.get("term", {}).get("name", "No Term")
            print(f"  {i}) {c['name']} [{term}]")
        picks = input("\nEnter course numbers separated by spaces: ").strip()
        try:
            idxs = [int(x) - 1 for x in picks.split() if x.isdigit()]
            selected = [courses[i] for i in idxs]
        except Exception:
            print("Invalid selection; exiting.", file=sys.stderr)
            sys.exit(1)

    else:
        print("Unknown option; exiting.", file=sys.stderr)
        sys.exit(1)

    if not selected:
        print("No courses selected; exiting.", file=sys.stderr)
        sys.exit(1)

    print(f"\n→ Downloading submissions from {len(selected)} course(s)…")

    # 3) Download submissions for each selected course
    for course in selected:
        cid = course["id"]
        cname = sanitize(course["name"])
        print(f"\n== Course: {cname} ==")
        try:
            subs = fetch_my_submissions(cid)
        except HTTPError as e:
            print(
                f"Warning: cannot fetch submissions for {cname} ({e}), skipping.")
            continue

        for sub in subs:
            assign = sub.get("assignment", {})
            aname = sanitize(assign.get("name")
                             or f"assignment_{sub['assignment_id']}")
            # collect attachments
            atts = []
            for hist in sub.get("submission_history", []):
                atts.extend(hist.get("attachments", []))
            atts.extend(sub.get("attachments", []))
            if not atts:
                continue
            folder = os.path.join(DOWNLOAD_ROOT, cname, aname)
            os.makedirs(folder, exist_ok=True)
            print(f" → {aname} ({len(atts)} file(s))")
            for att in atts:
                fname = sanitize(att.get("filename")
                                 or att.get("display_name")
                                 or att["url"].split("/")[-1])
                path = os.path.join(folder, fname)
                download_file(att["url"], path)
                time.sleep(0.2)

    print("\n✅ All done! Check the downloads/submissions folder.")


if __name__ == "__main__":
    main()
