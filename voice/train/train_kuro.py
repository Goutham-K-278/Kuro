import os
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import openwakeword
import scipy.io.wavfile as wav
from tqdm import tqdm

# Simple FCN Head for OpenWakeWord
class WakeWordHead(nn.Module):
    def __init__(self, input_dim=1536): # 16 frames * 96 embedding dim
        super(WakeWordHead, self). __init__()
        self.model = nn.Sequential(
            nn.Flatten(),
            nn.Linear(input_dim, 128),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Linear(64, 1),
            nn.Sigmoid()
        )
    
    def forward(self, x):
        return self.model(x)

from openwakeword.utils import AudioFeatures, download_models

def extract_embeddings(folder, preprocessor):
    features = []
    if not os.path.exists(folder):
        return np.array([])
    
    files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".wav")]
    print(f"Extracting embeddings from {len(files)} files in {folder}...")
    
    for f in tqdm(files):
        try:
            sr, data = wav.read(f)
            if sr != 16000:
                continue
            
            # Use AudioFeatures.embed_clips for batch processing
            # or preprocessor for custom logic
            feats = preprocessor.embed_clips(data[None, :])
            # embed_clips returns [batch, frames, features]
            # We want to take 16 frames (1.28s) - if clip is short, it pads.
            # Usually we want the last 16 frames
            if feats.shape[1] >= 16:
                features.append(feats[0, -16:, :])
            else:
                # Pad if too short
                pad = np.zeros((16 - feats.shape[1], 96))
                features.append(np.vstack([pad, feats[0]]))
                
        except Exception as e:
            print(f"Error processing {f}: {e}")
            
    return np.array(features)

def main():
    pos_dir = "voice/train/samples_pos"
    neg_dir = "voice/train/samples_neg"
    model_path = "voice/models/kuro.onnx"
    
    # 0. Download required models
    print("Ensuring OpenWakeWord models are downloaded...")
    download_models()
    
    # 1. Initialize Preprocessor
    print("Loading OpenWakeWord AudioFeatures (Embedding model)...")
    preprocessor = AudioFeatures(inference_framework="onnx") 
    
    # 2. Extract Features
    X_pos = extract_embeddings(pos_dir, preprocessor)
    X_neg = extract_embeddings(neg_dir, preprocessor)
    
    if len(X_pos) == 0:
        print("No positive samples found! Please record voice first.")
        return

    # If negatives are few, we'll just use what we have or add random noise
    if len(X_neg) < 5:
        print("Warning: Few negative samples. Training might be biased.")
        # Add some random embeddings as negatives
        fake_neg = np.random.normal(0, 0.1, (len(X_pos), 16, 96))
        X_neg = np.vstack([X_neg, fake_neg]) if len(X_neg) > 0 else fake_neg

    print(f"Training with {len(X_pos)} positive and {len(X_neg)} negative samples.")

    # 2. Prepare Data
    X = np.vstack([X_pos, X_neg])
    y = np.array([1]*len(X_pos) + [0]*len(X_neg))
    
    X_tensor = torch.FloatTensor(X)
    y_tensor = torch.FloatTensor(y).view(-1, 1)

    # 3. Train
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = WakeWordHead().to(device)
    optimizer = optim.Adam(model.parameters(), lr=0.001)
    criterion = nn.BCELoss()

    model.train()
    for epoch in range(100):
        optimizer.zero_grad()
        outputs = model(X_tensor.to(device))
        loss = criterion(outputs, y_tensor.to(device))
        loss.backward()
        optimizer.step()
        if (epoch+1) % 20 == 0:
            print(f"Epoch {epoch+1}/100, Loss: {loss.item():.4f}")

    # 4. Export to ONNX
    print(f"Exporting model to {model_path}...")
    model.eval()
    dummy_input = torch.randn(1, 16, 96).to(device)
    torch.onnx.export(model.to("cpu"), dummy_input.to("cpu"), model_path, 
                      input_names=['input'], output_names=['output'],
                      dynamic_axes={'input': {0: 'batch_size'}, 'output': {0: 'batch_size'}})
    
    print("\nSuccess! Custom 'Kuro' model trained and exported.")

if __name__ == "__main__":
    main()
