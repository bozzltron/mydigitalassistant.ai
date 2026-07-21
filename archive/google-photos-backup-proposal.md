# Google Photos Backup Proposal

## Executive Summary

Due to Google's API restrictions (effective March 2025), the Google Photos Library API can only access photos uploaded by your application, not your entire library. This proposal outlines three approaches to download all your Google Photos, with **Option 2 (Automated Takeout Script)** being the recommended solution.

---

## Current State (2025)

**⚠️ Important**: As of March 31, 2025, the Google Photos Library API has been restricted:
- ❌ Can only access photos uploaded by your app
- ❌ Cannot access existing photos in your library
- ✅ Can still upload new photos via API

**Result**: Direct API-based backup is no longer feasible for existing photos.

---

## Option 1: Google Takeout (Manual) + Helper Script

### Overview
Use Google Takeout to export all photos, then use a script to organize and clean up the downloaded files.

### Pros
- ✅ Works for all photos (no API restrictions)
- ✅ Includes metadata (dates, locations, descriptions)
- ✅ Official Google service
- ✅ No rate limits

### Cons
- ❌ Manual process (must initiate Takeout)
- ❌ Large exports can take hours/days
- ❌ Files come in multiple ZIP files
- ❌ Disorganized folder structure
- ❌ Includes JSON metadata files

### Implementation

#### Step 1: Manual Takeout
1. Go to [Google Takeout](https://takeout.google.com/)
2. Select only "Google Photos"
3. Choose export format (ZIP recommended)
4. Set file size limit (2GB per file)
5. Create export (takes time, you'll get email when ready)
6. Download all ZIP files

#### Step 2: Organization Script
Create a Python script to:
- Extract all ZIP files
- Organize photos by date (YYYY/MM/DD structure)
- Merge metadata from JSON files into EXIF
- Remove duplicate JSON files
- Preserve original filenames or rename with dates
- Handle videos separately

### Script Structure
```python
# google-photos-organizer.py
# - Extracts Takeout ZIPs
# - Organizes by date
# - Merges metadata
# - Removes duplicates
# - Creates organized folder structure
```

---

## Option 2: Automated Takeout Script (Recommended) ⭐

### Overview
Create a script that automates the Takeout process using Google Takeout API (if available) or guides you through the process, then automatically organizes the results.

### Pros
- ✅ Automated organization
- ✅ Can be scheduled/run periodically
- ✅ Handles large libraries efficiently
- ✅ Preserves metadata
- ✅ Can resume interrupted downloads

### Cons
- ⚠️ May require manual Takeout initiation (depends on API availability)
- ⚠️ Large libraries take time

### Implementation Approach

#### Phase 1: Takeout Automation
- Check if Google Takeout API is available
- If yes: Automate export creation
- If no: Provide clear instructions + wait for user to download

#### Phase 2: Download & Organize
```python
# google-photos-backup.py
# Features:
# - Monitors for Takeout ZIP files in download folder
# - Automatically extracts and organizes
# - Handles incremental backups (only new photos)
# - Progress tracking and resume capability
# - Metadata preservation
# - Duplicate detection
```

#### Phase 3: Organization Structure
```
backup/
├── photos/
│   ├── 2024/
│   │   ├── 01/
│   │   │   ├── 2024-01-15_IMG_1234.jpg
│   │   │   └── 2024-01-20_VID_5678.mp4
│   │   └── 02/
│   └── 2025/
├── metadata/
│   └── (preserved JSON files for reference)
└── logs/
    └── backup_2025-01-15.log
```

### Key Features
- **Incremental Backup**: Only processes new photos since last backup
- **Resume Support**: Can pause and resume large exports
- **Metadata Preservation**: Merges JSON metadata into EXIF when possible
- **Duplicate Detection**: Skips already-backed-up photos
- **Progress Tracking**: Shows download/organization progress
- **Error Handling**: Handles corrupted files, network issues

---

## Option 3: Third-Party Tools (Alternative)

### gphotos-sync
- **Language**: Python
- **Method**: Uses Google Photos API (limited by restrictions)
- **Status**: May not work for existing photos after March 2025
- **Link**: https://github.com/gilesknap/gphotos-sync

### Google Photos Takeout Helper
- **Language**: Python
- **Method**: Organizes Takeout exports
- **Status**: ✅ Still works (uses Takeout, not API)
- **Link**: https://github.com/TheLastGimbus/GooglePhotosTakeoutHelper

### Recommendation
Use **Google Photos Takeout Helper** as a reference or integrate its logic into our custom script.

---

## Recommended Implementation Plan

### Phase 1: Takeout Organizer Script (Week 1)
Create a script that:
1. Accepts Takeout ZIP file location
2. Extracts and organizes photos by date
3. Preserves metadata
4. Removes duplicates
5. Creates organized folder structure

**Deliverable**: `organize-takeout.py`

### Phase 2: Automation Layer (Week 2)
Add automation features:
1. Monitor download folder for new Takeout files
2. Auto-extract and organize
3. Incremental backup support
4. Progress tracking
5. Resume capability

**Deliverable**: `google-photos-backup.py`

### Phase 3: Takeout Integration (Week 3 - Optional)
If Google Takeout API is available:
1. Automate Takeout export creation
2. Monitor export status
3. Auto-download when ready
4. Full end-to-end automation

**Deliverable**: Enhanced `google-photos-backup.py`

---

## Technical Specifications

### Technology Stack
- **Language**: Python 3.9+
- **Key Libraries**:
  - `zipfile` - Extract Takeout ZIPs
  - `json` - Parse metadata files
  - `exifread` / `Pillow` - Read/write EXIF data
  - `pathlib` - File system operations
  - `tqdm` - Progress bars
  - `hashlib` - Duplicate detection

### File Structure
```
google-photos-backup/
├── src/
│   ├── __init__.py
│   ├── organizer.py          # Main organization logic
│   ├── metadata_merger.py    # Merge JSON into EXIF
│   ├── duplicate_detector.py # Find duplicates
│   └── utils.py              # Helper functions
├── config/
│   └── config.yaml           # Configuration
├── tests/
│   └── test_organizer.py
├── requirements.txt
├── README.md
└── main.py                   # Entry point
```

### Configuration
```yaml
# config.yaml
backup:
  source_dir: ~/Downloads/Google_Takeout
  output_dir: ~/Pictures/Google_Photos_Backup
  organize_by: date  # date, album, or both
  preserve_metadata: true
  remove_duplicates: true
  date_format: YYYY/MM/DD
  
metadata:
  merge_to_exif: true
  preserve_json: false  # Keep JSON files for reference
  
processing:
  max_workers: 4  # Parallel processing
  chunk_size: 100  # Process N files at a time
```

---

## Usage Examples

### Basic Organization
```bash
python organize-takeout.py \
  --input ~/Downloads/Google_Takeout \
  --output ~/Pictures/Google_Photos_Backup
```

### Automated Backup
```bash
python google-photos-backup.py \
  --config config.yaml \
  --watch ~/Downloads  # Monitor for new Takeout files
```

### Incremental Backup
```bash
python google-photos-backup.py \
  --input ~/Downloads/Google_Takeout \
  --output ~/Pictures/Google_Photos_Backup \
  --incremental  # Only process new photos
```

---

## Error Handling & Edge Cases

### Scenarios to Handle
1. **Corrupted ZIP files**: Skip and log
2. **Missing metadata**: Use file timestamps as fallback
3. **Duplicate photos**: Detect by hash, keep one copy
4. **Large files**: Process in chunks, show progress
5. **Network interruptions**: Resume capability
6. **Disk space**: Check before processing
7. **Permission errors**: Clear error messages
8. **Unsupported formats**: Log and skip

---

## Security & Privacy Considerations

- ✅ All processing local (no cloud upload)
- ✅ No API keys required (uses Takeout)
- ✅ Original files preserved
- ✅ Metadata handling is read-only (doesn't modify originals)
- ⚠️ Handle sensitive photos appropriately
- ⚠️ Secure storage of backup location

---

## Future Enhancements

1. **Cloud Storage Integration**: Auto-upload to Dropbox, OneDrive, etc.
2. **Encryption**: Optional encryption of backup
3. **Compression**: Optional compression of organized backup
4. **Web Interface**: GUI for easier management
5. **Scheduled Backups**: Cron job integration
6. **Multi-account Support**: Handle multiple Google accounts
7. **Album Preservation**: Maintain album structure if desired

---

## Timeline Estimate

- **Phase 1** (Organizer Script): 2-3 days
- **Phase 2** (Automation): 2-3 days
- **Phase 3** (Takeout API - if available): 3-5 days
- **Testing & Polish**: 2-3 days

**Total**: ~2 weeks for full implementation

---

## Next Steps

1. **Decision**: Choose which option to implement
2. **Setup**: Create project structure
3. **Research**: Verify Google Takeout API availability
4. **Prototype**: Build basic organizer script
5. **Test**: Run on sample Takeout export
6. **Iterate**: Add features based on needs

---

## Questions to Consider

1. **Frequency**: How often do you want to backup? (One-time, monthly, etc.)
2. **Organization**: Prefer date-based or album-based structure?
3. **Metadata**: Keep JSON files or merge into EXIF only?
4. **Storage**: Local drive only, or also cloud backup?
5. **Automation**: Fully automated or manual trigger?

---

**Recommendation**: Start with **Option 2 (Automated Takeout Script)** - it provides the best balance of automation and reliability given current API restrictions.
