# Setup

No Python packages to install — all three scripts use only the standard library
(blake2b hashing is built into Python's `hashlib`).

The only dependency is the **exiftool** command-line tool:

- **Mac:** `brew install exiftool`
- **Linux:** `sudo apt install libimage-exiftool-perl`
- **Windows:** download from https://exiftool.org/, then rename
  `exiftool(-k).exe` to `exiftool.exe` and put it somewhere on your PATH.

## Scope

`scan_files.py` scans **all files**, not just photos/videos. It automatically
skips hidden files and directories (anything starting with `.` — this covers
`.git`, `.Trash`, `.DS_Store`, AppleDouble `._filename` sidecars, etc. in one
rule) plus a few common junk directories (`node_modules`, `__pycache__`, OS
recycle-bin folders). Add more with `--exclude PATTERN` (repeatable, wildcards
allowed).

## Run

Each script takes `--output-dir` (default: current directory, created if it doesn't exist)
and writes fixed filenames into it, so you always know where to look.

```bash
# 1. Scan the drive -> writes files.db, files.csv
python3 scan_files.py "/Volumes/Backup Drive" --output-dir ~/backup-project

# 2. Find duplicates -> writes duplicates_report.csv, directory_pairs.csv
python3 find_duplicates.py ~/backup-project/files.db --all \
    --root "/Volumes/Backup Drive" --output-dir ~/backup-project

# 3. Compare directory contents -> writes directory_comparison.csv
python3 compare_directories.py ~/backup-project/files.db \
    --root "/Volumes/Backup Drive" --output-dir ~/backup-project
```
