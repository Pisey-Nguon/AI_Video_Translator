import sys
import os
import tempfile
import asyncio
import moviepy as mp
import whisper
from googletrans import Translator
from gtts import gTTS
import edge_tts
import pyttsx3
from pydub import AudioSegment

from PyQt5 import QtCore, QtGui, QtWidgets

# Global translator and mapping for available Edge TTS voices.
translator = Translator()
edge_tts_voices = {
    "Edge TTS - English (AriaNeural)": "en-US-AriaNeural",
    "Edge TTS - English (GuyNeural)": "en-US-GuyNeural",
    "Edge TTS - Khmer (SreymomNeural)": "km-KH-SreymomNeural"
}

def format_timestamp(seconds):
    """Convert seconds to SRT timestamp format: HH:MM:SS,mmm."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    sec = int(seconds % 60)
    msec = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{sec:02d},{msec:03d}"

def timestamp_to_seconds(timestamp):
    """Convert SRT timestamp string (HH:MM:SS,mmm) to seconds."""
    h, m, s_ms = timestamp.split(":")
    s, ms = s_ms.split(",")
    return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000.0

def generate_tts_audio_for_segment(text, target_lang, selected_voice):
    """Generate a TTS audio segment using one of the supported methods."""
    temp_filename = None
    audio_seg = None
    try:
        if selected_voice == "gTTS":
            tts = gTTS(text=text, lang=target_lang)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_file:
                temp_filename = temp_file.name
            tts.save(temp_filename)
            audio_seg = AudioSegment.from_file(temp_filename, format="mp3")
        elif selected_voice in edge_tts_voices:
            voice_name = edge_tts_voices[selected_voice]
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_file:
                temp_filename = temp_file.name
            async def run_edge_tts():
                communicate = edge_tts.Communicate(text, voice=voice_name, rate="+0%")
                await communicate.save(temp_filename)
            asyncio.run(run_edge_tts())
            audio_seg = AudioSegment.from_file(temp_filename, format="mp3")
        elif selected_voice == "pyttsx3 (Default)":
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
                temp_filename = temp_file.name
            engine = pyttsx3.init()
            engine.save_to_file(text, temp_filename)
            engine.runAndWait()
            audio_seg = AudioSegment.from_file(temp_filename, format="wav")
        else:
            print("No valid voice option selected for TTS.")
    except Exception as e:
        print(f"Error generating TTS audio: {str(e)}")
    finally:
        if temp_filename and os.path.exists(temp_filename):
            os.remove(temp_filename)
    return audio_seg

class TranslatorWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(str)
    statusUpdate = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)
    
    def __init__(self, video_path, srt_destination_path, target_lang, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.srt_destination_path = srt_destination_path
        self.target_lang = target_lang
    
    def run(self):
        try:
            self.statusUpdate.emit("Extracting audio from video...\n")
            clip = mp.VideoFileClip(self.video_path)
            audio_path = "temp_audio.wav"
            clip.audio.write_audiofile(audio_path, logger=None)
            
            self.statusUpdate.emit("Audio extracted. Loading Whisper model...\n")
            model = whisper.load_model("base")
            
            self.statusUpdate.emit("Transcribing audio...\n")
            result = model.transcribe(audio_path)
            segments = result.get("segments", [])
            if not segments:
                full_text = result.get("text", "")
                segments = [{"start": 0, "end": clip.duration, "text": full_text}]
            self.statusUpdate.emit(f"Transcribed {len(segments)} segments.\n")
            
            translated_segments = []
            self.statusUpdate.emit("Translating segments...\n")
            for seg in segments:
                text = seg.get("text", "")
                try:
                    translation_obj = translator.translate(text, dest=self.target_lang)
                    translated_text = translation_obj.text if translation_obj and translation_obj.text else text
                except Exception as t_err:
                    translated_text = text
                    self.statusUpdate.emit(f"Warning: Translation error for a segment: {str(t_err)}\n")
                translated_segments.append(translated_text)
            
            srt_content = ""
            for i, seg in enumerate(segments):
                start = seg["start"]
                end = seg["end"]
                translated_text = translated_segments[i]
                srt_content += f"{i+1}\n"
                srt_content += f"{format_timestamp(start)} --> {format_timestamp(end)}\n"
                srt_content += translated_text.strip() + "\n\n"
            
            with open(self.srt_destination_path, "w", encoding="utf-8") as f:
                f.write(srt_content)
            self.statusUpdate.emit(f"SRT file saved to {self.srt_destination_path}\n")
            os.remove(audio_path)
            
            self.finished.emit(srt_content)
        except Exception as e:
            self.error.emit(str(e))

class VoiceWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal()
    statusUpdate = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)
    
    def __init__(self, srt_content, voice_destination_path, target_lang, selected_voice, parent=None):
        super().__init__(parent)
        self.srt_content = srt_content
        self.voice_destination_path = voice_destination_path
        self.target_lang = target_lang
        self.selected_voice = selected_voice
    
    def run(self):
        try:
            self.statusUpdate.emit("Generating voice audio based on timeline...\n")
            segments = []
            parts = self.srt_content.strip().split("\n\n")
            for part in parts:
                lines = part.splitlines()
                if len(lines) >= 3:
                    timeline = lines[1]
                    if "-->" in timeline:
                        start_str, end_str = timeline.split("-->")
                        start = timestamp_to_seconds(start_str.strip())
                        end = timestamp_to_seconds(end_str.strip())
                        text = "\n".join(lines[2:]).strip()
                        segments.append({"start": start, "end": end, "text": text})
            
            if not segments:
                self.statusUpdate.emit("No valid SRT segments found.\n")
                return
            
            final_audio = AudioSegment.silent(duration=0)
            current_time = 0
            for seg in segments:
                gap = seg["start"] - current_time
                if gap > 0:
                    final_audio += AudioSegment.silent(duration=int(gap * 1000))
                seg_audio = generate_tts_audio_for_segment(seg["text"], self.target_lang, self.selected_voice)
                if seg_audio:
                    final_audio += seg_audio
                else:
                    self.statusUpdate.emit("Skipping a segment due to TTS generation error.\n")
                current_time = seg["end"]
            
            ext = os.path.splitext(self.voice_destination_path)[1].lower()[1:]
            if ext not in ["mp3", "wav"]:
                ext = "mp3"
            final_audio.export(self.voice_destination_path, format=ext)
            self.statusUpdate.emit(f"Voice audio saved to {self.voice_destination_path}\n")
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.video_path = ""
        self.srt_destination_path = ""
        self.voice_destination_path = ""
        self.setWindowTitle("Video Translator to SRT & Voice")
        self.resize(800, 800)
        
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QVBoxLayout(central_widget)
        
        # --- File Selection Section ---
        file_layout = QtWidgets.QHBoxLayout()
        self.select_button = QtWidgets.QPushButton("Select Video File")
        self.select_button.clicked.connect(self.select_video)
        file_layout.addWidget(self.select_button)
        self.video_label = QtWidgets.QLabel("No file selected.")
        file_layout.addWidget(self.video_label)
        main_layout.addLayout(file_layout)
        
        srt_layout = QtWidgets.QHBoxLayout()
        self.srt_button = QtWidgets.QPushButton("Choose SRT Destination")
        self.srt_button.clicked.connect(self.choose_srt_destination)
        srt_layout.addWidget(self.srt_button)
        self.srt_label = QtWidgets.QLabel("No SRT destination selected.")
        srt_layout.addWidget(self.srt_label)
        main_layout.addLayout(srt_layout)
        
        voice_layout = QtWidgets.QHBoxLayout()
        self.voice_button_dest = QtWidgets.QPushButton("Choose Voice Destination")
        self.voice_button_dest.clicked.connect(self.choose_voice_destination)
        voice_layout.addWidget(self.voice_button_dest)
        self.voice_label = QtWidgets.QLabel("No voice destination selected.")
        voice_layout.addWidget(self.voice_label)
        main_layout.addLayout(voice_layout)
        
        # --- Language & Voice Options ---
        lang_layout = QtWidgets.QHBoxLayout()
        self.lang_label = QtWidgets.QLabel("Target Language Code (e.g., 'es', 'fr', 'km'):")
        lang_layout.addWidget(self.lang_label)
        self.lang_entry = QtWidgets.QLineEdit()
        lang_layout.addWidget(self.lang_entry)
        main_layout.addLayout(lang_layout)
        
        voice_model_layout = QtWidgets.QHBoxLayout()
        self.voice_model_label = QtWidgets.QLabel("Select Voice Model:")
        voice_model_layout.addWidget(self.voice_model_label)
        self.voice_combo = QtWidgets.QComboBox()
        voice_options = [
            "gTTS",
            "Edge TTS - English (AriaNeural)",
            "Edge TTS - English (GuyNeural)",
            "Edge TTS - Khmer (SreymomNeural)",
            "pyttsx3 (Default)"
        ]
        self.voice_combo.addItems(voice_options)
        voice_model_layout.addWidget(self.voice_combo)
        main_layout.addLayout(voice_model_layout)
        
        # --- Action Buttons ---
        button_layout = QtWidgets.QHBoxLayout()
        self.translate_button = QtWidgets.QPushButton("Translate Video")
        self.translate_button.clicked.connect(self.translate_video)
        button_layout.addWidget(self.translate_button)
        self.generate_voice_button = QtWidgets.QPushButton("Generate Voice Audio")
        self.generate_voice_button.clicked.connect(self.generate_voice)
        button_layout.addWidget(self.generate_voice_button)
        main_layout.addLayout(button_layout)
        
        # --- Editable SRT Text Area ---
        main_layout.addWidget(QtWidgets.QLabel("Edit SRT (Subtitles) Here:"))
        self.srt_text = QtWidgets.QTextEdit()
        # Set a Khmer-supporting font (ensure this font is installed on your system)
        font = QtGui.QFont("Noto Sans Khmer", 12)
        self.srt_text.setFont(font)
        main_layout.addWidget(self.srt_text)
        
        # --- Clipboard Paste Button ---
        self.paste_button = QtWidgets.QPushButton("Paste from Clipboard")
        self.paste_button.clicked.connect(self.paste_clipboard)
        main_layout.addWidget(self.paste_button)
        
        # --- Status Output Area ---
        main_layout.addWidget(QtWidgets.QLabel("Status Output:"))
        self.status_text = QtWidgets.QTextEdit()
        self.status_text.setReadOnly(True)
        main_layout.addWidget(self.status_text)
    
    def append_status(self, message):
        self.status_text.append(message)
    
    def select_video(self):
        options = QtWidgets.QFileDialog.Options()
        file_name, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Video File", "", 
                        "Video Files (*.mp4 *.avi *.mkv *.mov);;All Files (*)", options=options)
        if file_name:
            self.video_path = file_name
            self.video_label.setText(f"Selected: {os.path.basename(file_name)}")
        else:
            self.video_label.setText("No file selected.")
    
    def choose_srt_destination(self):
        options = QtWidgets.QFileDialog.Options()
        file_name, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save SRT File As", "", 
                        "SRT Files (*.srt);;All Files (*)", options=options)
        if file_name:
            self.srt_destination_path = file_name
            self.srt_label.setText(f"SRT Destination: {os.path.basename(file_name)}")
        else:
            self.srt_label.setText("No SRT destination selected.")
    
    def choose_voice_destination(self):
        options = QtWidgets.QFileDialog.Options()
        file_name, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save Voice Audio File As", "", 
                        "Audio Files (*.mp3 *.wav);;All Files (*)", options=options)
        if file_name:
            self.voice_destination_path = file_name
            self.voice_label.setText(f"Voice Destination: {os.path.basename(file_name)}")
        else:
            self.voice_label.setText("No voice destination selected.")
    
    def paste_clipboard(self):
        clipboard = QtWidgets.QApplication.clipboard()
        text = clipboard.text()
        self.srt_text.insertPlainText(text)
    
    def translate_video(self):
        if not self.video_path:
            QtWidgets.QMessageBox.critical(self, "Error", "Please select a video file first!")
            return
        if not self.srt_destination_path:
            QtWidgets.QMessageBox.critical(self, "Error", "Please choose a destination for the SRT file!")
            return
        target_lang = self.lang_entry.text().strip()
        if not target_lang:
            QtWidgets.QMessageBox.critical(self, "Error", "Please enter a target language code (e.g., 'es', 'fr', 'km')!")
            return
        
        self.translate_button.setEnabled(False)
        self.append_status("Starting translation...\n")
        
        self.translator_thread = QtCore.QThread()
        self.translator_worker = TranslatorWorker(self.video_path, self.srt_destination_path, target_lang)
        self.translator_worker.moveToThread(self.translator_thread)
        self.translator_thread.started.connect(self.translator_worker.run)
        self.translator_worker.statusUpdate.connect(self.append_status)
        self.translator_worker.finished.connect(self.on_translation_finished)
        self.translator_worker.error.connect(self.on_worker_error)
        self.translator_worker.finished.connect(lambda _: self.translator_thread.quit())
        self.translator_worker.finished.connect(lambda _: self.translator_worker.deleteLater())
        self.translator_thread.finished.connect(lambda: self.translator_thread.deleteLater())
        self.translator_thread.start()
    
    def on_translation_finished(self, srt_content):
        self.srt_text.setPlainText(srt_content)
        self.translate_button.setEnabled(True)
    
    def generate_voice(self):
        if not self.voice_destination_path:
            QtWidgets.QMessageBox.critical(self, "Error", "Please choose a destination for the voice audio file!")
            return
        srt_content = self.srt_text.toPlainText().strip()
        if not srt_content:
            QtWidgets.QMessageBox.critical(self, "Error", "SRT text is empty. Please generate and/or edit the subtitles first!")
            return
        target_lang = self.lang_entry.text().strip()
        if not target_lang:
            QtWidgets.QMessageBox.critical(self, "Error", "Please enter a target language code!")
            return
        
        selected_voice = self.voice_combo.currentText()
        self.generate_voice_button.setEnabled(False)
        self.append_status("Starting voice generation...\n")
        
        self.voice_thread = QtCore.QThread()
        self.voice_worker = VoiceWorker(srt_content, self.voice_destination_path, target_lang, selected_voice)
        self.voice_worker.moveToThread(self.voice_thread)
        self.voice_thread.started.connect(self.voice_worker.run)
        self.voice_worker.statusUpdate.connect(self.append_status)
        self.voice_worker.finished.connect(self.on_voice_finished)
        self.voice_worker.error.connect(self.on_worker_error)
        self.voice_worker.finished.connect(lambda: self.voice_thread.quit())
        self.voice_worker.finished.connect(lambda: self.voice_worker.deleteLater())
        self.voice_thread.finished.connect(lambda: self.voice_thread.deleteLater())
        self.voice_thread.start()
    
    def on_voice_finished(self):
        self.generate_voice_button.setEnabled(True)
    
    def on_worker_error(self, error_msg):
        QtWidgets.QMessageBox.critical(self, "Error", error_msg)
        self.translate_button.setEnabled(True)
        self.generate_voice_button.setEnabled(True)

if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
