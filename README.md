# YouTube Video Clipper with Playlist Support

## Project Overview
This is a desktop application built with Python that allows users to download YouTube videos (both single videos and playlists) and create clips from them. The app features a modern graphical user interface and supports high-quality video downloads with flexible clipping capabilities.

## Features
- 🎥 Download single YouTube videos
- 📋 Download entire YouTube playlists
- ✂️ Create multiple clips from videos
- 🎬 Built-in video player
- 📁 Organized file management (separate folders for downloads and clips)
- 🎯 Progress tracking for downloads
- 🔄 Support for high-quality video formats
- 🎵 Automatic audio extraction and merging

## Project Structure
```
yt-clip-notes/
├── main_app_playlistSupport.py    # Main application file with playlist support
├── main_app.py                    # Previous version without playlist support
├── main_ffmpeg.py                 # FFmpeg utility functions
├── main.py                        # Entry point
├── pyproject.toml                 # Project dependencies and configuration
├── README.md                      # Project documentation
├── test.py                        # Test file
├── uv.lock                        # Lock file for dependencies
├── clips/                         # Directory for storing video clips
└── downloads/                     # Directory for storing downloaded videos
```

## Requirements
- Python 3.x
- FFmpeg (for video processing)
- Required Python packages:
  - PySide6 (for GUI)
  - yt-dlp (for video downloads)
  - Other dependencies listed in pyproject.toml

## Installation
1. Clone the repository
2. Install FFmpeg if not already installed
3. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run the application:
   ```bash
   python main.py
   ```

## Usage
1. **Single Video Download**
   - Paste a YouTube URL
   - Click "Download Video"
   - Wait for download to complete

2. **Playlist Download**
   - Check "This is a playlist URL"
   - Paste playlist URL
   - Click "Download Playlist"
   - Monitor progress in playlist manager

3. **Creating Clips**
   - Load a downloaded video
   - Use video player controls
   - Set clip start/end points
   - Save clips
   - Click "Start Clipping" to process all clips

## Configuration
- Downloads are saved in the `downloads` folder
- Clips are saved in the `clips` folder
- You can change these locations in the app settings

## Features in Detail
1. **Video Download**
   - Supports high-quality video downloads (up to 2160p)
   - Automatic format selection
   - Progress tracking
   - Error handling

2. **Playlist Management**
   - Download entire playlists
   - Track individual video progress
   - Load any downloaded video
   - Status tracking for each video

3. **Video Player**
   - Play/Pause controls
   - Time slider
   - Current/Total time display
   - Clip point markers

4. **Clipping Features**
   - Set multiple clip points
   - Delete unwanted clips
   - Batch process all clips
   - Option to clean up source videos

## Contributing
Feel free to submit issues and enhancement requests!

## License
[Your chosen license]

## Acknowledgments
- Built with PySide6
- Uses yt-dlp for downloads
- FFmpeg for video processing
