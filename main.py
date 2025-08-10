import sys
import os
import yt_dlp
from moviepy import VideoFileClip
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
    done = Signal(str)
    error = Signal(str)

    def __init__(self, url, output_path):
        super().__init__()
        self.url = url
        self.output_path = output_path

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
                    pass  # If we can't remove it, yt-dlp will handle it

            # Configure yt-dlp options
            ydl_opts = {
                'outtmpl': self.output_path,
                'format': 'best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]',
                'progress_hooks': [self.hook],
                'merge_output_format': 'mp4',
                'quiet': False,  # Enable output for debugging
                'no_warnings': False,
                'ignoreerrors': True,
                'nocheckcertificate': True,
                'postprocessors': [{
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': 'mp4'
                }]
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                error = ydl.download([self.url])
                if error:
                    raise Exception("Download failed")
                
                # Verify the file exists after download
                if not os.path.exists(self.output_path):
                    raise Exception("Download completed but file not found")
                
        except Exception as e:
            self.error.emit(f"Download error: {str(e)}")
            return

    def hook(self, d):
        if d['status'] == 'downloading':
            # Calculate progress based on either total bytes or total bytes estimate
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            if total:
                downloaded = d.get('downloaded_bytes', 0)
                percent = (downloaded / total) * 100
                self.progress.emit(percent)
            
        elif d['status'] == 'finished':
            self.progress.emit(100)
            if os.path.exists(d['filename']):
                self.done.emit(d['filename'])
            else:
                self.error.emit("File not found after download")


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

            # Load and process the video using context manager
            with VideoFileClip(self.video_path) as video:
                total = len(clips_config)
                for i, clip_config in enumerate(clips_config, 1):
                    start_time = float(clip_config['start_time'])
                    end_time = float(clip_config['end_time'])
                    clip_name = clip_config['name']

                    # Extract and save the subclip
                    subclip = video.subclipped(start_time, end_time)
                    output_path = os.path.join(self.output_folder, f"{clip_name}.mp4")
                    
                    try:
                        # Write with default settings which handle audio correctly
                        subclip.write_videofile(
                            output_path,
                            codec="libx264",
                            audio_codec="aac",
                            # verbose=False,
                            preset='medium',  # Good balance between speed and quality
                            threads=2  # Use multiple threads for faster processing
                        )
                        self.progress.emit(i / total * 100)
                    finally:
                        subclip.close()

            self.done.emit()
        except Exception as e:
            self.error.emit(str(e))


class YouTubeClipper(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🎥 YouTube Video Clipper with Preview")
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
        download_btn = QPushButton("⬇ Download & Load Video")
        download_btn.clicked.connect(self.download_video)
        url_layout.addWidget(self.url_input)
        url_layout.addWidget(download_btn)
        main_layout.addLayout(url_layout)

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
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderMoved.connect(self.set_position)
        self.media_player.positionChanged.connect(self.update_slider)
        self.media_player.durationChanged.connect(self.set_slider_range)
        controls_layout.addWidget(play_btn)
        controls_layout.addWidget(self.slider)
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

        # Clip List
        self.clip_list = QListWidget()
        main_layout.addWidget(self.clip_list)

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
        self.download_thread = DownloadThread(url, os.path.join(self.output_folder, "video.mp4"))
        self.download_thread.progress.connect(self.progress_bar.setValue)
        self.download_thread.done.connect(self.load_video)
        self.download_thread.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        self.download_thread.start()

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
        QMessageBox.information(self, "Loaded", "Video loaded successfully!")
        
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

    def update_slider(self, position):
        self.slider.setValue(position)

    def set_position(self, position):
        self.media_player.setPosition(position)

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
        self.timestamps.append((self.current_start, end_pos))
        self.clip_list.addItem(QListWidgetItem(f"{format_timespan(self.current_start)} → {format_timespan(end_pos)}"))
        self.current_start = None

    def start_clipping(self):
        if not self.timestamps:
            QMessageBox.warning(self, "Error", "No clips added!")
            return
        if not self.video_path:
            QMessageBox.warning(self, "Error", "No video loaded!")
            return
        self.clip_thread = ClippingThread(self.video_path, self.timestamps, self.output_folder)
        self.clip_thread.progress.connect(self.progress_bar.setValue)
        self.clip_thread.done.connect(self.clipping_finished)
        self.clip_thread.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        self.clip_thread.start()

    def clipping_finished(self):
        QMessageBox.information(self, "Done", "All clips created successfully!")
        if self.cleanup_checkbox.isChecked() and self.video_path and os.path.exists(self.video_path):
            try:
                os.remove(self.video_path)
            except Exception as e:
                QMessageBox.warning(self, "Warning", f"Could not delete source video: {str(e)}")

    def toggle_cleanup(self, state):
        self.clean_video = bool(state)

    def update_position(self):
        if self.media_player.isPlaying():
            self.slider.setValue(self.media_player.position())

    def _check_and_load_existing_video(self):
        """Check for existing video.mp4 file and load it if found."""
        video_path = os.path.join(self.output_folder, "video.mp4")
        if os.path.exists(video_path):
            self.load_video(video_path)

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
