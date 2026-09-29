#!/usr/bin/env python3
"""
scan_files.py - Scan a drive for ALL files (not just photos/videos),
extract available metadata via exiftool, compute a blake2b hash of each
file, and store everything in a SQLite database (plus optional CSV
export) for duplicate detection and directory comparison.

Hidden files and directories (anything starting with ".") are skipped
entirely - this covers AppleDouble sidecar files (._filename), .git,
.Trash, .DS_Store, caches, etc. in one rule. A few common non-hidden junk
directories (node_modules, __pycache__, OS recycle-bin folders) are also
skipped by default; add more with --exclude.

Hashing uses blake2b (Python's standard library, no extra install) rather
than SHA-256. For this use case - identifying identical files on your own
backup drive, not defending against someone deliberately crafting a
colliding file - cryptographic strength beyond "accidental collisions are
astronomically unlikely" isn't needed, and blake2b is meaningfully faster
in pure software. File size is also stored, which would catch even a
theoretical hash collision between differently-sized files.

Requires the exiftool command-line tool to be installed and on PATH:
    https://exiftool.org/
    Mac:     brew install exiftool
    Linux:   apt install libimage-exiftool-perl
    Windows: download from exiftool.org and rename exiftool(-k).exe to exiftool.exe

Usage:
    python scan_files.py /path/to/drive --output-dir ~/photo-project
    python scan_files.py /path/to/drive --output-dir ~/photo-project --exclude "*.app" --exclude Downloads

(writes files.db and files.csv into --output-dir; created if needed)
"""

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

# Patterns passed to exiftool's -i (ignore) option, which accepts wildcards.
# ".*" alone covers all hidden files/dirs: .git, .cache, .Trash, .DS_Store,
# AppleDouble "._*" sidecars, etc. - one rule instead of many.
DEFAULT_EXCLUDE = [
    ".*",
    "node_modules",
    "__pycache__",
    "$RECYCLE.BIN",
    "System Volume Information",
]


def check_exiftool():
    if shutil.which("exiftool") is None:
        sys.exit(
            "exiftool not found on PATH. Install it from https://exiftool.org/ "
            "(brew install exiftool / apt install libimage-exiftool-perl / "
            "download the Windows build and rename to exiftool.exe) and try again."
        )


def compute_hash(path, block_size=65536):
    h = hashlib.blake2b()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(block_size), b""):
            h.update(chunk)
    return h.hexdigest()


def run_exiftool(root, exclude_patterns):
    """
    Run exiftool once, recursively, over the whole tree and get JSON metadata
    for every file not matched by an --exclude pattern. -n gives GPS
    coordinates (when present) as plain signed decimal degrees.
    """
    cmd = ["exiftool", "-r", "-j", "-n"]
    for pattern in exclude_patterns:
        cmd += ["-i", pattern]
    cmd += [str(root)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode not in (0, 1):  # 1 = exiftool completed with minor per-file warnings
        print(result.stderr, file=sys.stderr)
        sys.exit(f"exiftool failed with exit code {result.returncode}")
    if not result.stdout.strip():
        return []
    return json.loads(result.stdout)


def build_row(record):
    path = Path(record["SourceFile"])
    return {
        "path": str(path),
        "filename": path.name,
        "size_bytes": path.stat().st_size if path.exists() else None,
        "content_hash": None,  # filled in separately
        "width": record.get("ImageWidth"),
        "height": record.get("ImageHeight"),
        "duration_seconds": record.get("Duration"),
        "date_taken": record.get("DateTimeOriginal") or record.get("CreateDate"),
        "camera_make": record.get("Make"),
        "camera_model": record.get("Model"),
        "gps_lat": record.get("GPSLatitude"),
        "gps_lon": record.get("GPSLongitude"),
        "mime_type": record.get("MIMEType"),
        "file_type": record.get("FileType"),
        "error": None,
    }


def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE,
            filename TEXT,
            size_bytes INTEGER,
            content_hash TEXT,
            width INTEGER,
            height INTEGER,
            duration_seconds REAL,
            date_taken TEXT,
            camera_make TEXT,
            camera_model TEXT,
            gps_lat REAL,
            gps_lon REAL,
            mime_type TEXT,
            file_type TEXT,
            error TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_content_hash ON files(content_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date_taken ON files(date_taken)")
    conn.commit()
    return conn


def insert_row(conn, row):
    conn.execute("""
        INSERT OR REPLACE INTO files
        (path, filename, size_bytes, content_hash, width, height, duration_seconds,
         date_taken, camera_make, camera_model, gps_lat, gps_lon,
         mime_type, file_type, error)
        VALUES (:path, :filename, :size_bytes, :content_hash, :width, :height, :duration_seconds,
                :date_taken, :camera_make, :camera_model, :gps_lat, :gps_lon,
                :mime_type, :file_type, :error)
    """, row)


def export_csv(conn, csv_path):
    cur = conn.execute("SELECT * FROM files")
    cols = [d[0] for d in cur.description]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        writer.writerows(cur.fetchall())


def main():
    ap = argparse.ArgumentParser(description="Scan a drive for all files and build a metadata/hash database.")
    ap.add_argument("root", help="Root directory to scan")
    ap.add_argument("--output-dir", default=".",
                     help="Directory to write files.db and files.csv into (created if it doesn't exist)")
    ap.add_argument("--exclude", action="append", default=[],
                     help="Additional file/directory name pattern to skip (wildcards allowed, e.g. '*.app'). "
                          "Repeatable. Defaults already skip hidden files/dirs and: " + ", ".join(DEFAULT_EXCLUDE[1:]))
    args = ap.parse_args()

    check_exiftool()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / "files.db"
    csv_path = output_dir / "files.csv"

    exclude_patterns = DEFAULT_EXCLUDE + args.exclude
    print(f"Excluding: {', '.join(exclude_patterns)}", file=sys.stderr, flush=True)

    print("Running exiftool over the drive (one pass, this is the slow part)...", file=sys.stderr, flush=True)
    records = run_exiftool(args.root, exclude_patterns)
    print(f"exiftool found {len(records)} files. Hashing (blake2b)...", file=sys.stderr, flush=True)

    conn = init_db(str(db_path))
    count = 0
    for record in records:
        row = build_row(record)
        try:
            row["content_hash"] = compute_hash(row["path"])
        except Exception as e:
            row["error"] = str(e)
        insert_row(conn, row)
        count += 1
        if count % 50 == 0:
            conn.commit()
            print(f"Hashed {count}/{len(records)} files...", file=sys.stderr, flush=True)
    conn.commit()
    print(f"Done. Processed {count} files into {db_path}", flush=True)

    export_csv(conn, str(csv_path))
    print(f"Exported to {csv_path}", flush=True)

    conn.close()


if __name__ == "__main__":
    main()
