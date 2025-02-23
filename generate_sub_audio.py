import os
from gtts import gTTS
from pydub import AudioSegment
from pydub.playback import play
import torch
from transformers import pipeline

def load_voice_model(model_name):
    if model_name == "gtts":
        return "gtts"
    elif model_name == "huggingface":
        # Replace with a model that supports Khmer (if available)
        return pipeline("text-to-speech", model="facebook/mms-tts-kmr")  # Example model
    else:
        raise ValueError("Unsupported voice model")

def convert_srt_to_audio(srt_path, voice_model, output_path, language='en'):
    with open(srt_path, 'r', encoding='utf-8') as file:
        srt_content = file.read()

    if voice_model == "gtts":
        tts = gTTS(text=srt_content, lang=language)  # Set language to 'km' for Khmer
        tts.save(output_path)
    else:
        # Using Hugging Face model
        speech = voice_model(srt_content)
        audio = torch.tensor(speech["audio"])
        audio = AudioSegment(
            audio.numpy().tobytes(),
            frame_rate=speech["sampling_rate"],
            sample_width=audio.numpy().dtype.itemsize,
            channels=1
        )
        audio.export(output_path, format="wav")

def main():
    srt_file = input("Enter the path to the SRT file: ")
    voice_model_choice = input("Select voice model (gtts/huggingface): ")
    language = input("Enter the language code (e.g., 'en' for English, 'km' for Khmer): ")
    output_file = input("Enter the destination path for the output audio file: ")

    voice_model = load_voice_model(voice_model_choice)
    convert_srt_to_audio(srt_file, voice_model, output_file, language)

    print(f"Audio file has been generated and saved to {output_file}")

if __name__ == "__main__":
    main()