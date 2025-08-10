import yt_dlp
import os
import subprocess
import sys

def safe_val(val, default="-"):
    """Return a safe string value for printing."""
    return str(val) if val is not None else default

def safe_size(val):
    """Return size in MB safely."""
    if val:
        return f"{val / (1024*1024):.2f}"
    return "-"

def get_video_info(url):
    """Get video information and return info dict."""
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e:
        print(f"❌ Error fetching video info: {e}")
        return None

def find_best_formats(info):
    """
    Find the best video format (preferably ≥1920x1080) and best audio format.
    Returns tuple: (best_video_format, best_audio_format)
    """
    formats = info.get('formats', [])
    
    # Filter video formats (no audio)
    video_formats = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') == 'none']
    
    # Filter audio formats (no video)
    audio_formats = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
    
    # Find best video format
    best_video = None
    target_height = 1080
    
    # First try to find formats with height >= 1080
    high_res_videos = [f for f in video_formats if f.get('height', 0) >= target_height]
    
    if high_res_videos:
        # Sort by height (descending) and then by filesize (descending for quality)
        best_video = max(high_res_videos, key=lambda x: (
            x.get('height', 0),
            x.get('tbr', 0),  # total bitrate
            x.get('filesize', 0)
        ))
    else:
        # Fallback: get the highest resolution available
        if video_formats:
            best_video = max(video_formats, key=lambda x: (
                x.get('height', 0),
                x.get('tbr', 0)
            ))
    
    # Find best audio format
    best_audio = None
    if audio_formats:
        # Sort by audio bitrate and quality
        best_audio = max(audio_formats, key=lambda x: (
            x.get('abr', 0),  # audio bitrate
            x.get('filesize', 0)
        ))
    
    return best_video, best_audio

def display_selected_formats(video_fmt, audio_fmt):
    """Display information about selected formats."""
    print("\n🎯 SELECTED FORMATS:")
    print("="*80)
    
    if video_fmt:
        width = video_fmt.get('width', 'N/A')
        height = video_fmt.get('height', 'N/A')
        fps = video_fmt.get('fps', 'N/A')
        vcodec = video_fmt.get('vcodec', 'N/A')
        vbr = video_fmt.get('tbr', video_fmt.get('vbr', 'N/A'))
        size = safe_size(video_fmt.get('filesize'))
        
        print(f"📹 VIDEO: {video_fmt['format_id']}")
        print(f"   Resolution: {width}x{height}")
        print(f"   FPS: {fps}")
        print(f"   Codec: {vcodec}")
        print(f"   Bitrate: {vbr} kbps")
        print(f"   Size: {size} MB")
    
    if audio_fmt:
        acodec = audio_fmt.get('acodec', 'N/A')
        abr = audio_fmt.get('abr', 'N/A')
        size = safe_size(audio_fmt.get('filesize'))
        
        print(f"\n🔊 AUDIO: {audio_fmt['format_id']}")
        print(f"   Codec: {acodec}")
        print(f"   Bitrate: {abr} kbps")
        print(f"   Size: {size} MB")
    
    print("-"*80)

def download_separate_streams(url, video_fmt_id, audio_fmt_id, base_filename, output_dir="downloads"):
    """Download video and audio streams separately."""
    try:
        os.makedirs(output_dir, exist_ok=True)
        
        # Download video
        print("📥 Downloading video stream...")
        video_path = os.path.join(output_dir, f"{base_filename}_video")
        video_opts = {
            'format': video_fmt_id,
            'outtmpl': f"{video_path}.%(ext)s",
            'quiet': False
        }
        
        with yt_dlp.YoutubeDL(video_opts) as ydl:
            ydl.download([url])
        
        # Download audio
        print("📥 Downloading audio stream...")
        audio_path = os.path.join(output_dir, f"{base_filename}_audio")
        audio_opts = {
            'format': audio_fmt_id,
            'outtmpl': f"{audio_path}.%(ext)s",
            'quiet': False
        }
        
        with yt_dlp.YoutubeDL(audio_opts) as ydl:
            ydl.download([url])
        
        # Find the actual downloaded files (with extensions)
        video_file = None
        audio_file = None
        
        for file in os.listdir(output_dir):
            if file.startswith(f"{base_filename}_video"):
                video_file = os.path.join(output_dir, file)
            elif file.startswith(f"{base_filename}_audio"):
                audio_file = os.path.join(output_dir, file)
        
        return video_file, audio_file
        
    except Exception as e:
        print(f"❌ Download failed: {e}")
        return None, None

def merge_with_ffmpeg(video_file, audio_file, output_file):
    """Merge video and audio using FFmpeg."""
    try:
        print("🔧 Merging video and audio with FFmpeg...")
        
        # Check if FFmpeg is available
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("❌ FFmpeg not found. Please install FFmpeg first.")
            print("   Windows: Download from https://ffmpeg.org/download.html")
            print("   macOS: brew install ffmpeg")
            print("   Linux: sudo apt install ffmpeg (Ubuntu/Debian)")
            return False
        
        # FFmpeg command to merge video and audio
        cmd = [
            'ffmpeg',
            '-i', video_file,
            '-i', audio_file,
            '-c:v', 'copy',  # Copy video stream without re-encoding
            '-c:a', 'aac',   # Re-encode audio to AAC for better compatibility
            '-shortest',     # Match the shortest stream
            '-y',           # Overwrite output file if exists
            output_file
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"✅ Successfully merged! Output: {output_file}")
            
            # Clean up temporary files
            try:
                os.remove(video_file)
                os.remove(audio_file)
                print("🧹 Cleaned up temporary files.")
            except:
                print("⚠️ Could not remove temporary files.")
            
            return True
        else:
            print(f"❌ FFmpeg error: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"❌ Merge failed: {e}")
        return False

def main():
    # Example video URL - replace with your desired URL
    video_url = input("Enter YouTube URL: ").strip()
    if not video_url:
        video_url = "https://www.youtube.com/watch?v=yfeCCnOPqrA"  # Default
        print(f"Using default URL: {video_url}")
    
    # Get video information
    print("📡 Fetching video information...")
    info = get_video_info(video_url)
    if not info:
        return
    
    # Find best formats
    video_fmt, audio_fmt = find_best_formats(info)
    
    if not video_fmt or not audio_fmt:
        print("❌ Could not find suitable video or audio formats.")
        return
    
    # Display selected formats
    display_selected_formats(video_fmt, audio_fmt)
    
    # Ask for confirmation
    response = input("\nProceed with download? (y/n): ").lower()
    if response not in ['y', 'yes']:
        print("Download cancelled.")
        return
    
    # Get output filename
    title = info.get('title', 'video').replace('/', '_').replace('\\', '_')
    safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()
    output_filename = input(f"Enter filename (default: '{safe_title}'): ").strip()
    if not output_filename:
        output_filename = safe_title
    
    # Download separate streams
    video_file, audio_file = download_separate_streams(
        video_url, 
        video_fmt['format_id'], 
        audio_fmt['format_id'], 
        output_filename
    )
    
    if video_file and audio_file:
        # Merge with FFmpeg
        final_output = os.path.join("downloads", f"{output_filename}_final.mp4")
        success = merge_with_ffmpeg(video_file, audio_file, final_output)
        
        if success:
            print(f"\n🎉 Download complete! Final video saved as: {final_output}")
        else:
            print(f"\n⚠️ Download complete but merge failed.")
            print(f"Video file: {video_file}")
            print(f"Audio file: {audio_file}")
    else:
        print("❌ Download failed.")

if __name__ == "__main__":
    main()