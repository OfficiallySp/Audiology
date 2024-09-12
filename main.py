import sys
import os
from PyQt6.QtWidgets import QApplication, QMainWindow, QPushButton, QFileDialog, QProgressBar
from PyQt6.QtCore import QThread, pyqtSignal
import requests
import mutagen
import io
from pydub import AudioSegment

class AudioProcessor(QThread):
    progress = pyqtSignal(int)
    
    def __init__(self, files, api_token):
        super().__init__()
        self.files = files
        self.api_token = api_token
    
    def run(self):
        for i, file in enumerate(self.files):
            self.process_file(file)
            self.progress.emit(int((i + 1) / len(self.files) * 100))
    
    def process_file(self, file):
        # Read audio file
        audio = mutagen.File(file, easy=True)
        
        # Create a sample of the audio for recognition
        sample = self.create_sample(file)
        
        # Send sample to AudD.io API
        response = self.recognize_song(sample)
        
        if response:
            # Update metadata
            if isinstance(audio, mutagen.easyid3.EasyID3):
                # For MP3 files
                if 'artist' in response: audio['artist'] = response['artist']
                if 'title' in response: audio['title'] = response['title']
                if 'album' in response: audio['album'] = response['album']
                if 'date' in response: audio['date'] = response['release_date']
                # Note: 'label' is not a standard ID3 tag, so we skip it
            else:
                # For other file types
                if 'artist' in response: audio['artist'] = response['artist']
                if 'title' in response: audio['title'] = response['title']
                if 'album' in response: audio['album'] = response['album']
                if 'date' in response: audio['date'] = response['release_date']
                if 'label' in response: audio['label'] = response['label']
            
            # Save artwork
            artwork_path = self.save_artwork(response['image'], os.path.dirname(file))
            if artwork_path:
                # Adding artwork is file type specific and might need additional handling
                pass
            
            audio.save()
            
            # Rename the file
            try:
                new_filename = f"{response['artist']} - {response['title']}{os.path.splitext(file)[1]}"
                new_filename = "".join(c for c in new_filename if c.isalnum() or c in (' ', '.', '-', '_')).rstrip()
                new_path = os.path.join(os.path.dirname(file), new_filename)
                os.rename(file, new_path)
            except OSError as e:
                print(f"Error renaming file: {str(e)}")
    
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
    
    def save_artwork(self, image_url, directory):
        if not image_url:
            return None
        try:
            response = requests.get(image_url)
            response.raise_for_status()
            artwork_path = os.path.join(directory, 'artwork.jpg')
            with open(artwork_path, 'wb') as f:
                f.write(response.content)
            return artwork_path
        except requests.RequestException as e:
            print(f"Failed to download artwork: {str(e)}")
            return None

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
        
        self.api_token = "REDACTED"  # Replace with your actual API token
    
    def select_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Audio Files", "", "Audio Files (*.mp3 *.wav *.ogg *.flac *.m4a)")
        if files:
            self.process_files(files)
    
    def process_files(self, files):
        self.progress_bar.show()
        self.processor = AudioProcessor(files, self.api_token)
        self.processor.progress.connect(self.update_progress)
        self.processor.start()
    
    def update_progress(self, value):
        self.progress_bar.setValue(value)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())