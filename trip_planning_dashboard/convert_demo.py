from PIL import Image
import numpy as np
from moviepy import ImageSequenceClip
import os

def convert_webp_to_mp4(input_path, output_path, fps=20):
    print(f"Opening {input_path}...")
    img = Image.open(input_path)
    
    frames = []
    try:
        while True:
            # Convert frame to RGB numpy array
            frames.append(np.array(img.convert('RGB')))
            img.seek(img.tell() + 1)
    except EOFError:
        pass # End of frames
    
    print(f"Extracted {len(frames)} frames. Converting to MP4...")
    
    # Create video clip
    clip = ImageSequenceClip(frames, fps=fps)
    clip.write_videofile(output_path, codec="libx264")
    print(f"Successfully saved to {output_path}")

if __name__ == "__main__":
    input_file = "dashboard_demo.webp"
    output_file = "dashboard_demo.mp4"
    target_duration = 58  # Target duration in seconds to match audio
    
    if os.path.exists(input_file):
        # We need to know the frame count first to calculate the correct FPS
        img = Image.open(input_file)
        frame_count = 0
        try:
            while True:
                frame_count += 1
                img.seek(img.tell() + 1)
        except EOFError:
            pass
        
        # Calculate FPS to stretch/shrink video to match audio
        optimized_fps = frame_count / target_duration
        print(f"Total frames: {frame_count}. Adjusting FPS to {optimized_fps:.2f} to match {target_duration}s duration.")
        
        convert_webp_to_mp4(input_file, output_file, fps=optimized_fps)
    else:
        print(f"Error: {input_file} not found!")
