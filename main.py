import sys
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QPushButton, QFileDialog, QProgressBar,
                             QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QDialogButtonBox)
from PyQt6.QtCore import QThread, pyqtSignal
import requests
import mutagen
import io
from pydub import AudioSegment
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, APIC
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover
from mutagen.wave import WAVE

class MetadataComparisonDialog(QDialog):
    def __init__(self, old_metadata, new_metadata, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Metadata Comparison")
        self.setLayout(QVBoxLayout())

        fields = ['artist', 'title', 'album', 'date']
        self.new_metadata = new_metadata

        for field in fields:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{field.capitalize()}:"))
            row.addWidget(QLabel(old_metadata.get(field, '')))
            edit = QLineEdit(new_metadata.get(field, ''))
            edit.setObjectName(field)
            row.addWidget(edit)
            self.layout().addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.layout().addWidget(buttons)

    def get_edited_metadata(self):
        for field in ['artist', 'title', 'album', 'date']:
            edit = self.findChild(QLineEdit, field)
            if edit:
                self.new_metadata[field] = edit.text()
        return self.new_metadata

class AudioProcessor(QThread):
    progress = pyqtSignal(int)
    metadata_ready = pyqtSignal(str, dict, dict)
    
    def __init__(self, files, api_token):
        super().__init__()
        self.files = files
        self.api_token = api_token
    
    def run(self):
        for i, file in enumerate(self.files):
            old_metadata, new_metadata = self.process_file(file)
            self.metadata_ready.emit(file, old_metadata, new_metadata)
            self.progress.emit(int((i + 1) / len(self.files) * 100))
    
    def process_file(self, file):
        # Read audio file
        audio = mutagen.File(file)
        
        # Get old metadata
        old_metadata = self.get_metadata(audio)
        
        # Create a sample of the audio for recognition
        sample = self.create_sample(file)
        
        # Send sample to AudD.io API
        new_metadata = self.recognize_song(sample)
        
        return old_metadata, new_metadata

    def get_metadata(self, audio):
        metadata = {}
        if isinstance(audio, mutagen.mp3.MP3):
            audio = ID3(audio.filename)
            metadata['artist'] = str(audio.get('TPE1', ['']))
            metadata['title'] = str(audio.get('TIT2', ['']))
            metadata['album'] = str(audio.get('TALB', ['']))
            metadata['date'] = str(audio.get('TDRC', ['']))
        elif isinstance(audio, mutagen.flac.FLAC):
            metadata['artist'] = ', '.join(audio.get('artist', []))
            metadata['title'] = ', '.join(audio.get('title', []))
            metadata['album'] = ', '.join(audio.get('album', []))
            metadata['date'] = ', '.join(audio.get('date', []))
        elif isinstance(audio, mutagen.mp4.MP4):
            metadata['artist'] = ', '.join(audio.get('\xa9ART', []))
            metadata['title'] = ', '.join(audio.get('\xa9nam', []))
            metadata['album'] = ', '.join(audio.get('\xa9alb', []))
            metadata['date'] = ', '.join(audio.get('\xa9day', []))
        else:
            for key in ['artist', 'title', 'album', 'date']:
                metadata[key] = ', '.join(audio.get(key, []))
        return metadata

    def create_sample(self, file):
        # Load the audio file
        audio = AudioSegment.from_file(file)
        
        # Take a 10-second sample from the middle of the track
        duration = len(audio)
        start = (duration - 10000) // 2 if duration > 10000 else 0
        sample = audio[start:start+10000]
        
        # Export the sample as a WAV file in memory
        buffer = io.BytesIO()
        sample.export(buffer, format="wav")
        
        return buffer.getvalue()
    
    def recognize_song(self, sample):
        url = 'https://api.audd.io/'
        data = {
            'api_token': self.api_token,
            'return': 'apple_music,spotify',
        }
        files = {
            'file': ('audio.wav', sample, 'audio/wav'),
        }
        try:
            response = requests.post(url, data=data, files=files)
            response.raise_for_status()
            result = response.json()
            
            if result['status'] == 'success' and result['result']:
                return {
                    'artist': result['result']['artist'],
                    'title': result['result']['title'],
                    'album': result['result']['album'],
                    'release_date': result['result'].get('release_date', ''),
                    'label': result['result'].get('label', ''),
                    'image': result['result'].get('spotify', {}).get('album', {}).get('images', [{}])[0].get('url', '')
                }
            else:
                print(f"Song not recognized: {result.get('error', {}).get('error_message', 'Unknown error')}")
                return None
        except requests.RequestException as e:
            print(f"API request failed: {str(e)}")
            return None
    
    def download_artwork(self, image_url):
        if not image_url:
            return None
        try:
            response = requests.get(image_url)
            response.raise_for_status()
            return response.content
        except requests.RequestException as e:
            print(f"Failed to download artwork: {str(e)}")
            return None

    def embed_artwork(self, audio, artwork_data):
        if isinstance(audio, mutagen.mp3.MP3):
            audio['APIC'] = APIC(
                encoding=3,
                mime='image/jpeg',
                type=3,  # 3 is for the cover image
                desc=u'Cover',
                data=artwork_data
            )
        elif isinstance(audio, mutagen.flac.FLAC):
            picture = Picture()
            picture.data = artwork_data
            picture.type = 3
            picture.mime = 'image/jpeg'
            picture.desc = 'Cover'
            audio.add_picture(picture)
        elif isinstance(audio, mutagen.mp4.MP4):
            audio['covr'] = [MP4Cover(artwork_data, imageformat=MP4Cover.FORMAT_JPEG)]
        else:
            print("Artwork embedding not supported for this file type")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Music Library Organizer")
        self.setGeometry(100, 100, 300, 200)
        
        self.button = QPushButton("Select Files", self)
        self.button.setGeometry(100, 70, 100, 30)
        self.button.clicked.connect(self.select_files)
        
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setGeometry(50, 120, 200, 25)
        self.progress_bar.hide()
        
        self.api_token = "db0c8fb5781cd90f459b003dbcfbb93b"  # Replace with your actual API token
    
    def select_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Audio Files", "", "Audio Files (*.mp3 *.wav *.ogg *.flac *.m4a)")
        if files:
            self.process_files(files)
    
    def process_files(self, files):
        self.progress_bar.show()
        self.processor = AudioProcessor(files, self.api_token)
        self.processor.progress.connect(self.update_progress)
        self.processor.metadata_ready.connect(self.show_comparison_dialog)
        self.processor.start()
    
    def update_progress(self, value):
        self.progress_bar.setValue(value)

    def show_comparison_dialog(self, file, old_metadata, new_metadata):
        dialog = MetadataComparisonDialog(old_metadata, new_metadata, self)
        if dialog.exec():
            edited_metadata = dialog.get_edited_metadata()
            self.apply_metadata(file, edited_metadata)

    def apply_metadata(self, file, metadata):
        audio = mutagen.File(file)
        if isinstance(audio, mutagen.wave.WAVE):
            # WAV files have limited metadata support
            print(f"Warning: Limited metadata support for WAV file: {file}")
            # We can try to set some basic tags, but they might not be supported by all players
            audio.tags = WAVE.tags(audio)
            if 'artist' in metadata: audio.tags['artist'] = metadata['artist']
            if 'title' in metadata: audio.tags['title'] = metadata['title']
            audio.save()
        elif isinstance(audio, mutagen.mp3.MP3):
            tags = ID3(file)
            if 'artist' in metadata: tags['TPE1'] = TPE1(encoding=3, text=metadata['artist'])
            if 'title' in metadata: tags['TIT2'] = TIT2(encoding=3, text=metadata['title'])
            if 'album' in metadata: tags['TALB'] = TALB(encoding=3, text=metadata['album'])
            if 'date' in metadata: tags['TDRC'] = TDRC(encoding=3, text=metadata['date'])
            tags.save()
        elif isinstance(audio, mutagen.flac.FLAC):
            if 'artist' in metadata: audio['artist'] = metadata['artist']
            if 'title' in metadata: audio['title'] = metadata['title']
            if 'album' in metadata: audio['album'] = metadata['album']
            if 'date' in metadata: audio['date'] = metadata['date']
            audio.save()
        elif isinstance(audio, mutagen.mp4.MP4):
            if 'artist' in metadata: audio['\xa9ART'] = metadata['artist']
            if 'title' in metadata: audio['\xa9nam'] = metadata['title']
            if 'album' in metadata: audio['\xa9alb'] = metadata['album']
            if 'date' in metadata: audio['\xa9day'] = metadata['date']
            audio.save()
        elif audio is not None:
            for key in ['artist', 'title', 'album', 'date']:
                if key in metadata:
                    try:
                        audio[key] = [metadata[key]]  # Set as a list for compatibility
                    except KeyError:
                        print(f"Warning: '{key}' tag not supported for this file type")
            audio.save()
        else:
            print(f"Warning: Unsupported file type for {file}")
            return

        # Embed artwork
        if 'image' in metadata and metadata['image']:
            artwork_data = self.processor.download_artwork(metadata['image'])
            if artwork_data:
                self.processor.embed_artwork(audio, artwork_data)
                audio.save()

        # Rename the file
        try:
            new_filename = f"{metadata['artist']} - {metadata['title']}{os.path.splitext(file)[1]}"
            new_filename = "".join(c for c in new_filename if c.isalnum() or c in (' ', '.', '-', '_')).rstrip()
            new_path = os.path.join(os.path.dirname(file), new_filename)
            os.rename(file, new_path)
        except OSError as e:
            print(f"Error renaming file: {str(e)}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())