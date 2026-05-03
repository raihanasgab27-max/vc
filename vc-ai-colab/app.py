import os
import uuid
import torch
import gc
import subprocess

# Patch torch.load to bypass PyTorch 2.6+ weights_only restriction
_original_load = torch.load
def _patched_load(*args, **kwargs):
    if 'weights_only' not in kwargs:
        kwargs['weights_only'] = False
    return _original_load(*args, **kwargs)
torch.load = _patched_load

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from infer_rvc_python import BaseLoader

# ==============================================================================
# MONKEY PATCH: Caching model data inside BaseLoader to prevent re-loading .pth
# ==============================================================================
_orig_call = BaseLoader.__call__

def efficient_call(self, audio_files=[], tag_list=[], overwrite=False, parallel_workers=1, type_output=None):
    if isinstance(audio_files, str): audio_files = [audio_files]
    if isinstance(tag_list, str): tag_list = [tag_list]
    
    if not audio_files: return []
    id_tag = tag_list[0] if tag_list else list(self.model_config.keys())[-1]
    input_audio_path = audio_files[0]
    
    params = self.model_config.get(id_tag)
    if not params: return []

    self.model_config[id_tag]["result"] = []
    self.output_list = []

    if not self.hu_bert_model:
        from infer_rvc_python.main import load_hu_bert
        print("[CACHE] Loading HuBERT base model...")
        self.hu_bert_model = load_hu_bert(self.config, self.hubert_path)

    if not hasattr(self, '_cache'): self._cache = {}
    
    cache = self._cache.get(id_tag)
    model_path = params["file_model"]
    
    if not cache or cache['path'] != model_path:
        print(f"[CACHE] Loading model into memory: {id_tag}...")
        from infer_rvc_python.main import load_trained_model
        import faiss
        import numpy as np

        (n_spk, tgt_sr, net_g, pipe, cpt, version) = load_trained_model(model_path, self.config)
        if_f0 = cpt.get("f0", 1)
        
        index_rate = params["index_influence"]
        file_index = params["file_index"]
        index = big_npy = None
        if os.path.exists(file_index) and index_rate != 0:
            try:
                index = faiss.read_index(file_index)
                big_npy = index.reconstruct_n(0, index.ntotal)
            except: pass
            
        if "rmvpe" in params["pitch_algo"] and not self.model_pitch_estimator:
            from infer_rvc_python.lib.rmvpe import RMVPE
            print("[CACHE] Loading RMVPE estimator...")
            self.model_pitch_estimator = RMVPE(self.rmvpe_path or "rmvpe.pt", is_half=self.config.is_half, device=self.config.device)
        
        if self.model_pitch_estimator:
            pipe.model_rmvpe = self.model_pitch_estimator
            
        self._cache[id_tag] = {
            'path': model_path,
            'vars': (n_spk, tgt_sr, net_g, pipe, cpt, version, if_f0, index, big_npy)
        }
        cache = self._cache[id_tag]

    (n_spk, tgt_sr, net_g, pipe, cpt, version, if_f0, index, big_npy) = cache['vars']
    index_rate = params["index_influence"]

    self.infer(
        id_tag, params, n_spk, tgt_sr, net_g, pipe, cpt, version, if_f0,
        index_rate, index, big_npy, None, input_audio_path, overwrite, type_output
    )
    
    return self.model_config[id_tag].get("result", [])

BaseLoader.__call__ = efficient_call
# ==============================================================================

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
MODELS_DIR = os.path.join(os.path.dirname(__file__), "rvc_models")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)

# Initialize RVC Converter
converter = BaseLoader(only_cpu=False, hubert_path=None, rmvpe_path=None)

def list_models():
    """Scan rvc_models directory for available models."""
    models = []
    if not os.path.isdir(MODELS_DIR):
        return models
    for name in os.listdir(MODELS_DIR):
        model_dir = os.path.join(MODELS_DIR, name)
        if os.path.isdir(model_dir):
            pth_files = [f for f in os.listdir(model_dir) if f.endswith(".pth")]
            if pth_files:
                index_files = [f for f in os.listdir(model_dir) if f.endswith(".index")]
                models.append({
                    "name": name,
                    "pth": os.path.join(model_dir, pth_files[0]),
                    "index": os.path.join(model_dir, index_files[0]) if index_files else ""
                })
    return models


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/models", methods=["GET"])
def api_models():
    """Return list of available voice models."""
    models = list_models()
    return jsonify([{"name": m["name"], "index": bool(m["index"])} for m in models])


@app.route("/api/convert", methods=["POST"])
def api_convert():
    """Convert uploaded audio using selected RVC model."""
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio_file = request.files["audio"]
    model_name = request.form.get("model", "")
    pitch = int(request.form.get("pitch", 0))
    f0method = request.form.get("f0method", "rmvpe")
    out_format = request.form.get("format", "mp3").lower()

    if not model_name:
        return jsonify({"error": "No model selected"}), 400

    models = list_models()
    model_info = next((m for m in models if m["name"] == model_name), None)
    if not model_info:
        return jsonify({"error": f"Model '{model_name}' not found"}), 404

    # Save uploaded file
    file_id = str(uuid.uuid4())[:8]
    ext = os.path.splitext(audio_file.filename)[1] or ".wav"
    input_path = os.path.join(UPLOAD_DIR, f"{file_id}_input{ext}")
    output_path_wav = os.path.join(OUTPUT_DIR, f"{file_id}_output.wav")
    final_output_path = output_path_wav
    mimetype = "audio/wav"

    if out_format == "mp3":
        final_output_path = os.path.join(OUTPUT_DIR, f"{file_id}_output.mp3")
        mimetype = "audio/mpeg"
        
    audio_file.save(input_path)

    try:
        converter.apply_conf(
            tag=model_name,
            file_model=model_info["pth"],
            pitch_algo=f0method,
            pitch_lvl=pitch,
            file_index=model_info["index"],
            index_influence=1.0,
            respiration_median_filtering=3,
            envelope_ratio=0.25,
            consonant_breath_protection=0.33
        )

        result_path = converter(
            audio_files=[input_path],
            tag_list=[model_name],
            overwrite=True,
            type_output="wav"
        )

        if not result_path or not os.path.exists(result_path[0] if isinstance(result_path, list) else result_path):
            raise Exception("Konversi gagal, file output tidak ditemukan.")

        final_wav = result_path[0] if isinstance(result_path, list) else result_path
        
        # Convert if needed
        if out_format == "mp3":
            subprocess.run(["ffmpeg", "-i", final_wav, "-vn", "-ar", "44100", "-ac", "2", "-b:a", "192k", "-y", final_output_path], 
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        return send_file(final_output_path, mimetype=mimetype, as_attachment=True, download_name=f"converted_{file_id}.{out_format}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            if os.path.exists(input_path):
                os.remove(input_path)
            if out_format == "mp3" and os.path.exists(output_path_wav):
                os.remove(output_path_wav)
        except:
            pass


@app.route("/api/upload-model", methods=["POST"])
def api_upload_model():
    """Upload a new voice model (.pth file)."""
    if "model" not in request.files:
        return jsonify({"error": "No model file provided"}), 400

    model_file = request.files["model"]
    model_name = request.form.get("name", "")

    if not model_name:
        model_name = os.path.splitext(model_file.filename)[0]

    model_name = "".join(c for c in model_name if c.isalnum() or c in "-_ ").strip()
    if not model_name:
        return jsonify({"error": "Invalid model name"}), 400

    model_dir = os.path.join(MODELS_DIR, model_name)
    os.makedirs(model_dir, exist_ok=True)

    save_path = os.path.join(model_dir, model_file.filename)
    model_file.save(save_path)

    return jsonify({"success": True, "name": model_name})


# Realtime Routes (Disabled in Colab)
@app.route("/api/devices", methods=["GET"])
def api_devices():
    return jsonify({"inputs": [], "outputs": []})

@app.route("/api/realtime/start", methods=["POST"])
def api_realtime_start():
    return jsonify({"error": "Real-Time is not available in Google Colab"}), 400

@app.route("/api/realtime/stop", methods=["POST"])
def api_realtime_stop():
    return jsonify({"success": True})

@app.route("/api/realtime/status", methods=["GET"])
def api_realtime_status():
    return jsonify({"is_running": False, "level": 0})

@app.route("/api/realtime/monitor", methods=["POST"])
def api_realtime_monitor():
    return jsonify({"success": False})


if __name__ == "__main__":
    print("=" * 40)
    print("  VoiceAI Colab Backend Ready!")
    print("  http://localhost:5000")
    print("=" * 40)

    models = list_models()
    if models:
        print(f"\n  Models found: {len(models)}")
        for m in models:
            print(f"    • {m['name']}")
    else:
        print(f"\n  [WARNING] No models found in: {MODELS_DIR}")

    print()
    app.run(host="0.0.0.0", port=5000, debug=False)
