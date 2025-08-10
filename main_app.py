import sys
import os
import yt_dlp
import subprocess
from humanfriendly import format_timespan
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QFileDialog, QProgressBar, QMessageBox, QListWidget, QListWidgetItem, QSlider,
    QCheckBox, QSizePolicy
)
from PySide6.QtCore import Qt, QUrl, QThread, Signal, QTimer
from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget


class DownloadThread(QThread):
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
            # First, ensure the output directory exists
            output_dir = os.path.dirname(self.output_path)
            os.makedirs(output_dir, exist_ok=True)
            
            # Remove existing file if it exists
            if os.path.exists(self.output_path):
                try:
                    os.remove(self.output_path)
                except Exception:
                    pass

            # Step 1: Get video info and find best formats
            self.status_update.emit("📡 Analyzing video formats...")
            info = self._get_video_info()
            if not info:
                raise Exception("Failed to get video information")

            video_fmt, audio_fmt = self._find_best_formats(info)
            if not video_fmt or not audio_fmt:
                # Fallback to single format download
                self.status_update.emit("⚠️ Using fallback download method...")
                self._download_single_format()
                return

            # Step 2: Download video and audio separately
            base_name = os.path.splitext(self.output_path)[0]
            video_temp = f"{base_name}_temp_video"
            audio_temp = f"{base_name}_temp_audio"

            # Download video stream
            self.status_update.emit("📹 Downloading video stream...")
            self.current_phase = "video"
            video_file = self._download_stream(video_fmt['format_id'], video_temp)
            
            # Download audio stream
            self.status_update.emit("🔊 Downloading audio stream...")
            self.current_phase = "audio"
            audio_file = self._download_stream(audio_fmt['format_id'], audio_temp)

            # Step 3: Merge with FFmpeg
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
        """Get video information without downloading."""
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
                return ydl.extract_info(self.url, download=False)
        except Exception:
            return None

    def _find_best_formats(self, info):
        """Find the best video and audio formats."""
        formats = info.get('formats', [])
        
        # Filter video-only formats
        video_formats = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') == 'none']
        # Filter audio-only formats
        audio_formats = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
        
        # Find best video (preferably >= 1080p)
        best_video = None
        high_res_videos = [f for f in video_formats if f.get('height', 0) >= 1080]
        
        if high_res_videos:
            best_video = max(high_res_videos, key=lambda x: (
                x.get('height', 0),
                x.get('tbr', 0),
                x.get('filesize', 0)
            ))
        elif video_formats:
            best_video = max(video_formats, key=lambda x: (
                x.get('height', 0),
                x.get('tbr', 0)
            ))
        
        # Find best audio
        best_audio = None
        if audio_formats:
            best_audio = max(audio_formats, key=lambda x: (
                x.get('abr', 0),
                x.get('filesize', 0)
            ))
        
        return best_video, best_audio

    def _download_stream(self, format_id, temp_path):
        """Download a specific stream."""
        ydl_opts = {
            'format': format_id,
            'outtmpl': f"{temp_path}.%(ext)s",
            'progress_hooks': [self._progress_hook],
            'quiet': True,
            'no_warnings': True
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([self.url])
        
        # Find the downloaded file
        for file in os.listdir(os.path.dirname(temp_path)):
            if file.startswith(os.path.basename(temp_path)):
                return os.path.join(os.path.dirname(temp_path), file)
        
        raise Exception(f"Downloaded {self.current_phase} file not found")

    def _merge_streams(self, video_file, audio_file):
        """Merge video and audio using FFmpeg."""
        try:
            # Check FFmpeg availability
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise Exception("FFmpeg not found. Please install FFmpeg.")

        cmd = [
            'ffmpeg',
            '-i', video_file,
            '-i', audio_file,
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-shortest',
            '-y',
            self.output_path
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                # Clean up temporary files
                try:
                    os.remove(video_file)
                    os.remove(audio_file)
                except Exception:
                    pass
                return True
            else:
                raise Exception(f"FFmpeg error: {result.stderr}")
                
        except Exception as e:
            raise Exception(f"Merge failed: {str(e)}")

    def _download_single_format(self):
        """Fallback method using single format download."""
        ydl_opts = {
            'outtmpl': self.output_path,
            'format': 'bestvideo[ext=mp4][height<=2160]+bestaudio[ext=m4a]/best[ext=mp4][height<=2160]/bestvideo+bestaudio/best',
            'progress_hooks': [self._progress_hook],
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4'
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
        """Progress hook for yt-dlp."""
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            if total:
                downloaded = d.get('downloaded_bytes', 0)
                percent = (downloaded / total) * 100
                
                # Adjust progress based on current phase
                if self.current_phase == "video":
                    # Video download is 0-60% of total progress
                    adjusted_percent = percent * 0.6
                elif self.current_phase == "audio":
                    # Audio download is 60-90% of total progress
                    adjusted_percent = 60 + (percent * 0.3)
                else:
                    # Single format download
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
            # Check if ffmpeg is available
            try:
                subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                raise Exception("FFmpeg is not installed or not found in system PATH")

            # Ensure output folder exists
            os.makedirs(self.output_folder, exist_ok=True)

            # Convert timestamps to clip configs with meaningful names
            clips_config = [
                {
                    "start_time": str(start),
                    "end_time": str(end),
                    "name": f"clip_{i+1}_{format_timespan(start).replace(':', '_')}-{format_timespan(end).replace(':', '_')}"
                }
                for i, (start, end) in enumerate(self.timestamps)
            ]

            total = len(clips_config)
            for i, clip_config in enumerate(clips_config, 1):
                start_time = float(clip_config['start_time'])
                end_time = float(clip_config['end_time'])
                clip_name = clip_config['name']
                output_path = os.path.join(self.output_folder, f"{clip_name}.mp4")
                
                # Calculate duration for progress monitoring
                duration = end_time - start_time

                # Single FFmpeg command to extract both video and audio
                # This is much more reliable than splitting and recombining
                cmd = [
                    'ffmpeg',
                    '-ss', str(start_time),          # Seek to start time
                    '-i', self.video_path,           # Input file
                    '-t', str(duration),             # Duration to extract
                    '-c:v', 'libx264',              # Video codec
                    '-c:a', 'aac',                  # Audio codec
                    '-preset', 'medium',            # Encoding speed/quality balance
                    '-crf', '23',                   # Video quality (lower = better)
                    '-b:a', '128k',                 # Audio bitrate
                    '-avoid_negative_ts', 'make_zero',  # Handle timestamp issues
                    '-movflags', '+faststart',      # Optimize for web playback
                    '-y',                           # Overwrite output file if exists
                    output_path
                ]

                try:
                    # Run the FFmpeg command
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True
                    )
                    stdout, stderr = process.communicate()
                    
                    if process.returncode != 0:
                        # Print stderr for debugging
                        print(f"FFmpeg stderr: {stderr}")
                        raise Exception(f"FFmpeg error (return code {process.returncode}): {stderr}")

                    # Verify the output file was created and has content
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
        self.setWindowTitle("🎥 YouTube Video Clipper with Enhanced Download (FFmpeg)")
        self.setGeometry(200, 100, 900, 700)

        self.output_folder = os.getcwd()
        self.timestamps = []  # [(start_sec, end_sec)]
        self.current_start = None
        self.video_path = None
        self.clean_video = True  # Flag to control video cleanup

        main_layout = QVBoxLayout(self)

        # URL Input
        url_layout = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste YouTube URL here...")
        download_btn = QPushButton("⬇ Download & Load Video (Enhanced)")
        download_btn.clicked.connect(self.download_video)
        url_layout.addWidget(self.url_input)
        url_layout.addWidget(download_btn)
        main_layout.addLayout(url_layout)

        # Status label for download progress
        self.status_label = QLabel("Ready to download...")
        self.status_label.setStyleSheet("color: #00c853; font-style: italic;")
        main_layout.addWidget(self.status_label)

        # Output Folder
        folder_layout = QHBoxLayout()
        self.folder_label = QLabel(f"Output Folder: {self.output_folder}")
        choose_btn = QPushButton("📂 Choose Folder")
        choose_btn.clicked.connect(self.choose_folder)
        self.cleanup_checkbox = QCheckBox("Delete source video after clipping")
        self.cleanup_checkbox.setChecked(True)
        self.cleanup_checkbox.stateChanged.connect(self.toggle_cleanup)
        folder_layout.addWidget(self.folder_label)
        folder_layout.addWidget(choose_btn)
        folder_layout.addWidget(self.cleanup_checkbox)
        main_layout.addLayout(folder_layout)

        # Video Player
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)

        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(640, 360)  # 16:9 aspect ratio
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.media_player.setVideoOutput(self.video_widget)
        main_layout.addWidget(self.video_widget)

        # Controls
        controls_layout = QHBoxLayout()
        play_btn = QPushButton("▶ Play/Pause")
        play_btn.clicked.connect(self.toggle_play)
        
        # Time display layout
        time_layout = QVBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderMoved.connect(self.set_position)
        self.media_player.positionChanged.connect(self.update_slider)
        self.media_player.durationChanged.connect(self.set_slider_range)
        
        # Current time and total duration display
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
        main_layout.addLayout(controls_layout)

        # Timestamp Controls
        ts_layout = QHBoxLayout()
        set_start_btn = QPushButton("⏱ Set Start")
        set_start_btn.clicked.connect(self.set_start_time)
        set_end_btn = QPushButton("📌 Set End & Save Clip")
        set_end_btn.clicked.connect(self.set_end_time)
        ts_layout.addWidget(set_start_btn)
        ts_layout.addWidget(set_end_btn)
        main_layout.addLayout(ts_layout)

        # Clip List with delete functionality
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
        main_layout.addLayout(clip_layout)

        # Progress Bar
        self.progress_bar = QProgressBar()
        main_layout.addWidget(self.progress_bar)

        # Start Clipping
        start_btn = QPushButton("🚀 Start Clipping All")
        start_btn.clicked.connect(self.start_clipping)
        main_layout.addWidget(start_btn)

        # Styling
        self.setStyleSheet("""
            QWidget {
                background-color: #202020;
                color: white;
                font-size: 14px;
            }
            QPushButton {
                background-color: #444;
                padding: 6px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #555;
            }
            QLineEdit {
                background-color: #333;
                border: 1px solid #555;
                padding: 4px;
            }
            QLabel {
                font-weight: bold;
            }
            QSlider::groove:horizontal {
                background: #444;
                height: 8px;
            }
            QSlider::handle:horizontal {
                background: #00c853;
                width: 16px;
            }
            QListWidget {
                background-color: #2b2b2b;
                border: none;
                selection-background-color: #404040;
            }
            QLabel {
                color: #ffffff;
            }
        """)
        
        # Try to load existing video after UI is initialized
        self._check_and_load_existing_video()

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.output_folder = folder
            self.folder_label.setText(f"Output Folder: {self.output_folder}")

    def download_video(self):
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Error", "Please enter a YouTube URL")
            return
        
        self.progress_bar.setValue(0)
        self.status_label.setText("Starting download...")
        
        self.download_thread = DownloadThread(url, os.path.join(self.output_folder, "video.mp4"))
        self.download_thread.progress.connect(self.progress_bar.setValue)
        self.download_thread.status_update.connect(self.status_label.setText)
        self.download_thread.done.connect(self.load_video)
        self.download_thread.error.connect(self.handle_download_error)
        self.download_thread.start()

    def handle_download_error(self, error_msg):
        self.status_label.setText("Download failed")
        QMessageBox.critical(self, "Download Error", error_msg)

    def load_video(self, path):
        if not os.path.exists(path):
            QMessageBox.critical(self, "Error", "Video file not found!")
            return
        
        # Clear previous timestamps
        self.timestamps.clear()
        self.clip_list.clear()
        self.current_start = None
        
        self.video_path = path
        self.media_player.setSource(QUrl.fromLocalFile(path))
        self.status_label.setText("✅ Video loaded successfully!")
        self.progress_bar.setValue(100)
        
        # Set up position update timer for smoother slider movement
        self.position_timer = QTimer(self)
        self.position_timer.setInterval(50)  # Update every 50ms
        self.position_timer.timeout.connect(self.update_position)
        self.position_timer.start()

    def toggle_play(self):
        if self.media_player.isPlaying():
            self.media_player.pause()
        else:
            self.media_player.play()

    def set_slider_range(self, duration):
        self.slider.setRange(0, duration)
        # Update total time display
        total_seconds = duration / 1000
        self.total_time_label.setText(format_timespan(total_seconds))

    def update_slider(self, position):
        self.slider.setValue(position)
        # Update current time display
        current_seconds = position / 1000
        self.current_time_label.setText(format_timespan(current_seconds))

    def set_position(self, position):
        self.media_player.setPosition(position)
        # Update current time display when user moves slider
        current_seconds = position / 1000
        self.current_time_label.setText(format_timespan(current_seconds))

    def set_start_time(self):
        pos = self.media_player.position() / 1000
        self.current_start = pos
        QMessageBox.information(self, "Start Set", f"Start time set at {format_timespan(pos)}")

    def set_end_time(self):
        if self.current_start is None:
            QMessageBox.warning(self, "Error", "Set start time first!")
            return
        end_pos = self.media_player.position() / 1000
        if end_pos <= self.current_start:
            QMessageBox.warning(self, "Error", "End time must be greater than start time!")
            return
        
        # Add timestamp tuple and create list item with index
        self.timestamps.append((self.current_start, end_pos))
        clip_text = f"{len(self.timestamps)}. {format_timespan(self.current_start)} → {format_timespan(end_pos)} (Duration: {format_timespan(end_pos - self.current_start)})"
        self.clip_list.addItem(QListWidgetItem(clip_text))
        self.current_start = None

    def delete_selected_clip(self):
        current_row = self.clip_list.currentRow()
        if current_row == -1:
            QMessageBox.warning(self, "No Selection", "Please select a clip to delete")
            return
        
        # Confirm deletion
        reply = QMessageBox.question(
            self, 
            "Confirm Deletion", 
            f"Delete clip {current_row + 1}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            # Remove from timestamps list and UI
            self.timestamps.pop(current_row)
            self.clip_list.takeItem(current_row)
            
            # Refresh the list to update numbering
            self.refresh_clip_list()
    
    def refresh_clip_list(self):
        """Refresh the clip list to update numbering after deletion"""
        self.clip_list.clear()
        for i, (start, end) in enumerate(self.timestamps):
            clip_text = f"{i+1}. {format_timespan(start)} → {format_timespan(end)} (Duration: {format_timespan(end - start)})"
            self.clip_list.addItem(QListWidgetItem(clip_text))

    def start_clipping(self):
        if not self.timestamps:
            QMessageBox.warning(self, "Error", "No clips added!")
            return
        if not self.video_path:
            QMessageBox.warning(self, "Error", "No video loaded!")
            return
        
        self.status_label.setText("🚀 Creating clips...")
        self.clip_thread = ClippingThread(self.video_path, self.timestamps, self.output_folder)
        self.clip_thread.progress.connect(self.progress_bar.setValue)
        self.clip_thread.done.connect(self.clipping_finished)
        self.clip_thread.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        self.clip_thread.start()

    def clipping_finished(self):
        self.status_label.setText("✅ All clips created successfully!")
        QMessageBox.information(self, "Done", "All clips created successfully!")
        if self.cleanup_checkbox.isChecked() and self.video_path and os.path.exists(self.video_path):
            try:
                os.remove(self.video_path)
                self.status_label.setText("✅ Clips created and source video cleaned up!")
            except Exception as e:
                QMessageBox.warning(self, "Warning", f"Could not delete source video: {str(e)}")

    def toggle_cleanup(self, state):
        self.clean_video = bool(state)

    def update_position(self):
        if self.media_player.isPlaying():
            position = self.media_player.position()
            self.slider.setValue(position)
            # Update current time display during playback
            current_seconds = position / 1000
            self.current_time_label.setText(format_timespan(current_seconds))

    def _check_and_load_existing_video(self):
        """Check for existing video.mp4 file and load it if found."""
        video_path = os.path.join(self.output_folder, "video.mp4")
        if os.path.exists(video_path):
            self.load_video(video_path)
            self.status_label.setText("📁 Existing video loaded")

    def closeEvent(self, event):
        # Clean up resources when closing the application
        if hasattr(self, 'position_timer'):
            self.position_timer.stop()
        if hasattr(self, 'media_player'):
            self.media_player.stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = YouTubeClipper()
    window.show()
    sys.exit(app.exec())