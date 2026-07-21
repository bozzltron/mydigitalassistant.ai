#!/usr/bin/env python3
"""
Google Exit Script - Export All Data and Prepare for Account Deletion

This script automates the export of all your Google data using the Google Data Portability API,
then organizes it for easy access. Account deletion must be done manually for security reasons.

Usage:
    python google-exit.py --output ~/Google_Data_Export

Requirements:
    1. Google Cloud Project with Data Portability API enabled
    2. OAuth 2.0 credentials (client_secrets.json)
    3. Python 3.11+

Author: Created for complete Google account data export
License: MIT
"""

import os
import sys
import json
import time
import argparse
import zipfile
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
import shutil

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    print("ERROR: Required packages not installed.")
    print("Install with: pip install google-auth-oauthlib google-api-python-client")
    sys.exit(1)


# Google Data Portability API scopes
SCOPES = ['https://www.googleapis.com/auth/dataportability']

# All available Google services for export
ALL_SERVICES = [
    'CHROME',
    'PLAY_GAMES_ACHIEVEMENTS',
    'PLAY_GAMES_APPLICATIONS',
    'PLAY_GAMES_GAMER_INFO',
    'PLAY_GAMES_GAMEPLAY',
    'PLAY_GAMES_PLAYER_EVENTS',
    'PLAY_GAMES_SETTINGS',
    'PLAY_GAMES_SNAPSHOTS',
    'YOUTUBE',
    'YOUTUBE_MUSIC',
    'GOOGLE_PHOTOS',
    'GOOGLE_ONE',
    'GMAIL',
    'DRIVE',
    'CALENDAR',
    'CONTACTS',
    'MAPS',
    'MY_ACTIVITY',
    'SEARCH_CONTRIBUTIONS',
    'ASSISTANT',
    'PAY',
    'FIT',
    'KEEP',
    'TASKS',
    'SHOPPING',
    'PLAY_STORE',
    'NEST',
    'NEWS',
    'BOOKMARKS',
    'SAVED',
    'LOCATION_HISTORY',
    'MAPS_MY_MAPS',
    'MAPS_STARRED_PLACES',
    'MAPS_YOUR_PLACES',
]


class GoogleExitExporter:
    """Exports all Google data using Data Portability API."""
    
    def __init__(self, output_dir: Path, services: Optional[List[str]] = None, max_parallel_downloads: int = 3):
        self.output_dir = Path(output_dir)
        self.services = services or ALL_SERVICES
        self.service = None
        self.credentials = None
        self.max_parallel_downloads = max_parallel_downloads
        
        # Create output directory structure
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir = self.output_dir / "raw_exports"
        self.organized_dir = self.output_dir / "organized"
        self.logs_dir = self.output_dir / "logs"
        
        for dir_path in [self.raw_dir, self.organized_dir, self.logs_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        self.log_file = self.logs_dir / f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        
    def log(self, message: str, level: str = "INFO"):
        """Log message to file and console."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"[{timestamp}] [{level}] {message}"
        print(log_message)
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(log_message + "\n")
    
    def authenticate(self) -> bool:
        """Authenticate with Google using OAuth 2.0."""
        creds = None
        token_file = self.output_dir / "token.json"
        secrets_file = Path("client_secrets.json")
        
        # Check for existing token
        if token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
            except Exception as e:
                self.log(f"Error loading token: {e}", "WARNING")
        
        # Check for client secrets
        if not secrets_file.exists():
            self.log("ERROR: client_secrets.json not found!", "ERROR")
            self.log("Please follow setup instructions in README.md", "ERROR")
            return False
        
        # If no valid credentials, get new ones
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    self.log(f"Error refreshing token: {e}", "WARNING")
                    creds = None
            
            if not creds:
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(secrets_file), SCOPES
                    )
                    creds = flow.run_local_server(port=0)
                except Exception as e:
                    self.log(f"Error during authentication: {e}", "ERROR")
                    return False
            
            # Save credentials for next time
            with open(token_file, 'w') as token:
                token.write(creds.to_json())
        
        self.credentials = creds
        return True
    
    def build_service(self):
        """Build the Data Portability API service."""
        try:
            self.service = build('dataportability', 'v1', credentials=self.credentials)
            self.log("Data Portability API service initialized")
        except Exception as e:
            self.log(f"Error building service: {e}", "ERROR")
            raise
    
    def estimate_export_size(self) -> Optional[Dict]:
        """Get size estimate for export before initiating."""
        if not hasattr(self, 'service') or self.service is None:
            # If service not built yet, just do basic estimate
            pass
        else:
            self.log("Checking export size estimate...")
        
        try:
            # Rough size estimates per service (in GB, very approximate)
            # These are conservative estimates based on typical usage
            service_size_estimates = {
                'GMAIL': 5.0,  # Average user: 2-10GB
                'DRIVE': 10.0,  # Average user: 5-50GB
                'GOOGLE_PHOTOS': 15.0,  # Average user: 10-100GB (highly variable)
                'YOUTUBE': 8.0,  # Playlists, subscriptions, watch history
                'YOUTUBE_MUSIC': 3.0,  # Music library
                'CALENDAR': 0.1,  # Events, usually small
                'CONTACTS': 0.01,  # Contact list, very small
                'MAPS': 2.0,  # Location history, saved places
                'MY_ACTIVITY': 3.0,  # Search history, activity logs
                'ASSISTANT': 1.0,  # Assistant interactions
                'CHROME': 1.0,  # Bookmarks, history
                'FIT': 0.5,  # Fitness data
                'KEEP': 0.1,  # Notes
                'TASKS': 0.01,  # Task lists
                'PAY': 0.5,  # Payment data
                'SHOPPING': 0.5,  # Shopping history
                'PLAY_STORE': 0.5,  # App purchases, reviews
                'NEST': 2.0,  # Nest device data
                'NEWS': 0.5,  # News preferences
                'BOOKMARKS': 0.1,  # Bookmarks
                'SAVED': 0.5,  # Saved items
                'LOCATION_HISTORY': 2.0,  # Location data
                'MAPS_MY_MAPS': 1.0,  # Custom maps
                'MAPS_STARRED_PLACES': 0.1,  # Starred places
                'MAPS_YOUR_PLACES': 0.5,  # Your places
            }
            
            # Calculate rough estimate
            estimated_total = sum(
                service_size_estimates.get(service, 2.0)  # Default 2GB for unknown services
                for service in self.services
            )
            
            # Check available disk space
            available = shutil.disk_usage(self.output_dir).free
            available_gb = available / (1024**3)
            
            estimate = {
                'services_count': len(self.services),
                'estimated_size_gb': estimated_total,
                'available_space_gb': available_gb,
                'sufficient_space': available_gb >= estimated_total * 1.1,  # 10% buffer
                'services': self.services,
                'service_breakdown': {
                    service: service_size_estimates.get(service, 2.0)
                    for service in self.services
                }
            }
            
            return estimate
            
        except Exception as e:
            if hasattr(self, 'log'):
                self.log(f"Error estimating size: {e}", "WARNING")
            return None
    
    def check_disk_space(self, required_gb: float) -> bool:
        """Check if sufficient disk space is available."""
        try:
            available = shutil.disk_usage(self.output_dir).free
            available_gb = available / (1024**3)
            
            # Require 10% buffer
            required_with_buffer = required_gb * 1.1
            
            if available_gb < required_with_buffer:
                self.log("=" * 60, "WARNING")
                self.log("INSUFFICIENT DISK SPACE!", "WARNING")
                self.log("=" * 60, "WARNING")
                self.log(f"Required: {required_with_buffer:.1f} GB (with 10% buffer)", "WARNING")
                self.log(f"Available: {available_gb:.1f} GB", "WARNING")
                self.log(f"Shortfall: {required_with_buffer - available_gb:.1f} GB", "WARNING")
                self.log("=" * 60, "WARNING")
                return False
            else:
                self.log(f"✓ Sufficient disk space available: {available_gb:.1f} GB")
                return True
        except Exception as e:
            self.log(f"Error checking disk space: {e}", "WARNING")
            return True  # Continue anyway if check fails
    
    def initiate_export(self) -> Optional[str]:
        """Initiate export job for all selected services."""
        self.log(f"Initiating export for {len(self.services)} services...")
        
        try:
            request_body = {
                'resourceTypes': self.services
            }
            
            request = self.service.portabilityArchive().initiate(body=request_body)
            response = request.execute()
            
            job_id = response.get('name', '').split('/')[-1]
            self.log(f"Export job initiated: {job_id}")
            return job_id
            
        except HttpError as e:
            self.log(f"Error initiating export: {e}", "ERROR")
            if e.resp.status == 403:
                self.log("API not enabled or insufficient permissions", "ERROR")
                self.log("Check: https://console.cloud.google.com/apis/library/dataportability.googleapis.com", "ERROR")
            return None
        except Exception as e:
            self.log(f"Unexpected error: {e}", "ERROR")
            return None
    
    def check_export_status(self, job_id: str) -> Dict:
        """Check status of export job."""
        try:
            name = f"portabilityArchive/{job_id}"
            request = self.service.portabilityArchive().get(name=name)
            response = request.execute()
            return response
        except HttpError as e:
            self.log(f"Error checking status: {e}", "ERROR")
            return {}
        except Exception as e:
            self.log(f"Unexpected error: {e}", "ERROR")
            return {}
    
    def wait_for_export(self, job_id: str, max_wait_hours: int = 48) -> bool:
        """Wait for export to complete, checking periodically."""
        self.log(f"Waiting for export to complete (max {max_wait_hours} hours)...")
        
        start_time = time.time()
        max_wait_seconds = max_wait_hours * 3600
        check_interval = 300  # Check every 5 minutes
        
        while True:
            elapsed = time.time() - start_time
            if elapsed > max_wait_seconds:
                self.log("Export timeout reached", "ERROR")
                return False
            
            status = self.check_export_status(job_id)
            state = status.get('state', 'UNKNOWN')
            
            self.log(f"Export status: {state}")
            
            if state == 'SUCCEEDED':
                self.log("Export completed successfully!")
                return True
            elif state == 'FAILED':
                self.log("Export failed!", "ERROR")
                error = status.get('error', {})
                self.log(f"Error details: {error}", "ERROR")
                return False
            elif state in ['IN_PROGRESS', 'PENDING']:
                # Estimate progress if available
                if 'progress' in status:
                    progress = status['progress']
                    self.log(f"Progress: {progress}")
                
                time.sleep(check_interval)
            else:
                self.log(f"Unknown state: {state}", "WARNING")
                time.sleep(check_interval)
    
    def download_file_with_resume(self, download_url: str, file_path: Path, file_size: Optional[int] = None) -> bool:
        """Download a file with resume capability."""
        import requests
        try:
            from tqdm import tqdm
            use_tqdm = True
        except ImportError:
            use_tqdm = False
            self.log("tqdm not available, progress bars disabled", "WARNING")
        
        # Check if file already exists (partial download)
        resume_pos = 0
        if file_path.exists():
            resume_pos = file_path.stat().st_size
            if file_size and resume_pos >= file_size:
                self.log(f"File already complete: {file_path.name}")
                return True
            if resume_pos > 0:
                self.log(f"Resuming download: {file_path.name} (from {resume_pos} bytes)")
        
        try:
            headers = {}
            if resume_pos > 0:
                headers['Range'] = f'bytes={resume_pos}-'
            
            response = requests.get(download_url, headers=headers, stream=True, timeout=30)
            
            # Handle partial content (resume)
            if response.status_code == 206:
                self.log(f"Resuming from byte {resume_pos}")
            elif response.status_code == 200:
                if resume_pos > 0:
                    # Server doesn't support resume, restart
                    self.log("Server doesn't support resume, restarting download")
                    resume_pos = 0
                    file_path.unlink()  # Delete partial file
            
            response.raise_for_status()
            
            # Get total size
            total_size = int(response.headers.get('content-length', 0))
            if resume_pos > 0:
                total_size += resume_pos
            
            # Download with progress bar
            mode = 'ab' if resume_pos > 0 else 'wb'
            with open(file_path, mode) as f:
                if resume_pos > 0:
                    f.seek(resume_pos)
                
                if use_tqdm:
                    with tqdm(
                        total=total_size,
                        initial=resume_pos,
                        unit='B',
                        unit_scale=True,
                        unit_divisor=1024,
                        desc=file_path.name[:50],
                        leave=False
                    ) as pbar:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                                pbar.update(len(chunk))
                else:
                    # Simple progress without tqdm
                    downloaded = resume_pos
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_size > 0:
                                percent = (downloaded / total_size) * 100
                                if downloaded % (1024 * 1024) == 0:  # Log every MB
                                    self.log(f"  Progress: {percent:.1f}% ({downloaded / (1024*1024):.1f} MB)")
            
            self.log(f"✓ Downloaded: {file_path.name} ({file_path.stat().st_size / (1024*1024):.1f} MB)")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"Error downloading {file_path.name}: {e}", "ERROR")
            return False
        except Exception as e:
            self.log(f"Unexpected error downloading {file_path.name}: {e}", "ERROR")
            return False
    
    def download_export(self, job_id: str, max_parallel: int = 3) -> bool:
        """Download completed export with support for multiple files and resume."""
        self.log("Downloading export...")
        
        try:
            # Get export info
            name = f"portabilityArchive/{job_id}"
            request = self.service.portabilityArchive().get(name=name)
            response = request.execute()
            
            # Get download URLs
            files = response.get('files', [])
            if not files:
                self.log("No files to download", "WARNING")
                return False
            
            self.log(f"Found {len(files)} file(s) to download")
            
            # Check disk space with actual file sizes
            total_size = sum(int(f.get('size', 0)) for f in files)
            if total_size > 0:
                total_size_gb = total_size / (1024**3)
                available = shutil.disk_usage(self.output_dir).free
                available_gb = available / (1024**3)
                
                self.log("=" * 60)
                self.log("ACTUAL EXPORT SIZE", "INFO")
                self.log("=" * 60)
                self.log(f"Number of files: {len(files)}")
                self.log(f"Total size: {total_size_gb:.2f} GB")
                self.log(f"Available space: {available_gb:.2f} GB")
                
                required_with_buffer = total_size_gb * 1.1  # 10% buffer
                
                if total_size > available * 0.9:  # Leave 10% buffer
                    self.log("=" * 60, "WARNING")
                    self.log("⚠️  WARNING: May not have enough disk space!", "WARNING")
                    self.log(f"Required (with buffer): {required_with_buffer:.2f} GB", "WARNING")
                    self.log(f"Available: {available_gb:.2f} GB", "WARNING")
                    self.log(f"Shortfall: {required_with_buffer - available_gb:.2f} GB", "WARNING")
                    self.log("=" * 60, "WARNING")
                    self.log("", "WARNING")
                    response = input("Continue anyway? (yes/no): ").strip().lower()
                    if response not in ['yes', 'y']:
                        self.log("Download cancelled by user", "INFO")
                        return False
                else:
                    self.log(f"✓ Sufficient space available ({available_gb:.2f} GB > {required_with_buffer:.2f} GB required)")
                
                self.log("=" * 60)
            
            # Import for downloads
            import requests
            try:
                from concurrent.futures import ThreadPoolExecutor, as_completed
            except ImportError:
                # Fallback for older Python versions
                self.log("concurrent.futures not available, using sequential downloads", "WARNING")
                max_parallel = 1
            
            def download_single_file(file_info, index):
                """Download a single file."""
                file_name = file_info.get('name', f'file_{index}.zip')
                download_url = file_info.get('downloadUrl', '')
                file_size = file_info.get('size')
                
                if not download_url:
                    self.log(f"No download URL for {file_name}", "WARNING")
                    return False, file_name
                
                file_path = self.raw_dir / file_name
                
                # Check if already downloaded
                if file_path.exists() and file_size:
                    if file_path.stat().st_size == file_size:
                        self.log(f"Already downloaded: {file_name}")
                        return True, file_name
                
                self.log(f"Downloading {file_name} ({index}/{len(files)})...")
                success = self.download_file_with_resume(download_url, file_path, file_size)
                return success, file_name
            
            # Download files (with optional parallel downloads)
            if max_parallel > 1 and len(files) > 1:
                try:
                    from concurrent.futures import ThreadPoolExecutor, as_completed
                except ImportError:
                    self.log("concurrent.futures not available, using sequential downloads", "WARNING")
                    max_parallel = 1
                
                if max_parallel > 1:
                    self.log(f"Downloading {len(files)} files in parallel (max {max_parallel} at a time)...")
                    with ThreadPoolExecutor(max_workers=max_parallel) as executor:
                    futures = {
                        executor.submit(download_single_file, file_info, i+1): file_info
                        for i, file_info in enumerate(files)
                    }
                    
                    completed = 0
                    failed = []
                    
                    for future in as_completed(futures):
                        completed += 1
                        success, file_name = future.result()
                        if not success:
                            failed.append(file_name)
                        self.log(f"Progress: {completed}/{len(files)} files downloaded")
                    
                    if failed:
                        self.log(f"Failed to download {len(failed)} file(s): {failed}", "ERROR")
                        return False
            else:
                # Sequential download
                for i, file_info in enumerate(files, 1):
                    success, file_name = download_single_file(file_info, i)
                    if not success:
                        self.log(f"Failed to download {file_name}", "ERROR")
                        return False
            
            self.log(f"✓ Successfully downloaded all {len(files)} file(s)")
            return True
            
        except Exception as e:
            self.log(f"Error downloading export: {e}", "ERROR")
            import traceback
            self.log(traceback.format_exc(), "ERROR")
            return False
    
    def organize_exports(self):
        """Organize downloaded exports by service type."""
        self.log("Organizing exported data...")
        
        # Extract and organize ZIP files
        for zip_file in self.raw_dir.glob("*.zip"):
            self.log(f"Extracting {zip_file.name}...")
            
            try:
                extract_dir = self.organized_dir / zip_file.stem
                extract_dir.mkdir(parents=True, exist_ok=True)
                
                with zipfile.ZipFile(zip_file, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
                
                self.log(f"Extracted to {extract_dir}")
                
            except Exception as e:
                self.log(f"Error extracting {zip_file}: {e}", "ERROR")
        
        self.log("Organization complete!")
    
    def create_summary(self):
        """Create summary report of exported data."""
        summary = {
            'export_date': datetime.now().isoformat(),
            'services_exported': self.services,
            'output_directory': str(self.output_dir),
            'raw_exports': [f.name for f in self.raw_dir.glob("*.zip")],
            'organized_data': [f.name for f in self.organized_dir.iterdir() if f.is_dir()],
        }
        
        summary_file = self.output_dir / "export_summary.json"
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2)
        
        self.log(f"Summary saved to {summary_file}")
    
    def export_all(self, check_space_first: bool = True) -> bool:
        """Complete export workflow."""
        self.log("=" * 60)
        self.log("Google Exit - Complete Data Export")
        self.log("=" * 60)
        
        # Step 1: Authenticate
        if not self.authenticate():
            return False
        
        # Step 2: Build service
        try:
            self.build_service()
        except Exception as e:
            return False
        
        # Step 2.5: Estimate space requirements
        if check_space_first:
            estimate = self.estimate_export_size()
            if estimate:
                self.log("=" * 60)
                self.log("SPACE REQUIREMENT ESTIMATE", "INFO")
                self.log("=" * 60)
                self.log(f"Services to export: {estimate['services_count']}")
                self.log(f"Estimated size: {estimate['estimated_size_gb']:.1f} GB")
                self.log(f"Available space: {estimate['available_space_gb']:.1f} GB")
                
                if estimate['estimated_size_gb']:
                    # Show service breakdown
                    self.log("Service breakdown:", "INFO")
                    for service, size in sorted(estimate.get('service_breakdown', {}).items(), 
                                                key=lambda x: x[1], reverse=True):
                        self.log(f"  {service}: ~{size:.1f} GB", "INFO")
                    self.log("")
                    
                    if not self.check_disk_space(estimate['estimated_size_gb']):
                        self.log("", "WARNING")
                        self.log("⚠️  WARNING: You may not have enough disk space!", "WARNING")
                        self.log("The export may fail or be incomplete.", "WARNING")
                        self.log("", "WARNING")
                        self.log("Consider:", "WARNING")
                        self.log("  1. Free up disk space", "WARNING")
                        self.log("  2. Choose a different output directory", "WARNING")
                        self.log("  3. Export fewer services at once", "WARNING")
                        self.log("  4. Use --skip-space-check to proceed anyway", "WARNING")
                        self.log("", "WARNING")
                        response = input("Continue anyway? (yes/no): ").strip().lower()
                        if response not in ['yes', 'y']:
                            self.log("Export cancelled by user", "INFO")
                            return False
                
                self.log("=" * 60)
        
        # Step 3: Initiate export
        job_id = self.initiate_export()
        if not job_id:
            return False
        
        # Step 4: Wait for completion
        if not self.wait_for_export(job_id):
            return False
        
        # Step 5: Download export
        if not self.download_export(job_id, max_parallel=self.max_parallel_downloads):
            return False
        
        # Step 6: Organize exports
        self.organize_exports()
        
        # Step 7: Create summary
        self.create_summary()
        
        self.log("=" * 60)
        self.log("Export complete!")
        self.log(f"Data saved to: {self.output_dir}")
        self.log("=" * 60)
        
        return True


def print_deletion_instructions():
    """Print instructions for account deletion."""
    print("\n" + "=" * 60)
    print("ACCOUNT DELETION INSTRUCTIONS")
    print("=" * 60)
    print("\nAccount deletion must be done manually for security reasons.")
    print("\nSteps to delete your Google account:")
    print("1. Go to: https://myaccount.google.com/")
    print("2. Click 'Data & Privacy' in the left menu")
    print("3. Scroll to 'More options'")
    print("4. Click 'Delete your Google Account'")
    print("5. Follow the on-screen instructions")
    print("\n⚠️  WARNING: This action is PERMANENT and cannot be undone!")
    print("⚠️  Make sure you have downloaded all your data first!")
    print("\nRecovery period: Up to 30 days (if you change your mind)")
    print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Export all Google data and prepare for account deletion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Export all data to default location
  python google-exit.py

  # Export to specific directory
  python google-exit.py --output ~/Google_Data_Export

  # Export specific services only
  python google-exit.py --services GOOGLE_PHOTOS GMAIL DRIVE

Setup:
  1. Create Google Cloud Project
  2. Enable Data Portability API
  3. Create OAuth 2.0 credentials
  4. Save as client_secrets.json
  5. Run this script

See README.md for detailed setup instructions.
        """
    )
    
    parser.add_argument(
        '--output',
        type=str,
        default='~/Google_Data_Export',
        help='Output directory for exported data (default: ~/Google_Data_Export)'
    )
    
    parser.add_argument(
        '--services',
        nargs='+',
        choices=ALL_SERVICES,
        help='Specific services to export (default: all services)'
    )
    
    parser.add_argument(
        '--max-wait-hours',
        type=int,
        default=48,
        help='Maximum hours to wait for export (default: 48)'
    )
    
    parser.add_argument(
        '--skip-organization',
        action='store_true',
        help='Skip organizing extracted files'
    )
    
    parser.add_argument(
        '--max-parallel-downloads',
        type=int,
        default=3,
        help='Maximum parallel downloads (default: 3, set to 1 for sequential)'
    )
    
    parser.add_argument(
        '--skip-space-check',
        action='store_true',
        help='Skip disk space check before export'
    )
    
    parser.add_argument(
        '--check-space-only',
        action='store_true',
        help='Only check space requirements, do not export'
    )
    
    args = parser.parse_args()
    
    # Expand user path
    output_dir = Path(args.output).expanduser().resolve()
    
    # Create exporter
    exporter = GoogleExitExporter(
        output_dir, 
        args.services,
        max_parallel_downloads=args.max_parallel_downloads
    )
    
    # If only checking space, do that and exit
    if args.check_space_only:
        print("\n" + "=" * 60)
        print("Google Exit - Space Requirement Check")
        print("=" * 60 + "\n")
        
        estimate = exporter.estimate_export_size()
        if estimate:
            print(f"Services to export: {estimate['services_count']}")
            print(f"Estimated total size: {estimate['estimated_size_gb']:.1f} GB")
            print(f"Available space: {estimate['available_space_gb']:.1f} GB")
            print()
            
            # Show service breakdown
            if 'service_breakdown' in estimate:
                print("Service size breakdown:")
                for service, size in sorted(estimate['service_breakdown'].items(), 
                                            key=lambda x: x[1], reverse=True):
                    print(f"  {service:25} ~{size:6.1f} GB")
                print()
            
            if estimate['estimated_size_gb']:
                required_with_buffer = estimate['estimated_size_gb'] * 1.1
                print(f"Required (with 10% buffer): {required_with_buffer:.1f} GB")
                print()
                
                if estimate['available_space_gb'] >= required_with_buffer:
                    print("✓ You have sufficient disk space for this export!")
                    return 0
                else:
                    print("⚠️  WARNING: You may not have enough disk space!")
                    print(f"   Shortfall: {required_with_buffer - estimate['available_space_gb']:.1f} GB")
                    return 1
        else:
            print("Could not estimate space requirements.")
            return 1
    
    # Run export
    success = exporter.export_all(check_space_first=not args.skip_space_check)
    
    if success:
        print_deletion_instructions()
        return 0
    else:
        print("\nExport failed. Check logs for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

