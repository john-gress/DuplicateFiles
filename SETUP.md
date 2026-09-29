# Setup

No Python packages to install — all three scripts use only the standard library.

The only dependency is the **exiftool** command-line tool:

- **Mac:** `brew install exiftool`
- **Linux:** `sudo apt install libimage-exiftool-perl`
- **Windows:** download from https://exiftool.org/, then rename
  `exiftool(-k).exe` to `exiftool.exe` and put it somewhere on your PATH.

## Run

Each script takes `--output-dir` (default: current directory, created if it doesn't exist)
and writes fixed filenames into it, so you always know where to look.

```bash
# 1. Scan the drive -> writes media.db, media.csv
python3 scan_photos.py "/Volumes/Backup Drive" --output-dir ~/photo-project

# 2. Find duplicates -> writes duplicates_report.csv, directory_pairs.csv
python3 find_duplicates.py ~/photo-project/media.db --all \
    --root "/Volumes/Backup Drive" --output-dir ~/photo-project

# 3. Compare directory contents -> writes directory_comparison.csv
python3 compare_directories.py ~/photo-project/media.db \
    --root "/Volumes/Backup Drive" --output-dir ~/photo-project
```
