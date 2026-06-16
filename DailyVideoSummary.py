import os
import subprocess
import datetime
import shutil
import tempfile
from pathlib import Path

# --- Configuration ---
SOURCE_DIR = Path(r"J:\EXTERNAL\Sighthound")
DEST_DIR_ROOT = Path(r"S:\Sighthound")
FRAMERATE = 10  # Adjust the frames per second of your video here

# Dynamically find the folder this script is running in and point to ffmpeg.exe
SCRIPT_DIR = Path(__file__).resolve().parent
FFMPEG_EXE = SCRIPT_DIR / "ffmpeg.exe"

def create_video_from_images(image_paths, output_path, image_date):
    """Pipes sorted images directly into the local FFmpeg to create an MP4."""
    cmd = [
        str(FFMPEG_EXE), '-y',
        '-f', 'image2pipe',
        '-framerate', str(FRAMERATE),
        '-i', '-',
        '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p',
        '-crf', '23', 
        '-metadata', f'creation_time={image_date}T12:00:00.000000Z',
        output_path
    ]
    
    try:
        process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        
        for img_path in image_paths:
            with open(img_path, 'rb') as f:
                process.stdin.write(f.read())
        
        process.stdin.close()
        _, stderr = process.communicate()
        
        if process.returncode != 0:
            print(f"[-] FFmpeg Error creating video for {image_date}:\n{stderr.decode('utf-8')}")
            return False
        return True
    except Exception as e:
        print(f"[-] Exception during video creation: {e}")
        return False

def append_videos(existing_video, new_video, final_output, image_date):
    """Joins two MP4s together using the local FFmpeg."""
    with tempfile.NamedTemporaryFile('w', delete=False, suffix='.txt') as f:
        f.write(f"file '{Path(existing_video).as_posix()}'\n")
        f.write(f"file '{Path(new_video).as_posix()}'\n")
        concat_file = f.name
        
    cmd = [
        str(FFMPEG_EXE), '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', concat_file,
        '-c', 'copy', 
        '-metadata', f'creation_time={image_date}T12:00:00.000000Z',
        final_output
    ]
    
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    os.remove(concat_file)
    
    if result.returncode != 0:
        print(f"[-] FFmpeg Error appending video:\n{result.stderr.decode('utf-8')}")
        return False
    return True

def main():
    # Pre-flight check for portable FFmpeg
    if not FFMPEG_EXE.exists():
        print(f"[-] Critical Error: Could not find ffmpeg.exe.")
        print(f"[-] Please ensure 'ffmpeg.exe' is placed in the exact same folder as this script:")
        print(f"    {SCRIPT_DIR}")
        return

    print("[*] Searching for images...")
    all_jpgs = list(SOURCE_DIR.rglob("thumbs/*.jpg"))
    
    if not all_jpgs:
        print("[-] No .jpg files found in 'thumbs' directories.")
        return

    # 1. Group images by (Image Date, Camera Parent Folder Name)
    groups = {}
    for jpg in all_jpgs:
        mtime = os.path.getmtime(jpg)
        dt = datetime.datetime.fromtimestamp(mtime)
        img_date = dt.strftime("%Y-%m-%d")
        
        if len(jpg.parents) >= 3:
            camera_name = jpg.parents[2].name
        else:
            camera_name = "unknown"
            
        key = (img_date, camera_name)
        if key not in groups:
            groups[key] = []
        groups[key].append(jpg)

    # 2. Sort the files internally by System Modified Date, then by Filename
    for key in groups:
        groups[key].sort(key=lambda x: (os.path.getmtime(x), x.name))

    # 3. Process each group
    for (img_date, camera_name), files in groups.items():
        print(f"\n[*] Processing: {img_date} | {camera_name} ({len(files)} images)")
        
        # --- NEW DIRECTORY LOGIC ---
        # Parse the year and month directly from the image date
        year_str, month_str, _ = img_date.split('-')
        
        # Construct path: S:\Sighthound\YYYY\MM\YYYY-MM-DD\Video Summary\
        dest_folder = DEST_DIR_ROOT / year_str / month_str / img_date / "Video Summary"
        dest_folder.mkdir(parents=True, exist_ok=True)
        # ---------------------------

        dest_filename = f"video-{img_date}-{camera_name}.mp4"
        dest_filepath = dest_folder / dest_filename
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            temp_new_video = temp_dir_path / "new_part.mp4"
            
            # Step A: Convert the current batch of images to a video
            success = create_video_from_images(files, str(temp_new_video), img_date)
            if not success:
                print(f"[-] Skipping {dest_filename} due to creation error.")
                continue
                
            # Step B: Determine if we are appending or creating fresh
            if dest_filepath.exists():
                print(f"[*] Found existing video for {dest_filename}. Appending...")
                temp_combined = temp_dir_path / "combined.mp4"
                
                success = append_videos(dest_filepath, temp_new_video, str(temp_combined), img_date)
                if success:
                    shutil.move(str(temp_combined), str(dest_filepath))
                else:
                    print(f"[-] Failed to append data. Leaving existing video untouched.")
                    continue
            else:
                print(f"[*] Creating new video: {dest_filename}")
                shutil.move(str(temp_new_video), str(dest_filepath))
                
            # Step C: Cleanup source images upon ultimate success
            print(f"[+] Video updated successfully. Deleting {len(files)} source images...")
            for img in files:
                try:
                    os.remove(img)
                except OSError as e:
                    print(f"[-] Failed to delete {img}: {e}")

    print("\n[+] All tasks completed.")

if __name__ == "__main__":
    main()