#!/usr/bin/env python3
"""Find old YouTube videos with very high view counts, for remake research.

Uses the official YouTube Data API v3 (search.list + videos.list). Standard
library only. Writes a CSV of candidates, deduplicated across queries.

Key lookup, first match wins:
  1. YOUTUBE_API_KEY environment variable
  2. macOS Keychain item "youtube-data-api"
     (security add-generic-password -a "$USER" -s youtube-data-api -w)
  3. none: send a placeholder, for cloud sessions whose proxy injects the key

Quota: each search page costs 100 units, each videos.list call 1 unit.
The free daily quota is 10,000 units.

Usage:
  python3 finder/find_videos.py --queries finder/queries.txt
  python3 finder/find_videos.py -q "bible story animated" --min-views 10000000
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://www.googleapis.com/youtube/v3"
PLACEHOLDER_KEY = "injected-by-proxy"


def api_key():
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if key:
        return key
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", "youtube-data-api", "-w"],
                capture_output=True, text=True, check=True,
            )
            if out.stdout.strip():
                return out.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    return PLACEHOLDER_KEY


def get(path, params, key):
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"X-Goog-Api-Key": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(body)["error"]["message"]
        except (ValueError, KeyError):
            msg = body[:300]
        sys.exit(f"YouTube API error {e.code} on {path}: {msg}")


def duration_seconds(iso):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def search(query, key, published_before, pages, language):
    ids, token = [], None
    for _ in range(pages):
        params = {
            "part": "id", "q": query, "type": "video", "order": "viewCount",
            "maxResults": 50, "publishedBefore": published_before,
        }
        if language:
            params["relevanceLanguage"] = language
        if token:
            params["pageToken"] = token
        data = get("search", params, key)
        ids += [i["id"]["videoId"] for i in data.get("items", []) if i.get("id", {}).get("videoId")]
        token = data.get("nextPageToken")
        if not token:
            break
    return ids


def details(ids, key):
    out = []
    for n in range(0, len(ids), 50):
        data = get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids[n:n + 50])}, key)
        out += data.get("items", [])
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-q", "--query", action="append", default=[], help="search query (repeatable)")
    p.add_argument("--queries", help="file with one query per line; '# direction' lines group them")
    p.add_argument("--min-views", type=int, default=10_000_000)
    p.add_argument("--published-before", default="2020-01-01", help="YYYY-MM-DD")
    p.add_argument("--min-minutes", type=float, default=2, help="skip Shorts and clips")
    p.add_argument("--pages", type=int, default=1, help="search pages per query (100 units each)")
    p.add_argument("--language", default="en", help="relevanceLanguage, '' for any")
    p.add_argument("--out", default="data/candidates.csv")
    a = p.parse_args()

    queries = [(q, "") for q in a.query]
    if a.queries:
        direction = ""
        for line in open(a.queries, encoding="utf-8"):
            line = line.strip()
            if line.startswith("#"):
                direction = line.lstrip("# ").strip()
            elif line:
                queries.append((line, direction))
    if not queries:
        p.error("give --query or --queries")

    key = api_key()
    before = f"{a.published_before}T00:00:00Z"
    found = {}
    for q, direction in queries:
        ids = [i for i in search(q, key, before, a.pages, a.language) if i not in found]
        for v in details(ids, key):
            views = int(v.get("statistics", {}).get("viewCount", 0))
            secs = duration_seconds(v.get("contentDetails", {}).get("duration"))
            if views < a.min_views or secs < a.min_minutes * 60:
                continue
            s = v["snippet"]
            found[v["id"]] = {
                "direction": direction,
                "query": q,
                "title": s.get("title", ""),
                "channel": s.get("channelTitle", ""),
                "published": s.get("publishedAt", "")[:10],
                "views": views,
                "likes": v.get("statistics", {}).get("likeCount", ""),
                "comments": v.get("statistics", {}).get("commentCount", ""),
                "minutes": round(secs / 60, 1),
                "url": f"https://www.youtube.com/watch?v={v['id']}",
                "description": " ".join(s.get("description", "").split())[:500],
            }
        print(f"{len(found):4d} total after: {q}", file=sys.stderr)

    rows = sorted(found.values(), key=lambda r: r["views"], reverse=True)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        fields = ["direction", "query", "title", "channel", "published", "views", "likes",
                  "comments", "minutes", "url", "description"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} videos to {a.out} ({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC)", file=sys.stderr)


if __name__ == "__main__":
    main()
