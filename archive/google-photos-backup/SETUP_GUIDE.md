# Google Exit - Setup Guide

Step-by-step guide to set up the Google Exit script.

## Prerequisites Checklist

- [ ] Python 3.11 or higher installed
- [ ] Google account with data to export
- [ ] Google account (for Cloud Console)

## Step 1: Install Dependencies

```bash
# Navigate to the script directory
cd google-photos-backup

# Install required packages
pip install -r requirements.txt
```

Or install manually:
```bash
pip install google-auth-oauthlib google-api-python-client requests
```

## Step 2: Create Google Cloud Project

### 2.1 Go to Google Cloud Console

1. Visit: https://console.cloud.google.com/
2. Sign in with your Google account

### 2.2 Create New Project

1. Click the project dropdown (top bar)
2. Click "New Project"
3. Enter project name: `Data Export` (or any name)
4. Click "Create"
5. Wait for project creation (few seconds)

### 2.3 Select Your Project

- Make sure your new project is selected in the project dropdown

## Step 3: Enable Data Portability API

### 3.1 Navigate to API Library

1. In the left menu, go to "APIs & Services" > "Library"
2. Or visit: https://console.cloud.google.com/apis/library

### 3.2 Enable the API

1. Search for: `Data Portability API`
2. Click on "Data Portability API"
3. Click "Enable"
4. Wait for activation (few seconds)

**Alternative direct link**: https://console.cloud.google.com/apis/library/dataportability.googleapis.com

## Step 4: Configure OAuth Consent Screen

### 4.1 Navigate to OAuth Consent Screen

1. Go to "APIs & Services" > "OAuth consent screen"
2. Or visit: https://console.cloud.google.com/apis/credentials/consent

### 4.2 Configure Basic Settings

1. **User Type**: Select "External" (unless you have Google Workspace)
   - Click "Create"

2. **App Information**:
   - App name: `Data Export` (or any name)
   - User support email: Your email
   - App logo: (optional, skip)
   - App domain: (optional, skip)
   - Developer contact: Your email
   - Click "Save and Continue"

3. **Scopes**:
   - Click "Add or Remove Scopes"
   - Search for: `dataportability`
   - Select: `.../auth/dataportability`
   - Click "Update"
   - Click "Save and Continue"

4. **Test Users** (if External):
   - Click "Add Users"
   - Add your Google account email
   - Click "Add"
   - Click "Save and Continue"

5. **Summary**:
   - Review settings
   - Click "Back to Dashboard"

## Step 5: Create OAuth Credentials

### 5.1 Navigate to Credentials

1. Go to "APIs & Services" > "Credentials"
2. Or visit: https://console.cloud.google.com/apis/credentials

### 5.2 Create OAuth Client ID

1. Click "Create Credentials" > "OAuth client ID"
2. If prompted about consent screen, you should have already configured it
3. **Application type**: Select "Desktop app"
4. **Name**: `Data Export Client` (or any name)
5. Click "Create"

### 5.3 Download Credentials

1. A popup will show your Client ID and Client Secret
2. **IMPORTANT**: Click "Download JSON"
3. Save the file as `client_secrets.json` in the script directory
4. Click "OK"

**File location**: Should be in the same directory as `google-exit.py`

```
google-photos-backup/
├── google-exit.py
├── client_secrets.json  ← Should be here
└── ...
```

## Step 6: Verify Setup

### 6.1 Check Files

Make sure you have:
- ✅ `client_secrets.json` in the script directory
- ✅ Python dependencies installed
- ✅ Data Portability API enabled

### 6.2 Test Run

```bash
# Run the script (it will authenticate on first run)
python google-exit.py --output ~/test_export
```

On first run:
1. Browser will open for authentication
2. Sign in with your Google account
3. Grant permissions
4. Script will create `token.json` for future runs

## Troubleshooting

### "API not enabled" Error

**Solution**:
1. Go to: https://console.cloud.google.com/apis/library/dataportability.googleapis.com
2. Make sure API is enabled
3. Wait a few minutes for activation

### "Insufficient permissions" Error

**Solution**:
1. Check OAuth consent screen is configured
2. Make sure you added yourself as a test user (if External)
3. Try deleting `token.json` and re-authenticating

### "client_secrets.json not found" Error

**Solution**:
1. Make sure file is in the same directory as `google-exit.py`
2. Check filename is exactly `client_secrets.json`
3. Verify file downloaded correctly

### Authentication Issues

**Solution**:
1. Delete `token.json` if it exists
2. Re-run the script
3. Make sure browser allows popups/redirects

## Quick Setup Checklist

- [ ] Python 3.11+ installed
- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] Google Cloud Project created
- [ ] Data Portability API enabled
- [ ] OAuth consent screen configured
- [ ] OAuth credentials created
- [ ] `client_secrets.json` downloaded to script directory
- [ ] Test run successful

## Next Steps

Once setup is complete:

1. **Run full export**:
   ```bash
   python google-exit.py --output ~/Google_Data_Export
   ```

2. **Wait for export** (can take hours for large accounts)

3. **Review exported data** in the output directory

4. **Delete account** (follow instructions provided by script)

## Need Help?

- Check `EXIT_README.md` for detailed usage
- Review error messages in logs
- Verify all setup steps completed
- Check Google Cloud Console for API status

---

**Setup complete?** Run the script and start your Google exit! 🚀

