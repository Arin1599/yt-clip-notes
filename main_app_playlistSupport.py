import sys
import os
import yt_dlp
import subprocess
from humanfriendly import format_timespan
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QFileDialog, QProgressBar, QMessageBox, QListWidget, QListWidgetItem, QSlider,
    QCheckBox, QSizePolicy, QComboBox, QGroupBox, QTextEdit, QTabWidget, QTableWidget, 
    QTableWidgetItem, QHeaderView, QAbstractItemView
)
from PySide6.QtCore import Qt, QUrl, QThread, Signal, QTimer, QMutex, QMutexLocker
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget


class PlaylistInfo:
    def __init__(self, title, url, video_id, duration=None, status="pending"):
        self.title = title
        self.url = url
        self.video_id = video_id
        self.duration = duration
        self.status = status  # pending, downloading, completed, error
        self.file_path = None
        self.progress = 0


class PlaylistDownloadThread(QThread):
    playlist_info = Signal(list)  # List of PlaylistInfo objects
    video_progress = Signal(str, float)  # video_id, progress
    video_completed = Signal(str, str)  # video_id, file_path
    video_error = Signal(str, str)  # video_id, error_message
    playlist_completed = Signal()
    error = Signal(str)

    def __init__(self, url, output_folder):
        super().__init__()
        self.url = url
        self.output_folder = output_folder
        self.playlist_videos = []
        self.mutex = QMutex()
        self.should_stop = False

    def stop_download(self):
        with QMutexLocker(self.mutex):
            self.should_stop = True

    def run(self):
        try:
            # Step 1: Extract playlist information
            playlist_info = self._extract_playlist_info()
            if not playlist_info:
                self.error.emit("Failed to extract playlist information")
                return

            self.playlist_videos = playlist_info
            self.playlist_info.emit(playlist_info)

            # Step 2: Download videos one by one
            for video_info in playlist_info:
                with QMutexLocker(self.mutex):
                    if self.should_stop:
                        return

                try:
                    self._download_single_video(video_info)
                except Exception as e:
                    self.video_error.emit(video_info.video_id, str(e))
                    continue

            self.playlist_completed.emit()

        except Exception as e:
            self.error.emit(f"Playlist download error: {str(e)}")

    def _extract_playlist_info(self):
        """Extract playlist information without downloading."""
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,  # Only extract metadata
                'playlistend': 100,  # Limit to first 100 videos to prevent overwhelming
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                
                if 'entries' not in info:
                    return None

                playlist_videos = []
                for entry in info['entries']:
                    if entry is None:
                        continue
                    
                    video_info = PlaylistInfo(
                        title=entry.get('title', 'Unknown Title'),
                        url=entry.get('url', f"https://www.youtube.com/watch?v={entry.get('id', '')}"),
                        video_id=entry.get('id', ''),
                        duration=entry.get('duration')
                    )
                    playlist_videos.append(video_info)

                return playlist_videos

        except Exception as e:
            print(f"Error extracting playlist info: {e}")
            return None

    def _download_single_video(self, video_info):
        """Download a single video with enhanced quality."""
        try:
            # Create safe filename
            safe_title = "".join(c for c in video_info.title if c.isalnum() or c in (' ', '-', '_', '.')).strip()
            safe_title = safe_title[:50]  # Limit length
            output_path = os.path.join(self.output_folder, f"{safe_title}.mp4")
            
            video_info.status = "downloading"

            # Try enhanced download first
            success = self._enhanced_download(video_info, output_path)
            
            if not success:
                # Fallback to simple download
                self._simple_download(video_info, output_path)

            if os.path.exists(output_path):
                video_info.status = "completed"
                video_info.file_path = output_path
                self.video_completed.emit(video_info.video_id, output_path)
            else:
                raise Exception("Download completed but file not found")

        except Exception as e:
            video_info.status = "error"
            raise e

    def _enhanced_download(self, video_info, output_path):
        """Try enhanced download with separate audio/video streams."""
        try:
            # Get video information
            info = self._get_video_info(video_info.url)
            if not info:
                return False

            video_fmt, audio_fmt = self._find_best_formats(info)
            if not video_fmt or not audio_fmt:
                return False

            # Download streams
            base_name = os.path.splitext(output_path)[0]
            video_temp = f"{base_name}_temp_video"
            audio_temp = f"{base_name}_temp_audio"

            # Download video and audio
            video_file = self._download_stream(video_info, video_fmt['format_id'], video_temp, "video")
            audio_file = self._download_stream(video_info, audio_fmt['format_id'], audio_temp, "audio")

            # Merge streams
            return self._merge_streams(video_file, audio_file, output_path)

        except Exception:
            return False

    def _simple_download(self, video_info, output_path):
        """Simple download fallback."""
        ydl_opts = {
            'outtmpl': output_path,
            'format': 'best[ext=mp4]/best',
            'progress_hooks': [lambda d: self._progress_hook(d, video_info.video_id)],
            'quiet': True,
            'no_warnings': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_info.url])

    def _get_video_info(self, url):
        """Get video information."""
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception:
            return None

    def _find_best_formats(self, info):
        """Find best video and audio formats."""
        formats = info.get('formats', [])
        
        video_formats = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') == 'none']
        audio_formats = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
        
        best_video = None
        high_res_videos = [f for f in video_formats if f.get('height', 0) >= 1080]
        
        if high_res_videos:
            best_video = max(high_res_videos, key=lambda x: (x.get('height', 0), x.get('tbr', 0)))
        elif video_formats:
            best_video = max(video_formats, key=lambda x: (x.get('height', 0), x.get('tbr', 0)))
        
        best_audio = None
        if audio_formats:
            best_audio = max(audio_formats, key=lambda x: x.get('abr', 0))
        
        return best_video, best_audio

    def _download_stream(self, video_info, format_id, temp_path, stream_type):
        """Download a specific stream."""
        ydl_opts = {
            'format': format_id,
            'outtmpl': f"{temp_path}.%(ext)s",
            'progress_hooks': [lambda d: self._progress_hook(d, video_info.video_id, stream_type)],
            'quiet': True,
            'no_warnings': True
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_info.url])
        
        # Find downloaded file
        for file in os.listdir(os.path.dirname(temp_path)):
            if file.startswith(os.path.basename(temp_path)):
                return os.path.join(os.path.dirname(temp_path), file)
        
        raise Exception(f"Downloaded {stream_type} file not found")

    def _merge_streams(self, video_file, audio_file, output_path):
        """Merge video and audio streams."""
        try:
            # Check if FFmpeg is installed
            try:
                subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                print("FFmpeg not found. Please install FFmpeg.")
                return False

            # Set up the FFmpeg command
            cmd = [
                'ffmpeg', '-i', video_file, '-i', audio_file,
                '-c:v', 'copy', '-c:a', 'aac', '-shortest',
                '-y', output_path
            ]

            # Run FFmpeg with progress monitoring
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                bufsize=1
            )

            # Wait for the process to complete
            stdout, stderr = process.communicate()

            # Check if merge was successful
            if process.returncode == 0:
                try:
                    # Clean up temporary files
                    os.remove(video_file)
                    os.remove(audio_file)
                except Exception:
                    pass  # Ignore cleanup errors
                return True
            else:
                print(f"FFmpeg error: {stderr}")
                return False
        except Exception as e:
            print(f"Merge failed: {str(e)}")
            return False

    def _progress_hook(self, d, video_id, stream_type=None):
        """Progress hook for downloads."""
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            if total:
                downloaded = d.get('downloaded_bytes', 0)
                percent = (downloaded / total) * 100
                
                if stream_type == "video":
                    percent = percent * 0.6
                elif stream_type == "audio":
                    percent = 60 + (percent * 0.3)
                elif stream_type is None:
                    percent = percent * 0.9
                
                self.video_progress.emit(video_id, percent)


class SingleVideoDownloadThread(QThread):
    progress = Signal(float)
    status_update = Signal(str)
    done = Signal(str)
    error = Signal(str)

    def __init__(self, url, output_path):
        super().__init__()
        self.url = url
        self.output_path = output_path
        self.current_phase = ""

    def run(self):
        try:
            output_dir = os.path.dirname(self.output_path)
            os.makedirs(output_dir, exist_ok=True)
            
            if os.path.exists(self.output_path):
                try:
                    os.remove(self.output_path)
                except Exception:
                    pass

            self.status_update.emit("📡 Analyzing video formats...")
            info = self._get_video_info()
            if not info:
                raise Exception("Failed to get video information")

            video_fmt, audio_fmt = self._find_best_formats(info)
            if not video_fmt or not audio_fmt:
                self.status_update.emit("⚠️ Using fallback download method...")
                self._download_single_format()
                return

            base_name = os.path.splitext(self.output_path)[0]
            video_temp = f"{base_name}_temp_video"
            audio_temp = f"{base_name}_temp_audio"

            self.status_update.emit("📹 Downloading video stream...")
            self.current_phase = "video"
            video_file = self._download_stream(video_fmt['format_id'], video_temp)
            
            self.status_update.emit("🔊 Downloading audio stream...")
            self.current_phase = "audio"
            audio_file = self._download_stream(audio_fmt['format_id'], audio_temp)

            self.status_update.emit("🔧 Merging video and audio...")
            success = self._merge_streams(video_file, audio_file)
            
            if success:
                self.status_update.emit("✅ Download complete!")
                self.done.emit(self.output_path)
            else:
                raise Exception("Failed to merge video and audio streams")

        except Exception as e:
            self.error.emit(f"Download error: {str(e)}")

    def _get_video_info(self):
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
                return ydl.extract_info(self.url, download=False)
        except Exception:
            return None

    def _find_best_formats(self, info):
        formats = info.get('formats', [])
        
        # First try to find a format with both video and audio
        combined_formats = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') != 'none']
        if combined_formats:
            best_combined = max(combined_formats, key=lambda x: (
                x.get('height', 0), x.get('tbr', 0), x.get('filesize', 0)
            ))
            return best_combined, None

        # If no combined format, get separate video and audio
        video_formats = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') == 'none']
        audio_formats = [f for f in formats if f.get('acodec') != 'none']  # Allow any audio format
        
        best_video = None
        high_res_videos = [f for f in video_formats if f.get('height', 0) >= 1080]
        
        if high_res_videos:
            best_video = max(high_res_videos, key=lambda x: (
                x.get('height', 0), x.get('tbr', 0), x.get('filesize', 0)
            ))
        elif video_formats:
            best_video = max(video_formats, key=lambda x: (x.get('height', 0), x.get('tbr', 0)))
        
        best_audio = None
        if audio_formats:
            # Sort by audio bitrate and prefer m4a container
            best_audio = max(audio_formats, key=lambda x: (
                x.get('ext', '') == 'm4a',  # Prefer m4a format
                x.get('abr', 0),
                x.get('filesize', 0)
            ))
        
        return best_video, best_audio

    def _download_stream(self, format_id, temp_path):
        ydl_opts = {
            'format': format_id,
            'outtmpl': f"{temp_path}.%(ext)s",
            'progress_hooks': [self._progress_hook],
            'quiet': True,
            'no_warnings': True
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([self.url])
        
        for file in os.listdir(os.path.dirname(temp_path)):
            if file.startswith(os.path.basename(temp_path)):
                return os.path.join(os.path.dirname(temp_path), file)
        
        raise Exception(f"Downloaded {self.current_phase} file not found")

    def _merge_streams(self, video_file, audio_file):
        try:
            # First check if FFmpeg is installed
            try:
                subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                raise Exception("FFmpeg not found. Please install FFmpeg.")

            # Set up the FFmpeg command
            cmd = [
                'ffmpeg', '-i', video_file, '-i', audio_file,
                '-c:v', 'copy', '-c:a', 'aac', '-shortest',
                '-y', self.output_path
            ]

            # Update status
            self.status_update.emit("🔄 Merging video and audio streams...")
            self.progress.emit(90)  # Show progress at 90%

            # Run FFmpeg with progress monitoring
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                bufsize=1
            )

            # Wait for the process to complete while allowing GUI updates
            stdout, stderr = process.communicate()

            # Check if merge was successful
            if process.returncode == 0:
                try:
                    # Clean up temporary files
                    os.remove(video_file)
                    os.remove(audio_file)
                except Exception:
                    pass  # Ignore cleanup errors
                
                self.status_update.emit("✅ Merge completed successfully!")
                self.progress.emit(100)
                return True
            else:
                error_msg = f"FFmpeg error: {stderr}"
                self.status_update.emit("❌ Merge failed!")
                raise Exception(error_msg)

        except Exception as e:
            self.status_update.emit(f"❌ Merge failed: {str(e)}")
            raise Exception(f"Merge failed: {str(e)}")

    def _download_single_format(self):
        ydl_opts = {
            'outtmpl': self.output_path,
            'format': 'bestvideo[ext=mp4][height<=2160]+bestaudio[ext=m4a]/best[ext=mp4]/bestaudio[ext=m4a]/best',
            'progress_hooks': [self._progress_hook],
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4'
            }, {
                'key': 'FFmpegMetadata',
                'add_metadata': True,
            }],
            'prefer_ffmpeg': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([self.url])
        
        if os.path.exists(self.output_path):
            self.done.emit(self.output_path)
        else:
            raise Exception("Download completed but file not found")

    def _progress_hook(self, d):
        if d['status'] == 'downloading':
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            
            if total and total > 0:  # If we have a valid total
                percent = (downloaded / total) * 100
            else:
                # If no total available, base progress on downloaded megabytes
                # Assume most videos are under 2GB, so scale accordingly
                percent = min(90, (downloaded / (1024 * 1024)) / 20)
            
            if self.current_phase == "video":
                adjusted_percent = percent * 0.6
            elif self.current_phase == "audio":
                adjusted_percent = 60 + (percent * 0.3)
            else:
                adjusted_percent = percent * 0.9
            
            self.progress.emit(adjusted_percent)
        elif d['status'] == 'finished':
            if self.current_phase == "video":
                self.progress.emit(60)
            elif self.current_phase == "audio":
                self.progress.emit(90)
            else:
                self.progress.emit(90)


class ClippingThread(QThread):
    progress = Signal(float)
    done = Signal()
    error = Signal(str)

    def __init__(self, video_path, timestamps, output_folder):
        super().__init__()
        self.video_path = video_path
        self.timestamps = timestamps
        self.output_folder = output_folder

    def run(self):
        try:
            try:
                subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                raise Exception("FFmpeg is not installed or not found in system PATH")

            os.makedirs(self.output_folder, exist_ok=True)

            # Get video name without extension
            video_name = os.path.splitext(os.path.basename(self.video_path))[0]
            # Clean video name to ensure valid filename
            safe_video_name = "".join(c for c in video_name if c.isalnum() or c in (' ', '-', '_')).strip()

            clips_config = [
                {
                    "start_time": str(start),
                    "end_time": str(end),
                    "name":f"{safe_video_name}_clip_{i+1}_{format_timespan(start).replace(':', '_')}-{format_timespan(end).replace(':', '_')}"
                }
                for i, (start, end) in enumerate(self.timestamps)
            ]

            total = len(clips_config)
            for i, clip_config in enumerate(clips_config, 1):
                start_time = float(clip_config['start_time'])
                end_time = float(clip_config['end_time'])
                clip_name = clip_config['name']
                output_path = os.path.join(self.output_folder, f"{clip_name}.mp4")
                
                duration = end_time - start_time

                cmd = [
                    'ffmpeg', '-ss', str(start_time), '-i', self.video_path, '-t', str(duration),
                    '-c:v', 'libx264', '-c:a', 'aac', '-preset', 'medium', '-crf', '23',
                    '-b:a', '128k', '-avoid_negative_ts', 'make_zero', '-movflags', '+faststart',
                    '-y', output_path
                ]

                try:
                    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                    stdout, stderr = process.communicate()
                    
                    if process.returncode != 0:
                        print(f"FFmpeg stderr: {stderr}")
                        raise Exception(f"FFmpeg error (return code {process.returncode}): {stderr}")

                    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                        raise Exception(f"Output file was not created or is empty: {output_path}")

                    self.progress.emit(i / total * 100)
                    
                except Exception as e:
                    raise Exception(f"FFmpeg processing error for clip {i}: {str(e)}")

            self.done.emit()
        except Exception as e:
            self.error.emit(str(e))


class YouTubeClipper(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🎥 YouTube Video/Playlist Clipper with Enhanced Download")
        self.setGeometry(200, 100, 1100, 800)

        # Set up folders for downloads and clips
        self.downloads_folder = os.path.join(os.getcwd(), "downloads")
        self.clips_folder = os.path.join(os.getcwd(), "clips")
        # Create folders if they don't exist
        os.makedirs(self.downloads_folder, exist_ok=True)
        os.makedirs(self.clips_folder, exist_ok=True)

        self.timestamps = []
        self.current_start = None
        self.video_path = None
        self.clean_video = True
        self.playlist_videos = {}  # video_id -> PlaylistInfo
        self.current_playlist_thread = None

        self.init_ui()
        self._check_and_load_existing_video()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        # URL Input Section
        url_group = QGroupBox("📺 Video/Playlist Input")
        url_layout = QVBoxLayout(url_group)
        
        url_input_layout = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste YouTube URL here (video or playlist)...")
        
        self.is_playlist_checkbox = QCheckBox("This is a playlist URL")
        self.is_playlist_checkbox.stateChanged.connect(self.toggle_playlist_mode)
        
        url_input_layout.addWidget(self.url_input)
        url_input_layout.addWidget(self.is_playlist_checkbox)
        
        download_layout = QHBoxLayout()
        self.download_btn = QPushButton("⬇ Download Video")
        self.download_btn.clicked.connect(self.download_content)
        self.stop_btn = QPushButton("🛑 Stop Download")
        self.stop_btn.clicked.connect(self.stop_download)
        self.stop_btn.setEnabled(False)
        self.load_local_btn = QPushButton("📂 Load Local Video")
        self.load_local_btn.clicked.connect(self.browse_local_video)
        
        download_layout.addWidget(self.download_btn)
        download_layout.addWidget(self.stop_btn)
        download_layout.addWidget(self.load_local_btn)
        
        url_layout.addLayout(url_input_layout)
        url_layout.addLayout(download_layout)
        main_layout.addWidget(url_group)

        # Status and Output Folder
        info_layout = QHBoxLayout()
        self.status_label = QLabel("Ready to download...")
        self.status_label.setStyleSheet("color: #00c853; font-style: italic;")
        
        folder_layout = QHBoxLayout()
        self.folder_label = QLabel(f"Downloads: {self.downloads_folder}\nClips: {self.clips_folder}")
        choose_btn = QPushButton("📂 Choose Folders")
        choose_btn.clicked.connect(self.choose_folder)
        self.cleanup_checkbox = QCheckBox("Delete source video after clipping")
        self.cleanup_checkbox.setChecked(True)
        self.cleanup_checkbox.stateChanged.connect(self.toggle_cleanup)
        
        folder_layout.addWidget(self.folder_label)
        folder_layout.addWidget(choose_btn)
        folder_layout.addWidget(self.cleanup_checkbox)
        
        info_layout.addWidget(self.status_label)
        info_layout.addStretch()
        info_layout.addLayout(folder_layout)
        main_layout.addLayout(info_layout)

        # Progress Bar
        self.progress_bar = QProgressBar()
        main_layout.addWidget(self.progress_bar)

        # Main Content Area with Tabs
        self.tab_widget = QTabWidget()
        
        # Video Player Tab
        self.player_tab = QWidget()
        self.init_player_tab()
        self.tab_widget.addTab(self.player_tab, "🎬 Video Player & Clipping")
        
        # Playlist Tab
        self.playlist_tab = QWidget()
        self.init_playlist_tab()
        self.tab_widget.addTab(self.playlist_tab, "📋 Playlist Manager")
        
        main_layout.addWidget(self.tab_widget)

        self.apply_styles()

    def init_player_tab(self):
        layout = QVBoxLayout(self.player_tab)

        # Video Player
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)

        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(640, 360)
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.media_player.setVideoOutput(self.video_widget)
        layout.addWidget(self.video_widget)

        # Controls
        controls_layout = QHBoxLayout()
        play_btn = QPushButton("▶ Play/Pause")
        play_btn.clicked.connect(self.toggle_play)
        
        time_layout = QVBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderMoved.connect(self.set_position)
        self.media_player.positionChanged.connect(self.update_slider)
        self.media_player.durationChanged.connect(self.set_slider_range)
        
        time_info_layout = QHBoxLayout()
        self.current_time_label = QLabel("00:00:00")
        self.current_time_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.total_time_label = QLabel("00:00:00")
        self.total_time_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        time_info_layout.addWidget(self.current_time_label)
        time_info_layout.addStretch()
        time_info_layout.addWidget(self.total_time_label)
        
        time_layout.addWidget(self.slider)
        time_layout.addLayout(time_info_layout)
        
        controls_layout.addWidget(play_btn)
        controls_layout.addLayout(time_layout)
        layout.addLayout(controls_layout)

        # Timestamp Controls
        ts_layout = QHBoxLayout()
        
        # Start time controls
        start_group = QHBoxLayout()
        self.start_time_input = QLineEdit()
        self.start_time_input.setPlaceholderText("HH:MM:SS")
        self.start_time_input.setMaximumWidth(100)
        set_start_btn = QPushButton("⏱ Set Start")
        set_start_btn.clicked.connect(self.set_start_time)
        start_group.addWidget(QLabel("Start:"))
        start_group.addWidget(self.start_time_input)
        start_group.addWidget(set_start_btn)
        
        # End time controls
        end_group = QHBoxLayout()
        self.end_time_input = QLineEdit()
        self.end_time_input.setPlaceholderText("HH:MM:SS")
        self.end_time_input.setMaximumWidth(100)
        set_end_btn = QPushButton("📌 Set End & Save Clip")
        set_end_btn.clicked.connect(self.set_end_time)
        end_group.addWidget(QLabel("End:"))
        end_group.addWidget(self.end_time_input)
        end_group.addWidget(set_end_btn)
        
        ts_layout.addLayout(start_group)
        ts_layout.addLayout(end_group)
        layout.addLayout(ts_layout)

        # Clip List
        clip_layout = QVBoxLayout()
        clip_header_layout = QHBoxLayout()
        clip_label = QLabel("Saved Clips:")
        clip_label.setStyleSheet("font-weight: bold; font-size: 16px;")
        delete_btn = QPushButton("🗑️ Delete Selected")
        delete_btn.clicked.connect(self.delete_selected_clip)
        clip_header_layout.addWidget(clip_label)
        clip_header_layout.addStretch()
        clip_header_layout.addWidget(delete_btn)
        
        self.clip_list = QListWidget()
        clip_layout.addLayout(clip_header_layout)
        clip_layout.addWidget(self.clip_list)
        layout.addLayout(clip_layout)

        # Start Clipping
        start_btn = QPushButton("🚀 Start Clipping All")
        start_btn.clicked.connect(self.start_clipping)
        layout.addWidget(start_btn)

    def init_playlist_tab(self):
        layout = QVBoxLayout(self.playlist_tab)

        # Playlist info
        info_label = QLabel("📋 Playlist Videos")
        info_label.setStyleSheet("font-weight: bold; font-size: 16px;")
        layout.addWidget(info_label)

        # Playlist table
        self.playlist_table = QTableWidget()
        self.playlist_table.setColumnCount(5)
        self.playlist_table.setHorizontalHeaderLabels(["Title", "Duration", "Status", "Progress", "Actions"])
        
        header = self.playlist_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.resizeSection(3, 100)
        
        self.playlist_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.playlist_table)

        # Playlist controls
        controls_layout = QHBoxLayout()
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh_playlist)
        load_selected_btn = QPushButton("📺 Load Selected Video")
        load_selected_btn.clicked.connect(self.load_selected_playlist_video)
        
        controls_layout.addWidget(refresh_btn)
        controls_layout.addWidget(load_selected_btn)
        controls_layout.addStretch()
        layout.addLayout(controls_layout)

    def apply_styles(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #202020;
                color: white;
                font-size: 14px;
            }
            QPushButton {
                background-color: #444;
                padding: 8px;
                border-radius: 4px;
                border: none;
            }
            QPushButton:hover {
                background-color: #555;
            }
            QPushButton:disabled {
                background-color: #333;
                color: #666;
            }
            QLineEdit {
                background-color: #333;
                border: 1px solid #555;
                padding: 6px;
                border-radius: 4px;
            }
            QCheckBox {
                spacing: 5px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
            QCheckBox::indicator:unchecked {
                background-color: #333;
                border: 2px solid #555;
                border-radius: 3px;
            }
            QCheckBox::indicator:checked {
                background-color: #00c853;
                border: 2px solid #00c853;
                border-radius: 3px;
            }
            QGroupBox {
                font-weight: bold;
                border: 2px solid #444;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
            QTabWidget::pane {
                border: 1px solid #444;
                background-color: #202020;
            }
            QTabBar::tab {
                background-color: #333;
                padding: 8px 16px;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background-color: #555;
                border-bottom: 2px solid #00c853;
            }
            QSlider::groove:horizontal {
                background: #444;
                height: 8px;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #00c853;
                width: 16px;
                height: 16px;
                border-radius: 8px;
                margin: -4px 0;
            }
            QListWidget {
                background-color: #2b2b2b;
                border: 1px solid #444;
                border-radius: 4px;
                selection-background-color: #404040;
            }
            QTableWidget {
                background-color: #2b2b2b;
                alternate-background-color: #333;
                gridline-color: #444;
                border: 1px solid #444;
                border-radius: 4px;
            }
            QTableWidget::item {
                padding: 8px;
                border-bottom: 1px solid #444;
            }
            QTableWidget::item:selected {
                background-color: #404040;
            }
            QHeaderView::section {
                background-color: #333;
                padding: 8px;
                border: 1px solid #444;
                font-weight: bold;
            }
            QProgressBar {
                border: 1px solid #444;
                border-radius: 4px;
                text-align: center;
                background-color: #333;
            }
            QProgressBar::chunk {
                background-color: #00c853;
                border-radius: 3px;
            }
        """)

    def toggle_playlist_mode(self, state):
        """Toggle between single video and playlist mode."""
        if state == Qt.CheckState.Checked.value:
            self.download_btn.setText("⬇ Download Playlist")
            self.tab_widget.setCurrentIndex(1)  # Switch to playlist tab
        else:
            self.download_btn.setText("⬇ Download Video")
            self.tab_widget.setCurrentIndex(0)  # Switch to player tab

    def choose_folder(self):
        folder_type = QMessageBox.question(
            self, 
            "Choose Folder Type",
            "Which folder do you want to change?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        
        if folder_type == QMessageBox.StandardButton.Yes:
            title = "Select Downloads Folder"
            folder = QFileDialog.getExistingDirectory(self, title)
            if folder:
                self.downloads_folder = folder
        else:
            title = "Select Clips Folder"
            folder = QFileDialog.getExistingDirectory(self, title)
            if folder:
                self.clips_folder = folder
        
        self.folder_label.setText(f"Downloads: {self.downloads_folder}\nClips: {self.clips_folder}")

    def download_content(self):
        """Download video or playlist based on checkbox state."""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Error", "Please enter a YouTube URL")
            return

        self.progress_bar.setValue(0)
        self.download_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        if self.is_playlist_checkbox.isChecked():
            self.download_playlist(url)
        else:
            self.download_single_video(url)

    def download_single_video(self, url):
        """Download a single video."""
        self.status_label.setText("Starting video download...")
        
        self.download_thread = SingleVideoDownloadThread(url, os.path.join(self.downloads_folder, "video.mp4"))
        self.download_thread.progress.connect(self.progress_bar.setValue)
        self.download_thread.status_update.connect(self.status_label.setText)
        self.download_thread.done.connect(self.on_single_video_downloaded)
        self.download_thread.error.connect(self.handle_download_error)
        self.download_thread.start()

    def download_playlist(self, url):
        """Download entire playlist."""
        self.status_label.setText("Extracting playlist information...")
        
        # Clear previous playlist data
        self.playlist_videos.clear()
        self.playlist_table.setRowCount(0)
        
        self.current_playlist_thread = PlaylistDownloadThread(url, self.downloads_folder)
        self.current_playlist_thread.playlist_info.connect(self.on_playlist_info_received)
        self.current_playlist_thread.video_progress.connect(self.on_video_progress)
        self.current_playlist_thread.video_completed.connect(self.on_video_completed)
        self.current_playlist_thread.video_error.connect(self.on_video_error)
        self.current_playlist_thread.playlist_completed.connect(self.on_playlist_completed)
        self.current_playlist_thread.error.connect(self.handle_download_error)
        self.current_playlist_thread.start()

    def stop_download(self):
        """Stop current download."""
        if hasattr(self, 'download_thread') and self.download_thread.isRunning():
            self.download_thread.terminate()
            self.download_thread.wait()
        
        if self.current_playlist_thread and self.current_playlist_thread.isRunning():
            self.current_playlist_thread.stop_download()
            self.current_playlist_thread.terminate()
            self.current_playlist_thread.wait()
        
        self.download_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Download stopped")

    def on_playlist_info_received(self, playlist_videos):
        """Handle playlist information received."""
        try:
            self.playlist_videos.clear()
            self.playlist_table.setRowCount(len(playlist_videos))
            
            for i, video_info in enumerate(playlist_videos):
                self.playlist_videos[video_info.video_id] = video_info
                
                # Title
                title_item = QTableWidgetItem(video_info.title[:60] + "..." if len(video_info.title) > 60 else video_info.title)
                title_item.setToolTip(video_info.title)
                self.playlist_table.setItem(i, 0, title_item)
                
                # Duration
                duration_text = format_timespan(video_info.duration) if video_info.duration else "Unknown"
                self.playlist_table.setItem(i, 1, QTableWidgetItem(duration_text))
                
                # Status
                status_item = QTableWidgetItem("Pending")
                status_item.setData(Qt.ItemDataRole.UserRole, video_info.video_id)
                self.playlist_table.setItem(i, 2, status_item)
                
                # Progress
                progress_item = QTableWidgetItem("0%")
                self.playlist_table.setItem(i, 3, progress_item)
                
                # Actions
                load_btn = QPushButton("📺 Load")
                load_btn.clicked.connect(lambda checked, vid_id=video_info.video_id: self.load_playlist_video(vid_id))
                load_btn.setEnabled(False)  # Enable only when downloaded
                self.playlist_table.setCellWidget(i, 4, load_btn)
            
            self.status_label.setText(f"Found {len(playlist_videos)} videos. Starting downloads...")
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error processing playlist info: {str(e)}")

    def on_video_progress(self, video_id, progress):
        """Update video download progress."""
        try:
            if video_id in self.playlist_videos:
                self.playlist_videos[video_id].progress = progress
                
                # Find the row for this video
                for row in range(self.playlist_table.rowCount()):
                    status_item = self.playlist_table.item(row, 2)
                    if status_item and status_item.data(Qt.ItemDataRole.UserRole) == video_id:
                        # Update progress
                        progress_item = self.playlist_table.item(row, 3)
                        if progress_item:
                            progress_item.setText(f"{progress:.1f}%")
                        
                        # Update status
                        status_item.setText("Downloading")
                        break
        except Exception as e:
            print(f"Error updating progress: {e}")

    def on_video_completed(self, video_id, file_path):
        """Handle video download completion."""
        try:
            if video_id in self.playlist_videos:
                self.playlist_videos[video_id].status = "completed"
                self.playlist_videos[video_id].file_path = file_path
                
                # Update table
                for row in range(self.playlist_table.rowCount()):
                    status_item = self.playlist_table.item(row, 2)
                    if status_item and status_item.data(Qt.ItemDataRole.UserRole) == video_id:
                        status_item.setText("✅ Complete")
                        
                        progress_item = self.playlist_table.item(row, 3)
                        if progress_item:
                            progress_item.setText("100%")
                        
                        # Enable load button
                        load_btn = self.playlist_table.cellWidget(row, 4)
                        if load_btn:
                            load_btn.setEnabled(True)
                        break
                
                # Update status
                completed_count = sum(1 for v in self.playlist_videos.values() if v.status == "completed")
                total_count = len(self.playlist_videos)
                self.status_label.setText(f"Downloaded {completed_count}/{total_count} videos")
                
        except Exception as e:
            print(f"Error handling video completion: {e}")

    def on_video_error(self, video_id, error_msg):
        """Handle video download error."""
        try:
            if video_id in self.playlist_videos:
                self.playlist_videos[video_id].status = "error"
                
                # Update table
                for row in range(self.playlist_table.rowCount()):
                    status_item = self.playlist_table.item(row, 2)
                    if status_item and status_item.data(Qt.ItemDataRole.UserRole) == video_id:
                        status_item.setText("❌ Error")
                        status_item.setToolTip(error_msg)
                        
                        progress_item = self.playlist_table.item(row, 3)
                        if progress_item:
                            progress_item.setText("Failed")
                        break
        except Exception as e:
            print(f"Error handling video error: {e}")

    def on_playlist_completed(self):
        """Handle playlist download completion."""
        self.download_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        
        completed_count = sum(1 for v in self.playlist_videos.values() if v.status == "completed")
        error_count = sum(1 for v in self.playlist_videos.values() if v.status == "error")
        total_count = len(self.playlist_videos)
        
        self.status_label.setText(f"✅ Playlist download complete! {completed_count} successful, {error_count} failed out of {total_count}")
        self.progress_bar.setValue(100)

    def on_single_video_downloaded(self, path):
        """Handle single video download completion."""
        try:
            self.download_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            
            # Get video info to rename the file
            title = "video"  # Default title if extraction fails
            try:
                # First try to get info from our downloaded file path
                title = os.path.splitext(os.path.basename(path))[0]
                if title == "video":  # If it's the default name, try getting from URL
                    with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                        info = ydl.extract_info(self.url_input.text(), download=False)
                        if info and info.get('title'):
                            title = info['title']
            except:
                pass  # Keep default title if extraction fails
                
            # Create safe filename
            safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_', '.')).strip()
            safe_title = safe_title[:100]  # Limit length
            new_path = os.path.join(self.downloads_folder, f"{safe_title}.mp4")
            
            # Rename the file
            if os.path.exists(path):
                if os.path.exists(new_path):
                    os.remove(new_path)  # Remove existing file if any
                os.rename(path, new_path)
                self.load_video(new_path)
                
        except Exception as e:
            QMessageBox.warning(self, "Warning", f"Could not rename video file: {str(e)}")
            self.load_video(path)  # Load original file if rename fails

    def handle_download_error(self, error_msg):
        """Handle download errors."""
        self.download_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Download failed")
        QMessageBox.critical(self, "Download Error", error_msg)

    def load_playlist_video(self, video_id):
        """Load a specific video from playlist."""
        if video_id in self.playlist_videos:
            video_info = self.playlist_videos[video_id]
            if video_info.status == "completed" and video_info.file_path:
                self.load_video(video_info.file_path)
                self.tab_widget.setCurrentIndex(0)  # Switch to player tab

    def load_selected_playlist_video(self):
        """Load currently selected video from playlist table."""
        current_row = self.playlist_table.currentRow()
        if current_row >= 0:
            status_item = self.playlist_table.item(current_row, 2)
            if status_item:
                video_id = status_item.data(Qt.ItemDataRole.UserRole)
                if video_id:
                    self.load_playlist_video(video_id)

    def refresh_playlist(self):
        """Refresh playlist table."""
        try:
            for row in range(self.playlist_table.rowCount()):
                status_item = self.playlist_table.item(row, 2)
                if status_item:
                    video_id = status_item.data(Qt.ItemDataRole.UserRole)
                    if video_id in self.playlist_videos:
                        video_info = self.playlist_videos[video_id]
                        
                        # Update status
                        if video_info.status == "completed":
                            status_item.setText("✅ Complete")
                        elif video_info.status == "downloading":
                            status_item.setText("Downloading")
                        elif video_info.status == "error":
                            status_item.setText("❌ Error")
                        else:
                            status_item.setText("Pending")
                        
                        # Update progress
                        progress_item = self.playlist_table.item(row, 3)
                        if progress_item:
                            if video_info.status == "completed":
                                progress_item.setText("100%")
                            elif video_info.status == "error":
                                progress_item.setText("Failed")
                            else:
                                progress_item.setText(f"{video_info.progress:.1f}%")
                        
                        # Update button
                        load_btn = self.playlist_table.cellWidget(row, 4)
                        if load_btn:
                            load_btn.setEnabled(video_info.status == "completed")
        except Exception as e:
            print(f"Error refreshing playlist: {e}")

    def load_video(self, path):
        """Load video into player."""
        try:
            if not os.path.exists(path):
                QMessageBox.critical(self, "Error", "Video file not found!")
                return
            
            # Clear previous timestamps
            self.timestamps.clear()
            self.clip_list.clear()
            self.current_start = None
            
            self.video_path = path
            self.media_player.setSource(QUrl.fromLocalFile(path))
            
            # Get video filename for display
            video_name = os.path.basename(path)
            self.status_label.setText(f"✅ Loaded: {video_name}")
            self.progress_bar.setValue(100)
            
            # Set up position update timer
            if hasattr(self, 'position_timer'):
                self.position_timer.stop()
            
            self.position_timer = QTimer(self)
            self.position_timer.setInterval(50)
            self.position_timer.timeout.connect(self.update_position)
            self.position_timer.start()
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load video: {str(e)}")

    def toggle_play(self):
        """Toggle play/pause."""
        try:
            if self.media_player.isPlaying():
                self.media_player.pause()
            else:
                self.media_player.play()
        except Exception as e:
            print(f"Error toggling play: {e}")

    def set_slider_range(self, duration):
        """Set slider range based on video duration."""
        try:
            self.slider.setRange(0, duration)
            total_seconds = duration / 1000
            self.total_time_label.setText(format_timespan(total_seconds))
        except Exception as e:
            print(f"Error setting slider range: {e}")

    def update_slider(self, position):
        """Update slider position."""
        try:
            self.slider.setValue(position)
            current_seconds = position / 1000
            self.current_time_label.setText(format_timespan(current_seconds))
        except Exception as e:
            print(f"Error updating slider: {e}")

    def set_position(self, position):
        """Set media player position."""
        try:
            self.media_player.setPosition(position)
            current_seconds = position / 1000
            self.current_time_label.setText(format_timespan(current_seconds))
        except Exception as e:
            print(f"Error setting position: {e}")

    def set_start_time(self):
        """Set clip start time."""
        try:
            # First check if there's manual input
            manual_time = self.start_time_input.text().strip()
            if manual_time:
                parsed_time = self.parse_timestamp(manual_time)
                if parsed_time is not None:
                    # Validate that the time is within video duration
                    if parsed_time > self.media_player.duration() / 1000:
                        QMessageBox.warning(self, "Error", "Start time exceeds video duration!")
                        return
                    self.current_start = parsed_time
                else:
                    return  # Error was already shown by parse_timestamp
            else:
                # Use current position if no manual input
                pos = self.media_player.position() / 1000
                self.current_start = pos
                self.start_time_input.setText(format_timespan(pos))
            
            QMessageBox.information(self, "Start Set", f"Start time set at {format_timespan(self.current_start)}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to set start time: {str(e)}")

    def set_end_time(self):
        """Set clip end time and save clip."""
        try:
            if self.current_start is None:
                QMessageBox.warning(self, "Error", "Set start time first!")
                return

            # Check for manual input
            manual_time = self.end_time_input.text().strip()
            if manual_time:
                end_pos = self.parse_timestamp(manual_time)
                if end_pos is None:
                    return  # Error was already shown by parse_timestamp
                # Validate that the time is within video duration
                if end_pos > self.media_player.duration() / 1000:
                    QMessageBox.warning(self, "Error", "End time exceeds video duration!")
                    return
            else:
                # Use current position if no manual input
                end_pos = self.media_player.position() / 1000
                self.end_time_input.setText(format_timespan(end_pos))
            
            if end_pos <= self.current_start:
                QMessageBox.warning(self, "Error", "End time must be greater than start time!")
                return
            
            self.timestamps.append((self.current_start, end_pos))
            clip_text = f"{len(self.timestamps)}. {format_timespan(self.current_start)} → {format_timespan(end_pos)} (Duration: {format_timespan(end_pos - self.current_start)})"
            self.clip_list.addItem(QListWidgetItem(clip_text))
            self.current_start = None
            
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to set end time: {str(e)}")

    def delete_selected_clip(self):
        """Delete selected clip from list."""
        try:
            current_row = self.clip_list.currentRow()
            if current_row == -1:
                QMessageBox.warning(self, "No Selection", "Please select a clip to delete")
                return
            
            reply = QMessageBox.question(
                self, "Confirm Deletion", f"Delete clip {current_row + 1}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                self.timestamps.pop(current_row)
                self.clip_list.takeItem(current_row)
                self.refresh_clip_list()
                
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to delete clip: {str(e)}")

    def refresh_clip_list(self):
        """Refresh clip list numbering."""
        try:
            self.clip_list.clear()
            for i, (start, end) in enumerate(self.timestamps):
                clip_text = f"{i+1}. {format_timespan(start)} → {format_timespan(end)} (Duration: {format_timespan(end - start)})"
                self.clip_list.addItem(QListWidgetItem(clip_text))
        except Exception as e:
            print(f"Error refreshing clip list: {e}")

    def start_clipping(self):
        """Start clipping process."""
        try:
            if not self.timestamps:
                QMessageBox.warning(self, "Error", "No clips added!")
                return
            if not self.video_path:
                QMessageBox.warning(self, "Error", "No video loaded!")
                return
            
            self.status_label.setText("🚀 Creating clips...")
            self.clip_thread = ClippingThread(self.video_path, self.timestamps, self.clips_folder)
            self.clip_thread.progress.connect(self.progress_bar.setValue)
            self.clip_thread.done.connect(self.clipping_finished)
            self.clip_thread.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
            self.clip_thread.start()
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to start clipping: {str(e)}")

    def clipping_finished(self):
        """Handle clipping completion."""
        try:
            self.status_label.setText("✅ All clips created successfully!")
            QMessageBox.information(self, "Done", "All clips created successfully!")
            
            if self.cleanup_checkbox.isChecked() and self.video_path and os.path.exists(self.video_path):
                try:
                    os.remove(self.video_path)
                    self.status_label.setText("✅ Clips created and source video cleaned up!")
                except Exception as e:
                    QMessageBox.warning(self, "Warning", f"Could not delete source video: {str(e)}")
                    
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Error in clipping completion: {str(e)}")

    def toggle_cleanup(self, state):
        """Toggle cleanup setting."""
        self.clean_video = bool(state)

    def parse_timestamp(self, timestamp_str):
        """Parse a timestamp string in HH:MM:SS format to seconds."""
        try:
            # Handle empty input
            if not timestamp_str.strip():
                return None

            # Split the timestamp into hours, minutes, and seconds
            parts = timestamp_str.split(':')
            
            if len(parts) == 3:  # HH:MM:SS
                hours, minutes, seconds = map(float, parts)
            elif len(parts) == 2:  # MM:SS
                hours = 0
                minutes, seconds = map(float, parts)
            elif len(parts) == 1:  # SS
                hours = 0
                minutes = 0
                seconds = float(parts[0])
            else:
                raise ValueError("Invalid time format")

            # Convert to seconds
            total_seconds = hours * 3600 + minutes * 60 + seconds
            
            # Validate the timestamp is not negative
            if total_seconds < 0:
                raise ValueError("Time cannot be negative")
                
            return total_seconds
            
        except Exception as e:
            QMessageBox.warning(None, "Invalid Time Format", 
                              "Please enter time in HH:MM:SS, MM:SS, or SS format")
            return None

    def update_timestamp_display(self):
        """Update the timestamp input fields with current video position."""
        if self.current_start is not None:
            self.start_time_input.setText(format_timespan(self.current_start))
        current_pos = self.media_player.position() / 1000
        self.end_time_input.setText(format_timespan(current_pos))

    def update_position(self):
        """Update position display during playback."""
        try:
            if self.media_player.isPlaying():
                position = self.media_player.position()
                self.slider.setValue(position)
                current_seconds = position / 1000
                self.current_time_label.setText(format_timespan(current_seconds))
                
                # Update end time input if no manual entry
                if not self.end_time_input.hasFocus():
                    self.end_time_input.setText(format_timespan(current_seconds))
        except Exception as e:
            print(f"Error updating position: {e}")

    def browse_local_video(self):
        """Browse and load a local video file."""
        file_dialog = QFileDialog()
        video_path, _ = file_dialog.getOpenFileName(
            self,
            "Select Video File",
            "",
            "Video Files (*.mp4 *.avi *.mkv *.mov *.wmv);;All Files (*.*)"
        )
        
        if video_path:
            self.load_video(video_path)
            
    def _check_and_load_existing_video(self):
        """Check for existing video and load it."""
        try:
            video_path = os.path.join(self.downloads_folder, "video.mp4")
            if os.path.exists(video_path):
                self.load_video(video_path)
        except Exception as e:
            print(f"Error checking existing video: {e}")

    def closeEvent(self, event):
        """Clean up resources on app close."""
        try:
            # Stop any running threads
            if hasattr(self, 'download_thread') and self.download_thread.isRunning():
                self.download_thread.terminate()
                self.download_thread.wait()
            
            if self.current_playlist_thread and self.current_playlist_thread.isRunning():
                self.current_playlist_thread.stop_download()
                self.current_playlist_thread.terminate()
                self.current_playlist_thread.wait()
            
            if hasattr(self, 'clip_thread') and self.clip_thread.isRunning():
                self.clip_thread.terminate()
                self.clip_thread.wait()
            
            # Clean up timers and media player
            if hasattr(self, 'position_timer'):
                self.position_timer.stop()
            if hasattr(self, 'media_player'):
                self.media_player.stop()
                
        except Exception as e:
            print(f"Error during cleanup: {e}")
        finally:
            event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = YouTubeClipper()
    window.show()
    sys.exit(app.exec())