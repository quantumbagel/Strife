#!/usr/bin/env python3
"""
Utility script to properly convert PNG images to WebP format and clean up original files.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Try importing Pillow in case cwebp is not installed
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

def check_cwebp():
    """Check if the cwebp tool is available on the system path."""
    return shutil.which("cwebp") is not None

def format_size(bytes_size):
    """Format size in bytes to a human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} TB"

def convert_with_cwebp(cwebp_path, input_path, output_path, lossless, quality):
    """Convert PNG to WebP using cwebp command-line tool."""
    cmd = [cwebp_path]
    if lossless:
        cmd.append("-lossless")
    else:
        cmd.extend(["-q", str(quality)])
    
    cmd.extend([str(input_path), "-o", str(output_path)])
    
    # Run silently
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode().strip())

def convert_with_pil(input_path, output_path, lossless, quality):
    """Convert PNG to WebP using Pillow library."""
    if not HAS_PIL:
        raise RuntimeError("Pillow is not installed.")
    
    with Image.open(input_path) as im:
        if lossless:
            im.save(output_path, "WEBP", lossless=True)
        else:
            im.save(output_path, "WEBP", quality=quality)

def process_file(input_path, lossless, quality, keep_original, dry_run, cwebp_path):
    """Process a single PNG file: convert and clean up."""
    input_path = Path(input_path).resolve()
    if not input_path.exists():
        print(f"[-] Error: File not found: {input_path}")
        return None
    
    output_path = input_path.with_suffix(".webp")
    orig_size = input_path.stat().st_size
    
    print(f"[*] Processing: {input_path.name} ({format_size(orig_size)})")
    
    if dry_run:
        print(f"    [Dry-run] Would convert to: {output_path.name}")
        if not keep_original:
            print(f"    [Dry-run] Would delete original: {input_path.name}")
        return {
            "success": True,
            "orig_size": orig_size,
            "new_size": orig_size,  # placeholder
            "cleaned": not keep_original
        }
        
    try:
        if cwebp_path:
            convert_with_cwebp(cwebp_path, input_path, output_path, lossless, quality)
        elif HAS_PIL:
            convert_with_pil(input_path, output_path, lossless, quality)
        else:
            print("[-] Error: Neither 'cwebp' nor 'Pillow' is available.")
            sys.exit(1)
            
        # Verify output file exists and has content
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("Generated WebP file is empty or missing.")
            
        new_size = output_path.stat().st_size
        size_diff = orig_size - new_size
        pct_change = (size_diff / orig_size) * 100 if orig_size > 0 else 0
        
        print(f"    [+] Created: {output_path.name} ({format_size(new_size)})")
        print(f"    [+] Saved: {format_size(size_diff)} ({pct_change:.1f}%)")
        
        cleaned = False
        if not keep_original:
            input_path.unlink()
            print(f"    [+] Deleted original: {input_path.name}")
            cleaned = True
            
        return {
            "success": True,
            "orig_size": orig_size,
            "new_size": new_size,
            "cleaned": cleaned
        }
    except Exception as e:
        print(f"    [-] Failed to convert: {e}")
        # Clean up partial output if any
        if output_path.exists():
            try:
                output_path.unlink()
            except Exception:
                pass
        return None

def main():
    parser = argparse.ArgumentParser(
        description="Convert PNG images to WebP and clean up the originals properly."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files or directories to process. Defaults to the assets/emoji directory."
    )
    parser.add_argument(
        "--lossy",
        action="store_true",
        help="Use lossy compression instead of lossless (default)."
    )
    parser.add_argument(
        "-q", "--quality",
        type=int,
        default=80,
        help="Quality for lossy compression (0-100). Default is 80."
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the original PNG files (do not delete them)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what actions would be performed without actually modifying files."
    )
    parser.add_argument(
        "-r", "--recursive",
        action="store_true",
        help="Recursively search directories for PNG files."
    )
    
    args = parser.parse_args()
    
    # 1. Determine conversion method
    cwebp_path = shutil.which("cwebp")
    if cwebp_path:
        print(f"[*] Using 'cwebp' for conversion: {cwebp_path}")
    elif HAS_PIL:
        print("[*] Using Python 'Pillow' library for conversion.")
    else:
        print("[-] Error: To convert images, you need either the 'cwebp' command-line tool or the 'Pillow' Python package.")
        print("    To install cwebp on macOS:  brew install webp")
        print("    To install Pillow:           pip install Pillow")
        sys.exit(1)
        
    # 2. Gather target paths
    targets = args.paths
    if not targets:
        # Default to assets/emoji relative to project root
        default_dir = Path(__file__).resolve().parents[1] / "assets" / "emoji"
        if default_dir.exists():
            targets = [str(default_dir)]
        else:
            targets = ["."]
            
    # 3. Collect PNG files
    png_files = []
    for target in targets:
        target_path = Path(target).resolve()
        if target_path.is_file():
            if target_path.suffix.lower() == ".png":
                png_files.append(target_path)
        elif target_path.is_dir():
            pattern = "**/*.png" if args.recursive else "*.png"
            # Ignore standard virtualenv and git directories if searching recursively
            for p in target_path.glob(pattern):
                if ".venv" not in p.parts and ".git" not in p.parts:
                    png_files.append(p)
                    
    # Deduplicate and sort
    png_files = sorted(list(set(png_files)))
    
    if not png_files:
        print("[*] No PNG files found to convert.")
        return
        
    print(f"[*] Found {len(png_files)} PNG files to process.")
    if args.dry_run:
        print("[!] Running in DRY-RUN mode. No files will be modified.")
        
    # 4. Process files
    stats = {
        "total": 0,
        "success": 0,
        "total_orig_size": 0,
        "total_new_size": 0,
        "cleaned_count": 0
    }
    
    lossless = not args.lossy
    
    for png_path in png_files:
        res = process_file(
            input_path=png_path,
            lossless=lossless,
            quality=args.quality,
            keep_original=args.keep,
            dry_run=args.dry_run,
            cwebp_path=cwebp_path
        )
        stats["total"] += 1
        if res:
            stats["success"] += 1
            stats["total_orig_size"] += res["orig_size"]
            stats["total_new_size"] += res["new_size"]
            if res["cleaned"]:
                stats["cleaned_count"] += 1
                
    # 5. Output summary
    print("\n" + "="*40)
    print(" CONVERSION SUMMARY")
    print("="*40)
    print(f"Total files found:  {stats['total']}")
    print(f"Successfully done:  {stats['success']}")
    if stats['success'] > 0 and not args.dry_run:
        saved = stats['total_orig_size'] - stats['total_new_size']
        saved_pct = (saved / stats['total_orig_size']) * 100 if stats['total_orig_size'] > 0 else 0
        print(f"Original size:      {format_size(stats['total_orig_size'])}")
        print(f"New size:           {format_size(stats['total_new_size'])}")
        print(f"Space saved:        {format_size(saved)} ({saved_pct:.1f}%)")
    if stats['cleaned_count'] > 0:
        print(f"Deleted PNG files:  {stats['cleaned_count']}")
    print("="*40)

if __name__ == "__main__":
    main()
