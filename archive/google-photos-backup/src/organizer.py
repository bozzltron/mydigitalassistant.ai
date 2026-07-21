"""
Google Photos Takeout Organizer

Organizes photos from Google Takeout exports into a clean, date-based structure.
"""

import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List
from tqdm import tqdm
from PIL import Image
from PIL.ExifTags import TAGS


class GooglePhotosOrganizer:
    """Organizes Google Takeout exports into a structured backup."""
    
    def __init__(
        self,
        input_dir: Path,
        output_dir: Path,
        organize_by_date: bool = True,
        preserve_metadata: bool = True,
        remove_duplicates: bool = True
    ):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.organize_by_date = organize_by_date
        self.preserve_metadata = preserve_metadata
        self.remove_duplicates = remove_duplicates
        
        # Supported image extensions
        self.image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.heic'}
        # Supported video extensions
        self.video_extensions = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}
        
        # Track processed files for duplicate detection
        self.processed_hashes: Dict[str, Path] = {}
        
    def organize(self) -> None:
        """Main organization method."""
        print(f"Organizing Google Photos from: {self.input_dir}")
        print(f"Output directory: {self.output_dir}")
        
        # Create output directory structure
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Find all media files in input directory
        media_files = self._find_media_files(self.input_dir)
        
        if not media_files:
            print("No media files found. Make sure you've extracted the Takeout ZIP files.")
            return
        
        print(f"Found {len(media_files)} media files to organize")
        
        # Process each file
        for file_path in tqdm(media_files, desc="Organizing photos"):
            try:
                self._process_file(file_path)
            except Exception as e:
                print(f"Error processing {file_path}: {e}")
                continue
        
        print(f"\n✓ Organization complete! Files saved to: {self.output_dir}")
    
    def _find_media_files(self, directory: Path) -> List[Path]:
        """Recursively find all media files in directory."""
        media_files = []
        
        for ext in self.image_extensions | self.video_extensions:
            media_files.extend(directory.rglob(f"*{ext}"))
            media_files.extend(directory.rglob(f"*{ext.upper()}"))
        
        return sorted(media_files)
    
    def _process_file(self, file_path: Path) -> None:
        """Process a single media file."""
        # Check for duplicates
        if self.remove_duplicates:
            file_hash = self._calculate_hash(file_path)
            if file_hash in self.processed_hashes:
                # Duplicate found, skip
                return
            self.processed_hashes[file_hash] = file_path
        
        # Determine date for organization
        date = self._get_file_date(file_path)
        
        # Create destination path
        if self.organize_by_date:
            dest_dir = self.output_dir / "photos" / date.strftime("%Y") / date.strftime("%m")
        else:
            dest_dir = self.output_dir / "photos"
        
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate destination filename
        dest_filename = self._generate_filename(file_path, date)
        dest_path = dest_dir / dest_filename
        
        # Copy file (preserve original)
        shutil.copy2(file_path, dest_path)
        
        # Merge metadata if requested
        if self.preserve_metadata and file_path.suffix.lower() in self.image_extensions:
            self._merge_metadata(file_path, dest_path)
    
    def _get_file_date(self, file_path: Path) -> datetime:
        """Get the date for a file from metadata or file timestamp."""
        # Try to find JSON metadata file
        json_path = self._find_metadata_json(file_path)
        
        if json_path:
            date = self._extract_date_from_json(json_path)
            if date:
                return date
        
        # Try EXIF data
        if file_path.suffix.lower() in self.image_extensions:
            date = self._extract_date_from_exif(file_path)
            if date:
                return date
        
        # Fallback to file modification time
        return datetime.fromtimestamp(file_path.stat().st_mtime)
    
    def _find_metadata_json(self, file_path: Path) -> Optional[Path]:
        """Find associated JSON metadata file for a photo."""
        # Google Takeout stores JSON files with same name
        json_path = file_path.with_suffix('.json')
        if json_path.exists():
            return json_path
        
        # Sometimes JSON is in a metadata subfolder
        metadata_dir = file_path.parent / "metadata"
        if metadata_dir.exists():
            json_path = metadata_dir / f"{file_path.stem}.json"
            if json_path.exists():
                return json_path
        
        return None
    
    def _extract_date_from_json(self, json_path: Path) -> Optional[datetime]:
        """Extract date from Google Takeout JSON metadata."""
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Try different date fields
            date_str = (
                data.get('photoTakenTime', {}).get('timestamp') or
                data.get('creationTime', {}).get('timestamp') or
                data.get('modificationTime', {}).get('timestamp')
            )
            
            if date_str:
                # Google uses Unix timestamp (seconds)
                return datetime.fromtimestamp(int(date_str))
        except Exception:
            pass
        
        return None
    
    def _extract_date_from_exif(self, file_path: Path) -> Optional[datetime]:
        """Extract date from EXIF data."""
        try:
            img = Image.open(file_path)
            exif = img.getexif()
            
            if exif:
                # Try common EXIF date tags
                for tag_id, value in exif.items():
                    tag = TAGS.get(tag_id, tag_id)
                    if tag in ['DateTime', 'DateTimeOriginal', 'DateTimeDigitized']:
                        try:
                            return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                        except ValueError:
                            continue
        except Exception:
            pass
        
        return None
    
    def _generate_filename(self, file_path: Path, date: datetime) -> str:
        """Generate organized filename with date prefix."""
        date_prefix = date.strftime("%Y-%m-%d_%H-%M-%S")
        return f"{date_prefix}_{file_path.name}"
    
    def _merge_metadata(self, source_path: Path, dest_path: Path) -> None:
        """Merge JSON metadata into EXIF when possible."""
        json_path = self._find_metadata_json(source_path)
        if not json_path:
            return
        
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            
            # Open image and update EXIF
            img = Image.open(dest_path)
            exif_dict = img.getexif()
            
            # Add description if available
            if 'description' in metadata:
                # Note: EXIF description tag is 270
                exif_dict[270] = metadata['description']
            
            # Save with updated EXIF
            img.save(dest_path, exif=exif_dict)
        except Exception:
            # If metadata merge fails, continue without it
            pass
    
    def _calculate_hash(self, file_path: Path) -> str:
        """Calculate MD5 hash of file for duplicate detection."""
        import hashlib
        
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()


def main():
    """Command-line entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Organize Google Photos Takeout exports"
    )
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Input directory containing extracted Takeout files"
    )
    parser.add_argument(
        "--output",
        type=str,
        required=True,
        help="Output directory for organized photos"
    )
    parser.add_argument(
        "--no-date-organization",
        action="store_true",
        help="Don't organize by date (flat structure)"
    )
    parser.add_argument(
        "--no-metadata",
        action="store_true",
        help="Don't merge metadata from JSON files"
    )
    parser.add_argument(
        "--keep-duplicates",
        action="store_true",
        help="Keep duplicate files"
    )
    
    args = parser.parse_args()
    
    organizer = GooglePhotosOrganizer(
        input_dir=Path(args.input),
        output_dir=Path(args.output),
        organize_by_date=not args.no_date_organization,
        preserve_metadata=not args.no_metadata,
        remove_duplicates=not args.keep_duplicates
    )
    
    organizer.organize()


if __name__ == "__main__":
    main()
