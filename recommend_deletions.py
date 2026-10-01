#!/usr/bin/env python3
"""
recommend_deletions.py - Given the directory_comparison.csv produced by
compare_directories.py, surface directories that are good candidates for
deletion, split into two tiers:

  1. identical_directory_clusters.csv
     Directories whose content is 100% identical to each other (pct_a_in_b
     AND pct_b_in_a both 100). These are grouped transitively - if A==B
     and B==C, all three are reported as one 3-way cluster rather than
     three separate pairwise rows. You pick which one to keep; the others
     are true duplicates of it.

  2. redundant_subsets.csv
     Directories whose content is fully contained in one or more OTHER
     directories (only one side at 100%), grouped by the redundant
     directory and listing everywhere its content already exists. Filtered
     by --min-files, since a small/coincidental match (e.g. a folder with
     1-2 files that happen to also exist in some huge unrelated backup) is
     technically a "clean subset" but not meaningful evidence the folder
     itself is redundant.

CAVEATS - read before deleting anything:
  - This is based only on files that hashed successfully in the original
    scan. Hidden files, unreadable files, and anything excluded during
    scanning aren't represented, and a directory that looks like a clean
    100% match could still contain such files.
  - File COUNT overlap, not disk space - a directory with many tiny files
    and one with few huge files aren't weighted by size here.
  - For identical clusters, nothing here tells you which copy is
    "the good one" (right location, best organized) - that's your call.
  - Review before deleting. This narrows down where to look, it doesn't
    make the decision for you.

Usage:
    python recommend_deletions.py directory_comparison.csv
    python recommend_deletions.py directory_comparison.csv --min-files 20 --output-dir ~/backup-project
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path


def load_rows(csv_path):
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for r in reader:
            r["total_files_a"] = int(r["total_files_a"])
            r["total_files_b"] = int(r["total_files_b"])
            r["common_files"] = int(r["common_files"])
            r["pct_a_in_b"] = float(r["pct_a_in_b"])
            r["pct_b_in_a"] = float(r["pct_b_in_a"])
            rows.append(r)
        return rows


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def build_identical_clusters(rows):
    uf = UnionFind()
    file_counts = {}  # directory -> total_files, for sizing the cluster
    identical_rows = [r for r in rows if r["pct_a_in_b"] == 100.0 and r["pct_b_in_a"] == 100.0]

    for r in identical_rows:
        uf.union(r["directory_a"], r["directory_b"])
        file_counts[r["directory_a"]] = r["total_files_a"]
        file_counts[r["directory_b"]] = r["total_files_b"]

    clusters = defaultdict(set)
    for directory in file_counts:
        clusters[uf.find(directory)].add(directory)

    result = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        file_count = max(file_counts[m] for m in members)
        result.append((file_count, sorted(members)))

    result.sort(key=lambda x: -x[0])
    return result


def build_redundant_subsets(rows, min_files):
    redundant_map = {}  # directory -> {total_files, covered_by: set()}

    for r in rows:
        if r["pct_a_in_b"] == 100.0 and r["pct_b_in_a"] == 100.0:
            continue  # handled by the identical-cluster tier
        if r["pct_a_in_b"] == 100.0:
            redundant, total_files, coverer = r["directory_a"], r["total_files_a"], r["directory_b"]
        elif r["pct_b_in_a"] == 100.0:
            redundant, total_files, coverer = r["directory_b"], r["total_files_b"], r["directory_a"]
        else:
            continue

        entry = redundant_map.setdefault(redundant, {"total_files": total_files, "covered_by": set()})
        entry["covered_by"].add(coverer)

    result = [
        (info["total_files"], directory, sorted(info["covered_by"]))
        for directory, info in redundant_map.items()
        if info["total_files"] >= min_files
    ]
    result.sort(key=lambda x: -x[0])
    return result


def main():
    ap = argparse.ArgumentParser(description="Recommend directories to delete from a directory_comparison.csv.")
    ap.add_argument("comparison_csv", help="Path to directory_comparison.csv from compare_directories.py")
    ap.add_argument("--min-files", type=int, default=10,
                     help="Minimum file count for a directory to appear in the redundant-subsets tier "
                          "(filters out small/coincidental matches). Default: 10")
    ap.add_argument("--output-dir", default=".",
                     help="Directory to write identical_directory_clusters.csv and redundant_subsets.csv into "
                          "(created if it doesn't exist)")
    args = ap.parse_args()

    rows = load_rows(args.comparison_csv)
    print(f"Loaded {len(rows)} rows from {args.comparison_csv}")

    clusters = build_identical_clusters(rows)
    subsets = build_redundant_subsets(rows, args.min_files)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    clusters_path = output_dir / "identical_directory_clusters.csv"
    subsets_path = output_dir / "redundant_subsets.csv"

    with open(clusters_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["file_count", "directory_count", "directories"])
        for file_count, members in clusters:
            writer.writerow([file_count, len(members), " | ".join(members)])

    with open(subsets_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["file_count", "redundant_directory", "fully_covered_by"])
        for file_count, directory, covered_by in subsets:
            writer.writerow([file_count, directory, " | ".join(covered_by)])

    print(f"Identical-content clusters: {len(clusters)} (>=2 directories each)")
    print(f"Redundant subset directories (>= {args.min_files} files): {len(subsets)}")
    print(f"Wrote {clusters_path}")
    print(f"Wrote {subsets_path}")


if __name__ == "__main__":
    main()
