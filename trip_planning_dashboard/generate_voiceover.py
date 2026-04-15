from google import genai
from google.genai import types
from dotenv import load_dotenv
import os

# Load credentials
load_dotenv(dotenv_path="../.env")

client = genai.Client()

# The script to narrated
script_text = """
I needed to plan my vacation and thought of building this tool using AI... Welcome to Triply.
Planning a trip can be overwhelming. That's why I created this dashboard. 
It takes your destination, dates, and even the season into account.
Simply tell us where you're going—like Bali—and what you're looking for. 
Whether it's luxury, adventure, or a romantic getaway.
Our AI engine goes to work, crafting a personalized experience just for you.
In seconds, you get a full day-by-day itinerary. 
But it's more than just places to visit. We include the ideal time to spend at each spot.
Budgeting is made simple, too. You'll see costs in both local currency and USD, with seasonal comparisons.
And finally, our seasonal travel guide helps you pick the perfect time for your journey based on weather and events.
Your perfect journey starts here. Build yours today with Triply.
"""

def generate_soothing_voice():
    print("Generating soothing voice narration with Gemini 3.1 TTS...")
    
    # Configure the speech settings
    # 'Puck' or 'Charon' are often high-fidelity options
    speech_config = types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                voice_name="Puck" 
            )
        )
    )

    try:
        # We use the specialized TTS model
        response = client.models.generate_content(
            model="gemini-3.1-flash-tts-preview", 
            contents=script_text,
            config=types.GenerateContentConfig(
                response_modalities=["audio"],
                speech_config=speech_config,
            )
        )

        # Access the audio data from the response parts
        audio_found = False
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                audio_bytes = part.inline_data.data
                
                # Import wave to handle the header
                import wave
                
                # Save as a proper WAV file (16-bit PCM, 24kHz, Mono)
                with wave.open("narration.wav", "wb") as wav_file:
                    wav_file.setnchannels(1)  # Mono
                    wav_file.setsampwidth(2)   # 16-bit (2 bytes)
                    wav_file.setframerate(24000) # 24kHz
                    wav_file.writeframes(audio_bytes)
                
                print("Successfully saved formatted audio to narration.wav")
                audio_found = True
                break

        
        if not audio_found:
            print("No audio data found in the response parts.")

    except Exception as e:
        print(f"API Error: {str(e)}")
        raise e

if __name__ == "__main__":
    try:
        generate_soothing_voice()
    except Exception as e:
        print(f"Error generating voiceover: {e}")
