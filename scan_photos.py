#!/usr/bin/env python3
"""
scan_photos.py - Scan a drive for photo and video files, extract metadata
via exiftool, compute a SHA-256 hash of each file, and store everything
in a SQLite database (plus optional CSV export) for duplicate detection.

Requires the exiftool command-line tool to be installed and on PATH:
    https://exiftool.org/
    Mac:     brew install exiftool
    Linux:   apt install libimage-exiftool-perl
    Windows: download from exiftool.org and rename exiftool(-k).exe to exiftool.exe

Usage:
    python scan_photos.py /path/to/drive --output-dir ~/photo-project
    (writes media.db and media.csv into that directory; created if needed)
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

MEDIA_EXTENSIONS = [
    "jpg", "jpeg", "png", "heic", "tif", "tiff", "bmp", "gif",
    "mp4", "mov", "avi", "m4v", "3gp", "mkv",
]


def check_exiftool():
    if shutil.which("exiftool") is None:
        sys.exit(
            "exiftool not found on PATH. Install it from https://exiftool.org/ "
            "(brew install exiftool / apt install libimage-exiftool-perl / "
            "download the Windows build and rename to exiftool.exe) and try again."
        )


def sha256_of_file(path, block_size=65536):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(block_size), b""):
            h.update(chunk)
    return h.hexdigest()


def run_exiftool(root):
    """
    Run exiftool once, recursively, over the whole tree and get JSON metadata
    for every matching file. Much faster than invoking it per-file.
    -n gives GPS coordinates as plain signed decimal degrees.
    """
    cmd = ["exiftool", "-r", "-j", "-n"]
    for ext in MEDIA_EXTENSIONS:
        cmd += ["-ext", ext]
    cmd += [str(root)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode not in (0, 1):  # 1 = exiftool completed with minor per-file warnings
        print(result.stderr, file=sys.stderr)
        sys.exit(f"exiftool failed with exit code {result.returncode}")
    if not result.stdout.strip():
        return []
    return json.loads(result.stdout)


def is_apple_double(filename):
    """
    macOS writes a hidden sidecar file (AppleDouble format) alongside files
    copied to non-Mac filesystems, to preserve extended attributes/resource
    forks. Named "._originalname" - not real media, just metadata (usually
    padded out to a 4K filesystem block). Filter these out.
    """
    return filename.startswith("._")


def build_row(record):
    path = Path(record["SourceFile"])
    return {
        "path": str(path),
        "filename": path.name,
        "size_bytes": path.stat().st_size if path.exists() else None,
        "sha256": None,  # filled in separately
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
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE,
            filename TEXT,
            size_bytes INTEGER,
            sha256 TEXT,
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sha256 ON media(sha256)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date_taken ON media(date_taken)")
    conn.commit()
    return conn


def insert_row(conn, row):
    conn.execute("""
        INSERT OR REPLACE INTO media
        (path, filename, size_bytes, sha256, width, height, duration_seconds,
         date_taken, camera_make, camera_model, gps_lat, gps_lon,
         mime_type, file_type, error)
        VALUES (:path, :filename, :size_bytes, :sha256, :width, :height, :duration_seconds,
                :date_taken, :camera_make, :camera_model, :gps_lat, :gps_lon,
                :mime_type, :file_type, :error)
    """, row)


def export_csv(conn, csv_path):
    cur = conn.execute("SELECT * FROM media")
    cols = [d[0] for d in cur.description]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        writer.writerows(cur.fetchall())


def main():
    ap = argparse.ArgumentParser(description="Scan a drive for photos/videos and build a metadata database.")
    ap.add_argument("root", help="Root directory to scan")
    ap.add_argument("--output-dir", default=".",
                     help="Directory to write media.db and media.csv into (created if it doesn't exist)")
    args = ap.parse_args()

    check_exiftool()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / "media.db"
    csv_path = output_dir / "media.csv"

    print("Running exiftool over the drive (one pass, this is the slow part)...", file=sys.stderr, flush=True)
    all_records = run_exiftool(args.root)
    records = [r for r in all_records if not is_apple_double(Path(r["SourceFile"]).name)]
    skipped = len(all_records) - len(records)
    if skipped:
        print(f"Skipped {skipped} AppleDouble sidecar files (._filename)", file=sys.stderr, flush=True)
    print(f"exiftool found {len(records)} media files. Hashing...", file=sys.stderr, flush=True)

    conn = init_db(str(db_path))
    count = 0
    for record in records:
        row = build_row(record)
        try:
            row["sha256"] = sha256_of_file(row["path"])
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
