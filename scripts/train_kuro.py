"""
scripts/train_kuro.py
──────────────────────────────────────────────────────────────────────────────
Step 2: Train a custom "Kuro" verifier model.
Uses OpenWakeWord's embedding extractor directly to train a robust sklearn model.
──────────────────────────────────────────────────────────────────────────────
"""
import sys, os
import warnings
warnings.filterwarnings('ignore')

from pathlib import Path
import numpy as np
import scipy.io.wavfile

ROOT       = Path(__file__).parent.parent
POS_DIR    = ROOT / "voice" / "training" / "positive"
NEG_DIR    = ROOT / "voice" / "training" / "negative"
MODEL_DIR  = ROOT / "voice" / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

OUT_JOBLIB = MODEL_DIR / "kuro_verifier.joblib"

# ── Validate inputs ────────────────────────────────────────────────────────────
pos_files = sorted(POS_DIR.glob("kuro_*.wav"))
neg_files = sorted(NEG_DIR.glob("neg_*.wav"))

if len(pos_files) < 5 or len(neg_files) < 5:
    print(f"ERROR: Need at least 5 positive and 5 negative samples.")
    sys.exit(1)

print(f"Training with {len(pos_files)} positive + {len(neg_files)} negative samples...")

try:
    import openwakeword
    from openwakeword.model import Model
    import joblib
    from sklearn.linear_model import LogisticRegression
    
    # Initialize base model just for its feature extractor
    model_name = "hey_jarvis"
    oww = Model(wakeword_models=[model_name])
    
    X = []
    y = []
    
    def extract_features(wav_path, is_positive):
        sr, dat = scipy.io.wavfile.read(str(wav_path))
        if len(dat) == 0: return
        
        # We process the audio in 1280-sample chunks just like the live stream
        step_size = 1280
        clip_features = []
        clip_energy = []
        
        for i in range(0, dat.shape[0]-step_size, step_size):
            chunk = dat[i:i+step_size]
            oww.predict(chunk)
            
            # Extract the raw 96-dimensional embedding for this frame
            feat = oww.preprocessor.get_features(oww.model_inputs[model_name])
            
            # Calculate RMS energy of this chunk to find where speech is
            rms = np.sqrt(np.mean(chunk.astype(np.float32)**2))
            
            clip_features.append(feat)
            clip_energy.append(rms)
            
        if not clip_features: return
        
        if is_positive:
            # For "Kuro" recordings, the wake word is exactly where the loudest part of the audio is
            best_idx = np.argmax(clip_energy)
            # Take the frame at peak energy
            X.append(clip_features[best_idx].flatten())
            y.append(1)
            
            # Data augmentation: also take the frame right before and right after peak
            if best_idx > 0:
                X.append(clip_features[best_idx-1].flatten())
                y.append(1)
            if best_idx < len(clip_features)-1:
                X.append(clip_features[best_idx+1].flatten())
                y.append(1)
        else:
            # For negative recordings, use a random sampling of frames (background noise)
            # Pick 3 random frames from the negative clip
            indices = np.random.choice(len(clip_features), min(3, len(clip_features)), replace=False)
            for idx in indices:
                X.append(clip_features[idx].flatten())
                y.append(0)

    print("Extracting features from audio...")
    for pf in pos_files: extract_features(pf, True)
    for nf in neg_files: extract_features(nf, False)
    
    X = np.array(X)
    y = np.array(y)
    
    print(f"Extracted {len(X)} feature vectors. Training classifier...")
    
    clf = LogisticRegression(class_weight='balanced', max_iter=1000)
    clf.fit(X, y)
    
    score = clf.score(X, y)
    print(f"Training accuracy: {score*100:.1f}%")
    
    joblib.dump(clf, str(OUT_JOBLIB))
    print(f"✅  Verifier model saved: {OUT_JOBLIB}")
    
except Exception as e:
    print(f"ERROR training verifier: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

# ── Update .env ────────────────────────────────────────────────────────────────
env_path = ROOT / ".env"
env_text = env_path.read_text(encoding='utf-8')

new_line = f'WAKEWORD_VERIFIER_PATH=voice/models/kuro_verifier.joblib'
if 'WAKEWORD_VERIFIER_PATH' in env_text:
    import re
    env_text = re.sub(r'WAKEWORD_VERIFIER_PATH=.*', new_line, env_text)
else:
    env_text += f'\n{new_line}\n'

env_path.write_text(env_text, encoding='utf-8')

print("\n" + "="*60)
print("  TRAINING COMPLETE!")
print("  Wake word: KURO (verified by your voice)")
print("  Restart Kuro for changes to take effect.")
print("="*60)
