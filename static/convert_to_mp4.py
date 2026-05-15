import os
import subprocess
from pathlib import Path

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

# Change this to your directory
DIRECTORY = r"C:/Users/Gajul/Coding Projects/Memories of Us/memories-of-us/static/media2"

# If True:
#   original .mov files will be deleted after successful conversion
DELETE_ORIGINAL = False

# Supported extensions
MOV_EXTENSIONS = {".mov", ".MOV"}

# -----------------------------------------------------------------------------
# Conversion Utility
# -----------------------------------------------------------------------------

def convert_mov_to_mp4(input_path: Path) -> bool:
    """
    Convert a MOV file to MP4 using ffmpeg.
    Uses fast remuxing first, then falls back to re-encoding.
    """

    output_path = input_path.with_suffix(".mp4")

    print(f"\nProcessing: {input_path.name}")

    # Skip if MP4 already exists
    if output_path.exists():
        print(f"Skipping (already exists): {output_path.name}")
        return True

    # -------------------------------------------------------------------------
    # Attempt Fast Remux
    # -------------------------------------------------------------------------

    remux_command = [
        "ffmpeg",
        "-y",

        "-i", str(input_path),

        # Copy video stream directly
        "-c:v", "copy",

        # Convert audio for compatibility
        "-c:a", "aac",
        "-b:a", "128k",

        # Optimize playback
        "-movflags", "+faststart",

        str(output_path),
    ]

    try:
        print("Attempting fast remux...")

        subprocess.run(
            remux_command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        print(f"Fast remux successful: {output_path.name}")

        # Delete original MOV after successful conversion
        input_path.unlink()

        print(f"Deleted original: {input_path.name}")
        return True

    except subprocess.CalledProcessError:
        print("Fast remux failed. Falling back to re-encoding...")

    # -------------------------------------------------------------------------
    # Fallback Re-Encode
    # -------------------------------------------------------------------------

    encode_command = [
        "ffmpeg",
        "-y",

        "-i", str(input_path),

        # Fast encoding
        "-c:v", "libx264",
        "-preset", "ultrafast",

        # Faster/lower quality
        "-crf", "28",

        # Browser compatibility
        "-pix_fmt", "yuv420p",

        # Audio
        "-c:a", "aac",
        "-b:a", "96k",

        # Streaming optimization
        "-movflags", "+faststart",

        str(output_path),
    ]

    try:
        subprocess.run(
            encode_command,
            check=True,
        )

        print(f"Re-encode successful: {output_path.name}")

        # Delete original MOV after successful conversion
        input_path.unlink()

        print(f"Deleted original: {input_path.name}")

        return True

    except subprocess.CalledProcessError as e:
        print(f"FAILED: {input_path.name}")
        print(e)

        if output_path.exists():
            output_path.unlink()

        return False


# -----------------------------------------------------------------------------
# Directory Walker
# -----------------------------------------------------------------------------

def process_directory(directory: str):

    base_path = Path(directory)

    if not base_path.exists():
        print(f"Directory does not exist: {directory}")
        return

    mov_files = []

    # Recursively find MOV files
    for file_path in base_path.rglob("*"):

        if (
            file_path.is_file() and
            file_path.suffix in MOV_EXTENSIONS
        ):
            mov_files.append(file_path)

    if not mov_files:
        print("No MOV files found.")
        return

    print(f"Found {len(mov_files)} MOV files.")

    success_count = 0

    for mov_file in mov_files:

        success = convert_mov_to_mp4(mov_file)

        if success:
            success_count += 1

    print("\n--------------------------------------------------")
    print(f"Completed.")
    print(f"Successful: {success_count}/{len(mov_files)}")
    print("--------------------------------------------------")


# -----------------------------------------------------------------------------
# Run
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    process_directory(DIRECTORY)