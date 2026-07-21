# Space Requirements & Estimation

## Overview

The script provides **space requirement estimates** before you start the export, so you know how much disk space you'll need.

## Quick Check

### Check Space Requirements Only (No Export)

```bash
# Check space requirements without exporting
python google-exit.py --check-space-only --output ~/Google_Export
```

This will show you:
- Estimated total size
- Available disk space
- Service-by-service breakdown
- Whether you have enough space

### Example Output

```
============================================================
Google Exit - Space Requirement Check
============================================================

Services to export: 25
Estimated total size: 45.2 GB
Available space: 120.5 GB

Service size breakdown:
  GOOGLE_PHOTOS            ~15.0 GB
  DRIVE                   ~10.0 GB
  GMAIL                    ~5.0 GB
  YOUTUBE                  ~8.0 GB
  MY_ACTIVITY              ~3.0 GB
  MAPS                     ~2.0 GB
  ...

Required (with 10% buffer): 49.7 GB

✓ You have sufficient disk space for this export!
```

## Automatic Space Check

The script **automatically checks space** before starting the export:

```bash
python google-exit.py --output ~/Google_Export
```

### What Happens

1. **Before Export**: Estimates space requirements
2. **Shows breakdown**: Service-by-service size estimates
3. **Checks available space**: Compares with your disk
4. **Warns if insufficient**: Prompts before continuing
5. **During Download**: Shows actual file sizes (more accurate)

### Example: Insufficient Space

```
============================================================
SPACE REQUIREMENT ESTIMATE
============================================================
Services to export: 25
Estimated size: 45.2 GB
Available space: 30.5 GB
============================================================
⚠️  WARNING: You may not have enough disk space!
Required (with 10% buffer): 49.7 GB
Available: 30.5 GB
Shortfall: 19.2 GB
============================================================

⚠️  WARNING: You may not have enough disk space!
The export may fail or be incomplete.

Consider:
  1. Free up disk space
  2. Choose a different output directory
  3. Export fewer services at once
  4. Use --skip-space-check to proceed anyway

Continue anyway? (yes/no):
```

## Space Estimates by Service

The script uses conservative estimates based on typical usage:

| Service | Estimated Size | Notes |
|---------|---------------|-------|
| **GOOGLE_PHOTOS** | ~15 GB | Highly variable (10-100GB+) |
| **DRIVE** | ~10 GB | Average user: 5-50GB |
| **GMAIL** | ~5 GB | Average user: 2-10GB |
| **YOUTUBE** | ~8 GB | Playlists, history, subscriptions |
| **MY_ACTIVITY** | ~3 GB | Search history, activity logs |
| **MAPS** | ~2 GB | Location history, saved places |
| **CALENDAR** | ~0.1 GB | Events, usually small |
| **CONTACTS** | ~0.01 GB | Contact list, very small |
| **CHROME** | ~1 GB | Bookmarks, history |
| **FIT** | ~0.5 GB | Fitness data |
| **KEEP** | ~0.1 GB | Notes |
| **TASKS** | ~0.01 GB | Task lists |

**Note**: These are rough estimates. Actual sizes vary significantly based on usage.

## Actual vs. Estimated

### Before Export (Estimated)
- Based on service type averages
- Conservative estimates
- May be higher or lower than actual

### During Download (Actual)
- Shows actual file sizes from Google
- More accurate than estimates
- Based on your actual data

## Tips for Large Exports

### 1. Check Space First
```bash
python google-exit.py --check-space-only
```

### 2. Export to External Drive
```bash
python google-exit.py --output /Volumes/ExternalDrive/Google_Export
```

### 3. Export Services Separately
```bash
# Export photos first
python google-exit.py --services GOOGLE_PHOTOS --output ~/Photos_Export

# Then export other services
python google-exit.py --services GMAIL DRIVE --output ~/Other_Export
```

### 4. Free Up Space
- Delete old files
- Empty trash
- Move files to cloud storage
- Use disk cleanup tools

## Skip Space Check

If you want to skip the space check (not recommended):

```bash
python google-exit.py --skip-space-check --output ~/Google_Export
```

**Warning**: This may cause the export to fail if you run out of space!

## Space During Download

The script also checks space **during download** when it knows the actual file sizes:

```
============================================================
ACTUAL EXPORT SIZE
============================================================
Number of files: 15
Total size: 42.8 GB
Available space: 120.5 GB
✓ Sufficient space available (120.5 GB > 47.1 GB required)
============================================================
```

## Understanding the Estimates

### Why Estimates May Vary

1. **Service usage**: Heavy users will have more data
2. **Account age**: Older accounts have more data
3. **Service type**: Photos/Drive are usually largest
4. **Compression**: ZIP files may be smaller than raw data

### Conservative Estimates

The script uses **conservative estimates** to avoid surprises:
- Better to overestimate than underestimate
- Includes 10% buffer for safety
- Warns if space is close

### Actual Sizes

Actual sizes are only known when:
- Export is complete
- Files are ready for download
- Google provides file sizes

## Troubleshooting

### "Insufficient Space" Warning

**Solutions**:
1. Free up disk space
2. Use external drive: `--output /Volumes/ExternalDrive/Export`
3. Export fewer services: `--services GOOGLE_PHOTOS GMAIL`
4. Export services separately

### Estimates Seem Too High

**Remember**:
- Estimates are conservative
- Actual size may be lower
- Better safe than sorry

### Estimates Seem Too Low

**Possible reasons**:
- You have more data than average
- Account is older/larger than typical
- Heavy usage of certain services

**Solution**: Export anyway - script will warn again during download with actual sizes.

## Summary

✅ **Check space before export**: `--check-space-only`
✅ **Automatic check**: Runs before export starts
✅ **Service breakdown**: See which services use most space
✅ **Actual sizes**: Shown during download
✅ **Warnings**: Alerts if space insufficient
✅ **10% buffer**: Extra space for safety

**Always check space requirements before starting a large export!** 🚀

