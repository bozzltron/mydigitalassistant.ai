# Google Exit - Complete Data Export Solution

## 🎯 Overview

A comprehensive, automated solution to export **all your Google data** and prepare for account deletion. This script uses the official Google Data Portability API to export everything from your Google account.

## ✨ Features

- ✅ **Exports ALL Google services** (Gmail, Drive, Photos, Calendar, Contacts, YouTube, Maps, Activity, and 20+ more)
- ✅ **Fully automated** using official Google APIs
- ✅ **Organized output** - structures data for easy access
- ✅ **Progress tracking** - monitors export status automatically
- ✅ **Error handling** - robust error handling and logging
- ✅ **Account deletion guide** - provides instructions for account deletion
- ✅ **Production-ready** - well-tested and documented

## 📋 What Gets Exported

The script exports data from all major Google services:

- **Communication**: Gmail, Contacts, Calendar
- **Storage**: Drive, Photos
- **Media**: YouTube, YouTube Music
- **Location**: Maps, Location History
- **Activity**: Search History, My Activity
- **Health**: Fit
- **Productivity**: Keep, Tasks
- **Browser**: Chrome bookmarks, history
- **And 15+ more services...**

See `google-exit.py` for the complete list.

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Setup Google Cloud Project

Follow the detailed guide: **`SETUP_GUIDE.md`**

Quick steps:
1. Create Google Cloud Project
2. Enable Data Portability API
3. Configure OAuth consent screen
4. Create OAuth credentials
5. Download `client_secrets.json`

### 3. Run the Script

```bash
# Export all data
python google-exit.py

# Or specify output directory
python google-exit.py --output ~/Google_Data_Export
```

### 4. Wait for Export

- Small accounts: 1-4 hours
- Medium accounts: 4-12 hours
- Large accounts: 12-48 hours

The script monitors progress automatically.

### 5. Delete Account

After export completes, follow the instructions provided by the script to delete your Google account.

## 📁 Files in This Package

- **`google-exit.py`** - Main script (run this!)
- **`EXIT_README.md`** - Detailed usage documentation
- **`SETUP_GUIDE.md`** - Step-by-step setup instructions
- **`EXISTING_SOLUTIONS.md`** - Comparison with other tools
- **`requirements.txt`** - Python dependencies

## 📖 Documentation

- **Quick Start**: This file
- **Detailed Usage**: `EXIT_README.md`
- **Setup Instructions**: `SETUP_GUIDE.md`
- **Existing Solutions**: `EXISTING_SOLUTIONS.md`

## 🔧 Usage Examples

```bash
# Export all data to default location
python google-exit.py

# Export to specific directory
python google-exit.py --output /Volumes/ExternalDrive/Google_Export

# Export specific services only
python google-exit.py --services GOOGLE_PHOTOS GMAIL DRIVE

# Increase wait time for large exports
python google-exit.py --max-wait-hours 72
```

## ⚠️ Important Notes

1. **Account deletion is PERMANENT** - Make sure you have backups!
2. **Large exports take time** - Be patient, the script monitors progress
3. **Setup required** - Need Google Cloud Project (takes ~10 minutes)
4. **API access** - Requires Data Portability API (free, but needs setup)

## 🔒 Security & Privacy

- ✅ All processing is local
- ✅ OAuth tokens stored securely
- ✅ No data sent to third parties
- ✅ Read-only access (cannot modify your data)
- ✅ You control all credentials

## 🆚 Comparison with Alternatives

| Feature | Our Script | Google Takeout | Service-Specific Tools |
|---------|-----------|----------------|------------------------|
| Automation | ✅ Full | ❌ Manual | ⚠️ Partial |
| All Services | ✅ Yes | ✅ Yes | ❌ One service each |
| Organization | ✅ Yes | ❌ No | ⚠️ Varies |
| Account Deletion | ✅ Instructions | ❌ No | ❌ No |

See `EXISTING_SOLUTIONS.md` for detailed comparison.

## 🐛 Troubleshooting

Common issues and solutions:

### API Not Enabled
- Enable Data Portability API in Google Cloud Console
- Wait a few minutes for activation

### Authentication Issues
- Delete `token.json` and re-authenticate
- Check OAuth consent screen configuration
- Verify `client_secrets.json` is in correct location

### Export Taking Too Long
- Large accounts can take 24-48 hours
- Script monitors automatically
- Increase `--max-wait-hours` if needed

See `SETUP_GUIDE.md` for more troubleshooting tips.

## 📚 Additional Resources

- [Google Data Portability API Docs](https://developers.google.com/data-portability)
- [Google Account Deletion Guide](https://support.google.com/accounts/answer/32046)
- [Google Takeout (Manual Alternative)](https://takeout.google.com/)

## 🤝 Contributing

Found a bug or want to improve this? Contributions welcome!

## ⚖️ License

MIT License - Use at your own risk. This script is provided as-is.

## 🙏 Credits

Built using:
- Google Data Portability API (official Google API)
- Python Google API Client libraries
- Inspired by various open-source Google export tools

---

## 🎯 Next Steps

1. **Read** `SETUP_GUIDE.md` for setup instructions
2. **Follow** the setup steps (takes ~10 minutes)
3. **Run** `python google-exit.py`
4. **Wait** for export to complete
5. **Review** exported data
6. **Delete** your Google account (if desired)

**Ready to export your data?** Start with `SETUP_GUIDE.md`! 🚀

---

**Questions?** Check the documentation files or review the code comments in `google-exit.py`.

Good luck with your Google exit! 🎉

