#!/usr/bin/env python3
"""
compare_directories.py - Given the database built by scan_photos.py, find
pairs of directories (at ANY depth, not just top-level folders) whose
contents overlap, and report what percentage of each directory's content
is found in the other.

This answers questions like: "is DIR_A/DIR_B/DIR_E's content contained in
DIR_A/DIR_C?" even though DIR_E is nested two levels deep and DIR_C is only
one level deep - depth doesn't have to match for a comparison to be valid.

Definitions:
  - "Content" of a directory = the set of DISTINCT sha256 hashes of every
    file anywhere underneath it (recursive), for files that hashed
    successfully. Two files with the same hash inside one directory don't
    inflate that directory's own count.
  - Only exact_sha256 matches count as "the same file" here - this is
    meant to be a rigorous statement about content overlap, so weaker
    signals (timestamp/GPS, filename) are intentionally not used.
  - Directory pairs where one is an ancestor of the other are skipped
    (trivially not useful - a folder always "contains" its own subfolder).
  - Candidate pairs are seeded from hashes that appear more than once in
    the database - directories that share zero duplicated files can't
    overlap, so this avoids comparing every possible pair on the drive.

Output columns: directory_a, directory_b, total_files_a, total_files_b,
common_files, pct_a_in_b, pct_b_in_a.
Read pct_a_in_b == 100 as "A's content is entirely inside B" (A subset of B),
and both == 100 as "A and B have identical content."

Usage:
    python compare_directories.py media.db --root "/Volumes/Backup Drive"
    python compare_directories.py media.db --root "/Volumes/Backup Drive" --output-dir ~/photo-project

Writes directory_comparison.csv into --output-dir (default: current directory).
"""

import argparse
import csv
import os
import sqlite3
from collections import defaultdict
from itertools import combinations
from pathlib import Path


def load_media(conn):
    cur = conn.execute("SELECT path, sha256 FROM media WHERE sha256 IS NOT NULL")
    return cur.fetchall()


def duplicated_hash_groups(conn):
    """Hashes that appear more than once, each with its list of file paths."""
    cur = conn.execute("""
        SELECT sha256, GROUP_CONCAT(path, '\x1f')
        FROM media
        WHERE sha256 IS NOT NULL
        GROUP BY sha256
        HAVING COUNT(*) > 1
    """)
    return [(sha256, paths.split("\x1f")) for sha256, paths in cur.fetchall()]


def ancestor_chain(path, root):
    """
    Directories from root down to the file's immediate containing folder,
    inclusive of both ends. Falls back to just the immediate parent if the
    file isn't actually under root (shouldn't normally happen).
    """
    parent = Path(path).parent
    try:
        rel = parent.relative_to(root)
    except ValueError:
        return [parent]
    chain = [root]
    cur = root
    for part in rel.parts:
        cur = cur / part
        chain.append(cur)
    return chain


def is_ancestor_or_self(a, b):
    """True if a == b, or a is an ancestor directory of b."""
    if a == b:
        return True
    try:
        b.relative_to(a)
        return True
    except ValueError:
        return False


def hash_set_for_dir(directory, all_media, cache):
    """Recursive set of distinct hashes for everything under `directory`."""
    if directory in cache:
        return cache[directory]
    result = set()
    for path, sha256 in all_media:
        try:
            Path(path).relative_to(directory)
            result.add(sha256)
        except ValueError:
            pass
    cache[directory] = result
    return result


def display_path(directory, root):
    """Show the path rooted at --root's own folder name, e.g. 'DIR_A/DIR_B/DIR_E'."""
    try:
        rel = directory.relative_to(root.parent)
        return str(rel)
    except ValueError:
        return str(directory)


def main():
    ap = argparse.ArgumentParser(description="Compare directory contents for subset/overlap relationships.")
    ap.add_argument("db", help="Path to media.db")
    ap.add_argument("--root", default=None,
                     help="Scan root. Recommended - without it, the common path of all files is used, "
                          "and display paths fall back to full absolute paths.")
    ap.add_argument("--output-dir", default=".",
                     help="Directory to write directory_comparison.csv into (created if it doesn't exist)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    all_media = load_media(conn)
    if not all_media:
        print("No hashed files found in the database.")
        return

    if args.root:
        root = Path(args.root).resolve()
    else:
        root = Path(os.path.commonpath([p for p, _ in all_media])).resolve()
        print(f"No --root given; using common path as root: {root}")

    groups = duplicated_hash_groups(conn)
    print(f"{len(groups)} distinct files have 2+ copies on the drive; deriving candidate directory pairs...")

    candidate_pairs = set()
    for _sha256, paths in groups:
        dirs_touched = set()
        for p in paths:
            dirs_touched.update(ancestor_chain(p, root))
        for a, b in combinations(sorted(dirs_touched, key=str), 2):
            if is_ancestor_or_self(a, b):
                continue
            candidate_pairs.add(frozenset((a, b)))

    print(f"{len(candidate_pairs)} candidate directory pairs to evaluate...")

    cache = {}
    rows = []
    for pair in candidate_pairs:
        a, b = sorted(pair, key=str)
        hashes_a = hash_set_for_dir(a, all_media, cache)
        hashes_b = hash_set_for_dir(b, all_media, cache)
        common = len(hashes_a & hashes_b)
        total_a, total_b = len(hashes_a), len(hashes_b)
        pct_a_in_b = round(100 * common / total_a, 1) if total_a else 0.0
        pct_b_in_a = round(100 * common / total_b, 1) if total_b else 0.0
        rows.append((display_path(a, root), display_path(b, root),
                      total_a, total_b, common, pct_a_in_b, pct_b_in_a))

    rows.sort(key=lambda r: -max(r[5], r[6]))  # highest overlap first

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "directory_comparison.csv"

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["directory_a", "directory_b", "total_files_a", "total_files_b",
                          "common_files", "pct_a_in_b", "pct_b_in_a"])
        writer.writerows(rows)

    print(f"Wrote {len(rows)} directory-pair comparisons to {output_path}")


if __name__ == "__main__":
    main()
