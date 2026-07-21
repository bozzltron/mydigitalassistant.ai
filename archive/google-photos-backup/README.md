# Google Photos Backup

Automated script to organize and backup Google Photos from Takeout exports.

## Overview

Due to Google Photos API restrictions (as of March 2025), this tool works with **Google Takeout** exports to organize your photos into a clean, date-based structure.

## Quick Start

### 1. Export from Google Takeout

1. Go to [Google Takeout](https://takeout.google.com/)
2. Select only "Google Photos"
3. Choose ZIP format, 2GB file size
4. Create export (you'll receive email when ready)
5. Download all ZIP files

### 2. Extract ZIP Files

Extract all Takeout ZIP files into a single directory:

```bash
mkdir ~/Downloads/Google_Takeout
# Extract all ZIP files here
```

### 3. Run Organizer

```bash
# Install dependencies
pip install -r requirements.txt

# Organize photos
python src/organizer.py \
  --input ~/Downloads/Google_Takeout \
  --output ~/Pictures/Google_Photos_Backup
```

## Features

- ✅ **Date-based organization**: Photos organized by year/month
- ✅ **Metadata preservation**: Merges JSON metadata into EXIF
- ✅ **Duplicate detection**: Skips duplicate photos
- ✅ **Progress tracking**: Shows progress with progress bars
- ✅ **Error handling**: Continues processing even if some files fail
- ✅ **Video support**: Handles both photos and videos

## Output Structure

```
backup/
└── photos/
    ├── 2024/
    │   ├── 01/
    │   │   ├── 2024-01-15_14-30-25_IMG_1234.jpg
    │   │   └── 2024-01-20_10-15-00_VID_5678.mp4
    │   └── 02/
    └── 2025/
```

## Options

```bash
python src/organizer.py \
  --input <input_dir> \
  --output <output_dir> \
  [--no-date-organization] \  # Flat structure instead
  [--no-metadata] \            # Skip metadata merging
  [--keep-duplicates]          # Keep duplicate files
```

## Requirements

- Python 3.9+
- See `requirements.txt` for dependencies

## Future Enhancements

- [ ] Automated Takeout monitoring
- [ ] Incremental backup support
- [ ] Cloud storage integration
- [ ] Web interface
- [ ] Scheduled backups

## See Also

- [Full Proposal](../google-photos-backup-proposal.md) - Detailed implementation plan
- [Google Photos Takeout Helper](https://github.com/TheLastGimbus/GooglePhotosTakeoutHelper) - Alternative tool
