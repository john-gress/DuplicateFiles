#!/usr/bin/env python3
"""
find_duplicates.py - Analyze the database built by scan_photos.py and
report groups of likely-duplicate photos/videos, at four confidence tiers:

  1. exact_sha256           - byte-identical files (certain duplicates)
  2. same_datetime_gps       - same capture time + GPS coords (near-certain;
                                catches copies that were resized/re-encoded
                                and so no longer hash identically)
  3. same_filename_and_size  - same normalized filename (ignoring "(1)",
                                "-copy" etc. suffixes) AND same byte size
                                (fairly strong, independent of metadata/hash)
  4. same_filename_only      - same normalized filename, different size
                                (weak signal on its own - cameras/phones
                                often reuse sequential filenames like
                                IMG_0001.jpg after a card reset or on a new
                                device, so this tier is for manual review)

Each duplicate group is tagged with the set of directories its files live
in, and the report is sorted by that so groups touching the same folder(s)
cluster together - no need to pick one folder as a canonical "top" dir,
since a group can legitimately span more than two locations.

A second file, the directory-pair summary, counts how many duplicate files
each pair of directories shares - useful for spotting a folder that's
almost entirely redundant with another.

Usage:
    python find_duplicates.py media.db                                    # exact_sha256 only (default)
    python find_duplicates.py media.db --datetime-gps                     # exact + date/GPS
    python find_duplicates.py media.db --filename-size --filename-only
    python find_duplicates.py media.db --all                              # every tier
    python find_duplicates.py media.db --all --root "/Volumes/Backup Drive" --output-dir ~/photo-project

Writes duplicates_report.csv and directory_pairs.csv into --output-dir (default: current directory).
"""

import argparse
import csv
import re
import sqlite3
from collections import defaultdict
from itertools import combinations
from pathlib import Path

COPY_SUFFIX_RE = re.compile(
    r"(?:\s*\(\d+\)$|[-_ ]copy(?:\s*\d*)?$|\s*-\s*copy$)",
    re.IGNORECASE,
)


def normalize_filename(filename):
    """Strip extension, lowercase, and drop common copy suffixes so
    'IMG_1234.jpg' and 'IMG_1234 (1).jpg' are recognized as the same name."""
    stem = Path(filename).stem
    stem = COPY_SUFFIX_RE.sub("", stem).strip()
    return stem.lower()


def group_rows(rows, key_func):
    groups = defaultdict(list)
    for row in rows:
        groups[key_func(row)].append(row)
    return {k: v for k, v in groups.items() if len(v) > 1}


def exact_duplicates(conn):
    cur = conn.execute("""
        SELECT sha256, COUNT(*) as n, GROUP_CONCAT(path, ' | ') as paths
        FROM media
        WHERE sha256 IS NOT NULL
        GROUP BY sha256
        HAVING n > 1
    """)
    return cur.fetchall()


def same_datetime_gps_duplicates(conn):
    cur = conn.execute("""
        SELECT date_taken, gps_lat, gps_lon, COUNT(*) as n, GROUP_CONCAT(path, ' | ') as paths
        FROM media
        WHERE date_taken IS NOT NULL AND gps_lat IS NOT NULL
        GROUP BY date_taken, gps_lat, gps_lon
        HAVING n > 1
    """)
    return cur.fetchall()


def filename_duplicates(conn):
    cur = conn.execute("SELECT path, filename, size_bytes FROM media")
    rows = cur.fetchall()

    by_name_and_size = group_rows(rows, lambda r: (normalize_filename(r[1]), r[2]))

    by_name = group_rows(rows, lambda r: normalize_filename(r[1]))
    name_and_size_keys = {name for (name, _size) in by_name_and_size}
    by_name_only = {
        name: members for name, members in by_name.items()
        if name not in name_and_size_keys
    }

    return by_name_and_size, by_name_only


def dir_key_for_path(path, root=None, level=None):
    """
    Directory label for a file, used for grouping/sorting the report.
    - No --root given: the file's immediate containing folder (absolute).
    - --root given: the folder path relative to root, truncated to the
      first `level` path segments if --dir-level was given (so files deep
      inside the same top-level folder are treated as one directory).
    """
    parent = Path(path).parent
    if root:
        try:
            rel = parent.relative_to(root)
            parts = rel.parts
            if level is not None and len(parts) > level:
                parts = parts[:level]
            return str(Path(*parts)) if parts else "."
        except ValueError:
            pass  # file isn't under root; fall through to absolute path
    return str(parent)


def directories_for_group(paths, root, level):
    dirs = sorted({dir_key_for_path(p, root, level) for p in paths})
    return dirs


def main():
    ap = argparse.ArgumentParser(description="Find duplicate photos/videos from the scan database.")
    ap.add_argument("db", help="Path to media.db")
    ap.add_argument("--output-dir", default=".",
                     help="Directory to write duplicates_report.csv and directory_pairs.csv into "
                          "(created if it doesn't exist)")
    ap.add_argument("--exact", action="store_true",
                     help="Include exact_sha256 matches (this is the default if no tier flags are given)")
    ap.add_argument("--datetime-gps", action="store_true", help="Include same_datetime_gps matches")
    ap.add_argument("--filename-size", action="store_true", help="Include same_filename_and_size matches")
    ap.add_argument("--filename-only", action="store_true",
                     help="Include same_filename_only matches (weakest signal, manual review)")
    ap.add_argument("--all", action="store_true", help="Include every tier")
    ap.add_argument("--root", default=None,
                     help="Scan root, to express directories relative to it instead of full absolute paths")
    ap.add_argument("--dir-level", type=int, default=None,
                     help="Group by the first N path segments under --root (e.g. 1 = top-level folder). "
                          "Requires --root. Omit for the immediate containing folder.")
    args = ap.parse_args()

    if args.all:
        tiers = {"exact", "datetime_gps", "filename_size", "filename_only"}
    else:
        tiers = set()
        if args.exact:
            tiers.add("exact")
        if args.datetime_gps:
            tiers.add("datetime_gps")
        if args.filename_size:
            tiers.add("filename_size")
        if args.filename_only:
            tiers.add("filename_only")
        if not tiers:
            tiers = {"exact"}  # default when no flags are given

    conn = sqlite3.connect(args.db)

    exact = exact_duplicates(conn) if "exact" in tiers else []
    same_meta = same_datetime_gps_duplicates(conn) if "datetime_gps" in tiers else []
    if "filename_size" in tiers or "filename_only" in tiers:
        by_name_and_size, by_name_only = filename_duplicates(conn)
        if "filename_size" not in tiers:
            by_name_and_size = {}
        if "filename_only" not in tiers:
            by_name_only = {}
    else:
        by_name_and_size, by_name_only = {}, {}

    # Tracks the exact file-sets already accepted, tier by tier, so a weaker
    # tier is skipped only when it names the SAME set of files already
    # covered by a stronger tier (see module docstring).
    reported_sets = set()
    skipped_counts = defaultdict(int)
    report_rows = []          # (match_type, key, count, paths_list, directories_list)
    pair_counts = defaultdict(int)   # (dirA, dirB) -> number of duplicate groups sharing both

    def already_reported(paths):
        return frozenset(paths) in reported_sets

    def accept(match_type, key, paths):
        reported_sets.add(frozenset(paths))
        dirs = directories_for_group(paths, args.root, args.dir_level)
        report_rows.append((match_type, key, len(paths), paths, dirs))
        for a, b in combinations(dirs, 2):
            pair_counts[tuple(sorted((a, b)))] += 1

    for sha256, n, paths_str in exact:
        accept("exact_sha256", sha256, paths_str.split(" | "))

    for date_taken, lat, lon, n, paths_str in same_meta:
        paths = paths_str.split(" | ")
        if already_reported(paths):
            skipped_counts["same_datetime_gps"] += 1
            continue
        accept("same_datetime_gps", f"{date_taken} @ {lat},{lon}", paths)

    for (name, size), members in by_name_and_size.items():
        paths = [m[0] for m in members]
        if already_reported(paths):
            skipped_counts["same_filename_and_size"] += 1
            continue
        accept("same_filename_and_size", f"{name} ({size} bytes)", paths)

    for name, members in by_name_only.items():
        paths = [m[0] for m in members]
        if already_reported(paths):
            skipped_counts["same_filename_only"] += 1
            continue
        accept("same_filename_only", name, paths)

    # Sort so groups touching the same directory (or directory combination)
    # cluster together, rather than being ordered by match tier.
    tier_rank = {"exact_sha256": 0, "same_datetime_gps": 1,
                 "same_filename_and_size": 2, "same_filename_only": 3}
    report_rows.sort(key=lambda r: ("; ".join(r[4]), tier_rank[r[0]], r[1]))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "duplicates_report.csv"
    pairs_path = output_dir / "directory_pairs.csv"

    with open(report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["directories", "match_type", "key", "count", "paths"])
        for match_type, key, count, paths, dirs in report_rows:
            writer.writerow(["; ".join(dirs), match_type, key, count, " | ".join(paths)])

    with open(pairs_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["directory_a", "directory_b", "shared_duplicate_groups"])
        for (a, b), n in sorted(pair_counts.items(), key=lambda kv: -kv[1]):
            writer.writerow([a, b, n])

    print(f"Tiers included: {', '.join(sorted(tiers))}")
    if "exact" in tiers:
        print(f"Exact duplicates (identical bytes): {len(exact)} groups")
    if "datetime_gps" in tiers:
        print(f"Same date+GPS: {len(same_meta)} groups "
              f"({skipped_counts['same_datetime_gps']} already covered by exact match, skipped)")
    if "filename_size" in tiers:
        print(f"Same filename + size: {len(by_name_and_size)} groups "
              f"({skipped_counts['same_filename_and_size']} already covered by a stronger tier, skipped)")
    if "filename_only" in tiers:
        print(f"Same filename only (weak signal, review manually): {len(by_name_only)} groups "
              f"({skipped_counts['same_filename_only']} already covered by a stronger tier, skipped)")
    print(f"Report written to {report_path}")
    print(f"Directory-pair summary written to {pairs_path}")


if __name__ == "__main__":
    main()
