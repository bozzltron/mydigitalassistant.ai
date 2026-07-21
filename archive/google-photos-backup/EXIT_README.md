# Google Exit - Complete Data Export & Account Deletion

A comprehensive script to export **all your Google data** and prepare for account deletion.

## 🎯 What This Does

1. **Exports ALL Google data** using Google Data Portability API:
   - Gmail, Drive, Photos, Calendar, Contacts
   - YouTube, Maps, Activity, Assistant, Fit, Keep, Tasks
   - And 20+ other Google services
   
2. **Organizes the data** into a clean structure

3. **Provides instructions** for account deletion

## ⚡ Quick Start

### Prerequisites

- Python 3.11 or higher
- Google account with data to export
- Google Cloud Project (free, setup takes ~10 minutes)

### Installation

```bash
# Clone or download this repository
cd google-photos-backup

# Install dependencies
pip install -r requirements.txt

# Or install manually:
pip install google-auth-oauthlib google-api-python-client requests
```

### Setup Google Cloud Project

1. **Create Project**
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Click "Create Project"
   - Name it (e.g., "Data Export")
   - Click "Create"

2. **Enable Data Portability API**
   - In your project, go to "APIs & Services" > "Library"
   - Search for "Data Portability API"
   - Click "Enable"

3. **Create OAuth Credentials**
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "OAuth client ID"
   - If prompted, configure OAuth consent screen:
     - User Type: "External" (unless you have Google Workspace)
     - App name: "Data Export" (or any name)
     - User support email: Your email
     - Developer contact: Your email
     - Click "Save and Continue" through scopes (add none)
     - Add yourself as a test user
   - Application type: "Desktop app"
   - Name: "Data Export Client"
   - Click "Create"
   - **Download the JSON file** and save as `client_secrets.json` in this directory

### Check Space Requirements First (Recommended)

```bash
# Check how much space you'll need before exporting
python google-exit.py --check-space-only --output ~/Google_Data_Export
```

This shows:
- Estimated total size
- Available disk space
- Service-by-service breakdown
- Whether you have enough space

### Run the Script

```bash
# Export all data to default location (~/Google_Data_Export)
python google-exit.py

# Or specify output directory
python google-exit.py --output /path/to/backup

# Export specific services only
python google-exit.py --services GOOGLE_PHOTOS GMAIL DRIVE
```

### First Run

1. Script will open browser for authentication
2. Sign in with your Google account
3. Grant permissions (read-only access to your data)
4. Script will initiate export
5. Wait for export to complete (can take hours for large accounts)
6. Script will download and organize everything

## 📁 Output Structure

```
Google_Data_Export/
├── raw_exports/          # Original ZIP files from Google
│   ├── export_1.zip
│   └── export_2.zip
├── organized/            # Extracted and organized data
│   ├── export_1/
│   └── export_2/
├── logs/                 # Export logs
│   └── export_YYYYMMDD_HHMMSS.log
├── export_summary.json   # Summary of what was exported
└── token.json           # Saved OAuth token (for reuse)
```

## 🔧 Options

```bash
python google-exit.py --help

Options:
  --output DIR              Output directory (default: ~/Google_Data_Export)
  --services SERVICE...     Specific services to export
  --max-wait-hours HRS      Max hours to wait (default: 48)
  --max-parallel-downloads  Parallel downloads (default: 3)
  --check-space-only        Check space requirements only (no export)
  --skip-space-check        Skip disk space check before export
  --skip-organization       Skip organizing extracted files
```

## 📋 Available Services

- `CHROME` - Chrome bookmarks, history, settings
- `GMAIL` - All emails
- `DRIVE` - All Drive files
- `GOOGLE_PHOTOS` - All photos and videos
- `CALENDAR` - Calendar events
- `CONTACTS` - Contact list
- `YOUTUBE` - YouTube videos, playlists, subscriptions
- `YOUTUBE_MUSIC` - Music library
- `MAPS` - Maps data, location history
- `MY_ACTIVITY` - Search history, activity
- `ASSISTANT` - Google Assistant interactions
- `FIT` - Fitness data
- `KEEP` - Google Keep notes
- `TASKS` - Google Tasks
- And many more...

See `google-exit.py` for complete list.

## ⏱️ Export Time

Export time depends on data size:

- **Small account** (<10GB): 1-4 hours
- **Medium account** (10-100GB): 4-12 hours
- **Large account** (100GB+): 12-48 hours

The script will monitor progress and notify when complete.

## 🗑️ Account Deletion

**After export is complete**, delete your account:

1. Go to [Google Account Settings](https://myaccount.google.com/)
2. Click "Data & Privacy"
3. Scroll to "More options"
4. Click "Delete your Google Account"
5. Follow instructions

⚠️ **WARNING**: Account deletion is **PERMANENT** and cannot be undone!

**Recovery period**: Up to 30 days (if you change your mind)

## 🔒 Security & Privacy

- ✅ All processing is local
- ✅ OAuth tokens stored securely
- ✅ No data sent to third parties
- ✅ Read-only access (cannot modify your data)
- ✅ You control all credentials

## 🐛 Troubleshooting

### "API not enabled" error
- Make sure Data Portability API is enabled in Google Cloud Console
- Check: https://console.cloud.google.com/apis/library/dataportability.googleapis.com

### "Insufficient permissions" error
- Make sure you're using the correct Google account
- Check OAuth consent screen is configured
- Add yourself as a test user if needed

### Export taking too long
- Large accounts can take 24-48 hours
- Script will wait up to 48 hours by default
- You can increase with `--max-wait-hours`

### Authentication issues
- Delete `token.json` and re-authenticate
- Make sure `client_secrets.json` is in the same directory
- Check OAuth consent screen configuration

## 📚 Additional Resources

- [Google Data Portability API Docs](https://developers.google.com/data-portability)
- [Google Account Deletion Guide](https://support.google.com/accounts/answer/32046)
- [Google Takeout (Manual Alternative)](https://takeout.google.com/)

## 🤝 Contributing

Found a bug or want to improve this? Contributions welcome!

## ⚖️ License

MIT License - Use at your own risk. This script is provided as-is.

## ⚠️ Disclaimer

- This script uses official Google APIs
- Account deletion is permanent - make sure you have backups
- Large exports may take significant time
- Test with a small export first if unsure

---

**Ready to export your data?** Follow the setup steps above and run:

```bash
python google-exit.py
```

Good luck with your Google exit! 🚀

