import os
import time
import wave
import pyaudio
import sys

def list_devices():
    """Returns a list of available input devices."""
    p = pyaudio.PyAudio()
    info = p.get_host_api_info_by_index(0)
    num_devices = info.get('deviceCount')
    
    devices = []
    print("\nAvailable Audio Input Devices:")
    for i in range(0, num_devices):
        device_info = p.get_device_info_by_host_api_device_index(0, i)
        if device_info.get('maxInputChannels') > 0:
            devices.append((i, device_info.get('name')))
            print(f"[{i}] {device_info.get('name')}")
    
    p.terminate()
    return devices

def record_sample(filename, device_index, duration=1.5, sample_rate=16000):
    """Records a single audio sample from the microphone with a progress bar."""
    p = pyaudio.PyAudio()
    try:
        stream = p.open(format=pyaudio.paInt16,
                        channels=1,
                        rate=sample_rate,
                        input=True,
                        input_device_index=device_index,
                        frames_per_buffer=1024)
    except Exception as e:
        print(f"\nError opening microphone: {e}")
        p.terminate()
        return False

    print(f"\nRecording {os.path.basename(filename)}...")
    print("Say 'Kuro' clearly!")
    
    frames = []
    total_chunks = int(sample_rate / 1024 * duration)
    
    for i in range(total_chunks):
        data = stream.read(1024, exception_on_overflow=False)
        frames.append(data)
        # Simple progress bar
        progress = int((i + 1) / total_chunks * 20)
        bar = "[" + "#" * progress + "-" * (20 - progress) + "]"
        sys.stdout.write(f"\r{bar} Speaking Now...")
        sys.stdout.flush()

    print("\nDone.")

    stream.stop_stream()
    stream.close()
    p.terminate()

    wf = wave.open(filename, 'wb')
    wf.setnchannels(1)
    wf.setsampwidth(p.get_sample_size(pyaudio.paInt16))
    wf.setframerate(sample_rate)
    wf.writeframes(b''.join(frames))
    wf.close()
    return True

def main():
    output_dir = "voice/train/samples"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    print("--- Kuro Voice Recorder (v2.0) ---")
    
    devices = list_devices()
    if not devices:
        print("No input devices found! Check your microphone settings.")
        return

    choice = input("\nEnter Device ID to use (Default is 0): ").strip()
    device_id = int(choice) if choice.isdigit() else 0

    print(f"\nUsing device: {device_id}")
    print("This script will record 15 samples of you saying 'Kuro'.")
    print("Try to vary your tone: normal, excited, tired, quiet, etc.")
    
    for i in range(1, 16):
        input(f"\n[Enter] to record sample {i}/15...")
        filename = os.path.join(output_dir, f"user_kuro_{i}.wav")
        success = record_sample(filename, device_id)
        if not success:
            print("Recording failed. Check your microphone and try again.")
            break
        time.sleep(0.3)

    print("\nAll samples recorded successfully in", output_dir)

if __name__ == "__main__":
    main()
