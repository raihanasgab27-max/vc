import threading
import queue
import time
import numpy as np
import sounddevice as sd
import traceback
import os
import sys

_is_running = False
_thread = None
_in_stream = None
_out_stream = None
_last_level = 0.0

# Rolling input buffer (accumulates mic data)
_input_lock = threading.Lock()
_input_buffer = np.zeros(0, dtype=np.float32)

SAMPLE_RATE = 16000
# How many seconds of audio to accumulate before sending to RVC
# Longer = better quality but more latency
PROCESS_SECONDS = 2.0
# Overlap between consecutive chunks to avoid clicks at boundaries
OVERLAP_SECONDS = 0.3

def get_audio_devices():
    """Returns a list of input and output audio devices."""
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        inputs = []
        outputs = []
        for i, dev in enumerate(devices):
            api_name = hostapis[dev['hostapi']]['name']
            full_name = f"{dev['name']} ({api_name})"
            if "Mapper" in dev['name'] or "Primary" in dev['name']:
                continue
            if dev['max_input_channels'] > 0:
                inputs.append({"id": i, "name": full_name})
            if dev['max_output_channels'] > 0:
                outputs.append({"id": i, "name": full_name})
        return {"inputs": inputs, "outputs": outputs}
    except Exception as e:
        print(f"Error listing devices: {e}")
        return {"inputs": [], "outputs": []}

def _audio_callback_in(indata, frames, time_info, status):
    """Callback for audio input - accumulates into rolling buffer."""
    global _last_level, _input_buffer
    if status:
        print(f"Input Status: {status}")
    
    _last_level = float(np.abs(indata).max())
    
    mono = indata[:, 0].copy()
    with _input_lock:
        _input_buffer = np.concatenate((_input_buffer, mono))
        # Keep max 10 seconds to prevent memory growth
        max_samples = int(SAMPLE_RATE * 10)
        if len(_input_buffer) > max_samples:
            _input_buffer = _input_buffer[-max_samples:]


class OutputBuffer:
    """Thread-safe output audio buffer with smooth crossfade."""
    def __init__(self, sr):
        self.buffer = np.zeros(0, dtype=np.float32)
        self.lock = threading.Lock()
        self.sr = sr

    def write(self, data):
        with self.lock:
            if data.ndim > 1:
                data = data.flatten()
            # Normalize to prevent clipping
            peak = np.abs(data).max()
            if peak > 1.0:
                data = data / peak * 0.95
            self.buffer = np.concatenate((self.buffer, data))
            # Cap at 4 seconds max
            max_len = self.sr * 4
            if len(self.buffer) > max_len:
                self.buffer = self.buffer[-max_len:]

    def read(self, frames):
        with self.lock:
            if len(self.buffer) >= frames:
                out = self.buffer[:frames].copy()
                self.buffer = self.buffer[frames:]
                return out.reshape(-1, 1)
            else:
                out = np.zeros(frames, dtype=np.float32)
                n = len(self.buffer)
                if n > 0:
                    out[:n] = self.buffer
                    self.buffer = np.zeros(0, dtype=np.float32)
                return out.reshape(-1, 1)

out_buffer = None

def _robust_audio_callback_out(outdata, frames, time_info, status):
    global out_buffer
    if out_buffer is None:
        outdata.fill(0)
        return
    data = out_buffer.read(frames)
    if data.shape[1] == 1 and outdata.shape[1] == 2:
        outdata[:, 0] = data[:, 0]
        outdata[:, 1] = data[:, 0]
    else:
        outdata[:] = data


def start_stream(rvc_engine, model_name, pitch, input_device_id, output_device_id, f0method="pm"):
    global _is_running, _thread, _in_stream, _out_stream, out_buffer, _input_buffer
    
    if _is_running:
        stop_stream()
    
    print(f"Starting real-time stream. Model: {model_name}, Pitch: {pitch}, Method: {f0method}")
    
    # Reset buffers
    with _input_lock:
        _input_buffer = np.zeros(0, dtype=np.float32)
    
    try:
        # Determine output sample rate
        model_sr = rvc_engine.vc.tgt_sr if rvc_engine.vc.tgt_sr else 40000
        output_sr = model_sr
        
        try:
            sd.check_output_settings(device=output_device_id, samplerate=output_sr, channels=1)
        except:
            for fs in [48000, 44100]:
                try:
                    sd.check_output_settings(device=output_device_id, samplerate=fs, channels=1)
                    output_sr = fs
                    print(f"Output device doesn't support {model_sr}Hz, using {fs}Hz")
                    break
                except:
                    continue
        
        out_buffer = OutputBuffer(output_sr)
        print(f"Output sample rate: {output_sr}Hz")
        
        def process_loop(rvc_engine, pitch, f0method, output_sr):
            import librosa
            from scipy.io import wavfile
            import uuid
            global _is_running, _input_buffer
            
            session_id = str(uuid.uuid4())[:8]
            temp_file = os.path.join(os.path.dirname(__file__), f"_rt_tmp_{session_id}.wav")
            
            process_samples = int(SAMPLE_RATE * PROCESS_SECONDS)
            
            print(f"Processing loop started. Chunk size: {PROCESS_SECONDS}s ({process_samples} samples)")
            
            while _is_running:
                try:
                    # Wait until we have enough audio accumulated
                    with _input_lock:
                        available = len(_input_buffer)
                    
                    if available < process_samples:
                        time.sleep(0.05)
                        continue
                    
                    # Grab the chunk from buffer
                    with _input_lock:
                        chunk = _input_buffer[:process_samples].copy()
                        # Keep overlap for next chunk (smoother transitions)
                        overlap_samples = int(SAMPLE_RATE * OVERLAP_SECONDS)
                        _input_buffer = _input_buffer[process_samples - overlap_samples:]
                    
                    # Skip silence (don't waste CPU on quiet chunks)
                    if np.abs(chunk).max() < 0.005:
                        # Output silence for this duration
                        silence_len = int(output_sr * (PROCESS_SECONDS - OVERLAP_SECONDS))
                        out_buffer.write(np.zeros(silence_len, dtype=np.float32))
                        continue
                    
                    # Convert to int16 and save as wav (RVC expects file path)
                    chunk_int16 = (chunk * 32767).astype(np.int16)
                    wavfile.write(temp_file, SAMPLE_RATE, chunk_int16)
                    
                    # Run RVC inference
                    t0 = time.time()
                    res = rvc_engine.vc.vc_single(
                        sid=0,
                        input_audio_path=temp_file,
                        f0_up_key=pitch,
                        f0_file=None,
                        f0_method=f0method,
                        file_index="",
                        file_index2="",
                        index_rate=rvc_engine.index_rate,
                        filter_radius=rvc_engine.filter_radius,
                        resample_sr=0,
                        rms_mix_rate=rvc_engine.rms_mix_rate,
                        protect=rvc_engine.protect
                    )
                    elapsed = time.time() - t0
                    
                    if isinstance(res, np.ndarray):
                        audio_out = res.astype(np.float32)
                        tgt_sr = rvc_engine.vc.tgt_sr
                        
                        # Resample if needed
                        if tgt_sr != output_sr:
                            audio_out = librosa.resample(
                                audio_out, orig_sr=tgt_sr, target_sr=output_sr
                            ).astype(np.float32)
                        
                        # Apply fade-in/fade-out to prevent clicks
                        fade_len = min(512, len(audio_out) // 4)
                        if fade_len > 0:
                            fade_in = np.linspace(0, 1, fade_len, dtype=np.float32)
                            fade_out = np.linspace(1, 0, fade_len, dtype=np.float32)
                            audio_out[:fade_len] *= fade_in
                            audio_out[-fade_len:] *= fade_out
                        
                        out_buffer.write(audio_out)
                        print(f"RVC processed {PROCESS_SECONDS}s in {elapsed:.2f}s | Output: {len(audio_out)} samples")
                    else:
                        err_msg = res[0] if isinstance(res, tuple) else str(res)
                        print(f"RVC Error: {err_msg[:100]}")
                        
                except queue.Empty:
                    continue
                except Exception:
                    traceback.print_exc()
                    time.sleep(0.1)
            
            # Cleanup
            if os.path.exists(temp_file):
                try: os.remove(temp_file)
                except: pass
            print("Processing loop ended.")
        
        # Input stream at 16kHz (RVC native)
        _in_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            device=input_device_id,
            channels=1,
            dtype='float32',
            blocksize=int(SAMPLE_RATE * 0.1),  # 100ms blocks for responsive meter
            callback=_audio_callback_in
        )
        
        # Output stream
        _out_stream = sd.OutputStream(
            samplerate=output_sr,
            device=output_device_id,
            channels=1,
            dtype='float32',
            blocksize=int(output_sr * 0.1),  # 100ms blocks
            callback=_robust_audio_callback_out
        )
        
        _in_stream.start()
        _out_stream.start()
        
        _is_running = True
        _thread = threading.Thread(
            target=process_loop, 
            args=(rvc_engine, pitch, f0method, output_sr), 
            daemon=True
        )
        _thread.start()
        
        return True, f"Stream started (input→RVC→output at {output_sr}Hz)"
    except Exception as e:
        traceback.print_exc()
        stop_stream()
        return False, str(e)


def stop_stream():
    global _is_running, _thread, _in_stream, _out_stream
    print("Stopping real-time stream...")
    _is_running = False
    
    if _thread is not None:
        _thread.join(timeout=3.0)
        _thread = None
    
    if _in_stream is not None:
        try:
            _in_stream.stop()
            _in_stream.close()
        except: pass
        _in_stream = None
    
    if _out_stream is not None:
        try:
            _out_stream.stop()
            _out_stream.close()
        except: pass
        _out_stream = None
    
    print("Stream stopped.")


def get_status():
    return _is_running

def get_level():
    return _last_level

def start_input_only(input_device_id):
    """Starts a minimal stream just to monitor input levels."""
    global _is_running, _in_stream, _last_level
    if _is_running: stop_stream()
    
    for sr in [16000, 44100, 48000]:
        try:
            _last_level = 0.0
            _in_stream = sd.InputStream(
                samplerate=sr,
                device=input_device_id,
                channels=1,
                dtype='float32',
                blocksize=int(sr * 0.1),
                callback=_audio_callback_in
            )
            _in_stream.start()
            _is_running = True
            print(f"Monitoring device {input_device_id} at {sr}Hz")
            return True
        except:
            continue
    
    print(f"Failed to monitor device {input_device_id}")
    return False
