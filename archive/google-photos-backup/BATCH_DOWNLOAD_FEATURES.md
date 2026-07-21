# Batch Download Features

## Overview

The script is **fully designed to handle batches of data files** seamlessly. Google will split large exports into multiple ZIP files (typically 2GB each), and the script handles all of them automatically.

## Key Features for Batch Downloads

### ✅ Automatic Detection
- Detects all files in the export automatically
- No need to specify file count or names
- Handles any number of files (1, 10, 100+)

### ✅ Parallel Downloads
- Downloads multiple files simultaneously (default: 3 at a time)
- Configurable: `--max-parallel-downloads N`
- Faster than sequential downloads
- Respects network bandwidth

### ✅ Resume Capability
- **Automatically resumes** interrupted downloads
- If download fails or is interrupted, just re-run the script
- Detects partial files and continues from where it left off
- No need to re-download completed files

### ✅ Progress Tracking
- Progress bars for each file (if `tqdm` installed)
- Shows download speed and ETA
- Logs progress to file
- Overall progress: "Downloading file 3/15..."

### ✅ Smart File Detection
- Checks if files already exist
- Skips already-downloaded files
- Verifies file sizes match (if available)
- Prevents duplicate downloads

### ✅ Error Handling
- Continues downloading other files if one fails
- Logs all errors for review
- Retries failed downloads on script re-run
- Clear error messages

### ✅ Disk Space Checking
- Estimates total size needed
- Warns if disk space may be insufficient
- Shows available vs. required space

## How It Works

### 1. Export Initiation
```python
# Script initiates export for all services
# Google processes and splits into multiple ZIP files
```

### 2. Status Monitoring
```python
# Script checks export status every 5 minutes
# Waits until all files are ready
```

### 3. File Discovery
```python
# Gets list of all files from Google
# Example: ['export_1.zip', 'export_2.zip', ..., 'export_15.zip']
```

### 4. Batch Download
```python
# Downloads all files in parallel (default: 3 at a time)
# Each file has its own progress bar
# Automatically resumes if interrupted
```

### 5. Verification
```python
# Checks file sizes
# Verifies downloads completed
# Logs any issues
```

## Usage Examples

### Default (3 parallel downloads)
```bash
python google-exit.py --output ~/Google_Export
# Downloads up to 3 files simultaneously
```

### Sequential (one at a time)
```bash
python google-exit.py --output ~/Google_Export --max-parallel-downloads 1
# Downloads one file at a time (slower but more reliable on slow connections)
```

### More Parallel (faster, but uses more bandwidth)
```bash
python google-exit.py --output ~/Google_Export --max-parallel-downloads 5
# Downloads up to 5 files simultaneously (faster but uses more bandwidth)
```

### Resume Interrupted Download
```bash
# If download was interrupted, just run again:
python google-exit.py --output ~/Google_Export
# Script will detect partial files and resume automatically
```

## Example Output

```
[2025-01-15 10:30:00] [INFO] Found 15 file(s) to download
[2025-01-15 10:30:00] [INFO] Total size: 28.5 GB
[2025-01-15 10:30:00] [INFO] Downloading 15 files in parallel (max 3 at a time)...
[2025-01-15 10:30:01] [INFO] Downloading export_1.zip (1/15)...
  export_1.zip: 100%|████████████| 2.0G/2.0G [05:23<00:00, 6.2MB/s]
[2025-01-15 10:30:01] [INFO] Downloading export_2.zip (2/15)...
  export_2.zip: 100%|████████████| 2.0G/2.0G [05:18<00:00, 6.4MB/s]
[2025-01-15 10:30:01] [INFO] Downloading export_3.zip (3/15)...
  export_3.zip: 100%|████████████| 2.0G/2.0G [05:25<00:00, 6.1MB/s]
[2025-01-15 10:35:30] [INFO] Progress: 3/15 files downloaded
[2025-01-15 10:35:30] [INFO] Downloading export_4.zip (4/15)...
...
[2025-01-15 11:45:00] [INFO] ✓ Successfully downloaded all 15 file(s)
```

## Handling Large Batches

### For Very Large Exports (50+ files)

1. **Use sequential downloads** to avoid overwhelming your connection:
   ```bash
   python google-exit.py --max-parallel-downloads 1
   ```

2. **Monitor disk space** - the script will warn you if space is low

3. **Let it run overnight** - large exports can take many hours

4. **Resume capability** - if interrupted, just re-run the script

## Resume Example

If download is interrupted:

```
# First run (interrupted after 5 files)
[INFO] Downloading export_6.zip (6/15)...
[ERROR] Network error: Connection reset

# Second run (resumes automatically)
[INFO] Found 15 file(s) to download
[INFO] Already downloaded: export_1.zip
[INFO] Already downloaded: export_2.zip
[INFO] Already downloaded: export_3.zip
[INFO] Already downloaded: export_4.zip
[INFO] Already downloaded: export_5.zip
[INFO] Resuming download: export_6.zip (from 1048576 bytes)
[INFO] Downloading export_6.zip (6/15)...
  export_6.zip: 100%|████████████| 2.0G/2.0G [05:20<00:00, 6.3MB/s]
[INFO] Downloading export_7.zip (7/15)...
...
```

## Technical Details

### File Detection
- Uses Google Data Portability API to get file list
- Each file has: name, size, download URL
- Files are typically named: `export_1.zip`, `export_2.zip`, etc.

### Resume Implementation
- Uses HTTP Range requests (`Range: bytes=start-end`)
- Checks existing file size
- Continues from last byte
- Falls back to restart if server doesn't support resume

### Parallel Downloads
- Uses `ThreadPoolExecutor` for concurrent downloads
- Configurable worker count
- Each download is independent
- Failed downloads don't stop others

### Progress Tracking
- Uses `tqdm` for progress bars (if available)
- Falls back to simple logging if `tqdm` not installed
- Logs progress to file for review

## Best Practices

1. **Start with default settings** (3 parallel downloads)
2. **Monitor first few files** to ensure it's working
3. **Let it run** - don't interrupt unless necessary
4. **Check logs** if something goes wrong
5. **Re-run if interrupted** - resume will handle it

## Troubleshooting

### Downloads are slow
- Try reducing parallel downloads: `--max-parallel-downloads 1`
- Check your internet connection
- Large files take time - be patient

### Downloads keep failing
- Check network stability
- Try sequential downloads: `--max-parallel-downloads 1`
- Check disk space
- Review error logs

### Files not resuming
- Some servers don't support HTTP Range requests
- Script will automatically restart download
- This is normal behavior

## Summary

✅ **The script handles batches seamlessly** - you don't need to do anything special. Just run it and it will:

1. Detect all files automatically
2. Download them in parallel (faster)
3. Resume if interrupted
4. Show progress for each file
5. Handle errors gracefully
6. Verify downloads completed

**You can safely run it and let it handle everything!** 🚀

