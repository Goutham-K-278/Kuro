import os
import pyttsx3
import random
import time

def main():
    pos_dir = "voice/train/samples_pos"
    neg_dir = "voice/train/samples_neg"
    
    # Ensure directories exist
    for d in [pos_dir, neg_dir]:
        if not os.path.exists(d):
            os.makedirs(d)

    # Move user samples to pos_dir if they are in the old samples folder
    old_samples = "voice/train/samples"
    if os.path.exists(old_samples):
        for f in os.listdir(old_samples):
            if f.endswith(".wav"):
                os.rename(os.path.join(old_samples, f), os.path.join(pos_dir, f))
        #os.rmdir(old_samples)

    print("--- Local Synthetic Voice Generator (v2.0) ---")
    
    engine = pyttsx3.init()
    voices = engine.getProperty('voices')
    
    print(f"Found {len(voices)} local voices.")
    
    # Positive words
    pos_words = ["Kuro", "Kuro", "Kuro.", "Kuro!"]
    # Negative (distractor) words
    neg_words = ["Hero", "Zero", "Cairo", "Kudo", "Ku-ko", "Okay", "Hey", "Computer", "Kiro", "Kero"]

    count_pos = 1
    count_neg = 1
    
    for voice in voices:
        engine.setProperty('voice', voice.id)
        
        # 1. Generate Positive Variants
        print(f"Generating positive variants for voice: {voice.name}...")
        for word in pos_words:
            for _ in range(3):
                rate = 150 + random.randint(-40, 40)
                engine.setProperty('rate', rate)
                output_path = os.path.join(pos_dir, f"synth_pos_{count_pos}.wav")
                engine.save_to_file(word, output_path)
                engine.runAndWait()
                count_pos += 1

        # 2. Generate Negative Variants
        print(f"Generating negative (distractor) variants for voice: {voice.name}...")
        for word in neg_words:
            for _ in range(2):
                rate = 150 + random.randint(-40, 40)
                engine.setProperty('rate', rate)
                output_path = os.path.join(neg_dir, f"synth_neg_{count_neg}.wav")
                engine.save_to_file(word, output_path)
                engine.runAndWait()
                count_neg += 1

    print(f"\nSuccessfully generated {count_pos-1} POSITIVE and {count_neg-1} NEGATIVE samples.")

if __name__ == "__main__":
    main()
