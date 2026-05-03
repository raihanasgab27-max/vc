import os
import uuid
import base64
import torch
import subprocess
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import realtime

# Fix for PyTorch 2.6+ weights_only loading issue with fairseq
try:
    import fairseq
    from fairseq.data.dictionary import Dictionary
    if hasattr(torch.serialization, 'add_safe_globals'):
        torch.serialization.add_safe_globals([Dictionary])
except Exception:
    pass

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
MODELS_DIR = os.path.join(os.path.dirname(__file__), "rvc_models")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)

# RVC engine (lazy init)
rvc_engine = None
current_model = None


def get_rvc():
    """Lazy-load RVC engine."""
    global rvc_engine
    if rvc_engine is None:
        from rvc_python.infer import RVCInference
        rvc_engine = RVCInference(device="cpu")
    return rvc_engine


def list_models():
    """Scan rvc_models directory for available models."""
    models = []
    if not os.path.isdir(MODELS_DIR):
        return models
    for name in os.listdir(MODELS_DIR):
        model_dir = os.path.join(MODELS_DIR, name)
        if os.path.isdir(model_dir):
            # Look for .pth file
            pth_files = [f for f in os.listdir(model_dir) if f.endswith(".pth")]
            if pth_files:
                index_files = [f for f in os.listdir(model_dir) if f.endswith(".index")]
                models.append({
                    "name": name,
                    "pth": pth_files[0],
                    "index": index_files[0] if index_files else None,
                })
    return models


# ── Routes ──────────────────────────────────────────────


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/models", methods=["GET"])
def api_models():
    """Return list of available voice models."""
    return jsonify(list_models())


@app.route("/api/convert", methods=["POST"])
def api_convert():
    """Convert uploaded audio using selected RVC model."""
    global current_model

    # Check for audio file
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio_file = request.files["audio"]
    model_name = request.form.get("model", "")
    pitch = int(request.form.get("pitch", 0))
    f0method = request.form.get("f0method", "rmvpe")
    out_format = request.form.get("format", "mp3").lower()

    if not model_name:
        return jsonify({"error": "No model selected"}), 400

    # Find model
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
        rvc = get_rvc()

        # Load model if different from current
        model_pth = os.path.join(MODELS_DIR, model_name, model_info["pth"])
        if current_model != model_name:
            index_path = ""
            if model_info["index"]:
                index_path = os.path.join(MODELS_DIR, model_name, model_info["index"])
            rvc.load_model(model_pth, index_path=index_path if index_path else None)
            current_model = model_name

        # Set parameters
        rvc.set_params(
            f0method=f0method,
            f0up_key=pitch,
        )

        # Run conversion
        rvc.infer_file(input_path, output_path_wav)

        # Convert if needed
        if out_format == "mp3":
            subprocess.run(["ffmpeg", "-i", output_path_wav, "-vn", "-ar", "44100", "-ac", "2", "-b:a", "192k", "-y", final_output_path], 
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Return converted file
        return send_file(final_output_path, mimetype=mimetype, as_attachment=True,
                         download_name=f"converted_{file_id}.{out_format}")

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        # Cleanup
        for path in [input_path, output_path_wav]:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        # Note: We don't remove final_output_path here because send_file might need it, 
        # but in a real app we'd use a background task or auto-cleanup. 
        # However, for simplicity we'll leave it or the user can delete it.


@app.route("/api/upload-model", methods=["POST"])
def api_upload_model():
    """Upload a new voice model (.pth file)."""
    if "model" not in request.files:
        return jsonify({"error": "No model file provided"}), 400

    model_file = request.files["model"]
    model_name = request.form.get("name", "")

    if not model_name:
        model_name = os.path.splitext(model_file.filename)[0]

    # Sanitize name
    model_name = "".join(c for c in model_name if c.isalnum() or c in "-_ ").strip()
    if not model_name:
        return jsonify({"error": "Invalid model name"}), 400

    model_dir = os.path.join(MODELS_DIR, model_name)
    os.makedirs(model_dir, exist_ok=True)

    save_path = os.path.join(model_dir, model_file.filename)
    model_file.save(save_path)

    return jsonify({"success": True, "name": model_name})


@app.route("/api/devices", methods=["GET"])
def api_devices():
    """Return available audio input and output devices."""
    try:
        return jsonify(realtime.get_audio_devices())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/realtime/start", methods=["POST"])
def api_realtime_start():
    global current_model
    data = request.json
    model_name = data.get("model", "")
    pitch = int(data.get("pitch", 0))
    f0method = data.get("f0method", "rmvpe")
    input_device = data.get("input_device")
    output_device = data.get("output_device")

    if not model_name:
        return jsonify({"error": "No model selected"}), 400

    models = list_models()
    model_info = next((m for m in models if m["name"] == model_name), None)
    if not model_info:
        return jsonify({"error": f"Model '{model_name}' not found"}), 404

    try:
        rvc = get_rvc()
        model_pth = os.path.join(MODELS_DIR, model_name, model_info["pth"])
        
        if current_model != model_name:
            index_path = ""
            if model_info["index"]:
                index_path = os.path.join(MODELS_DIR, model_name, model_info["index"])
            rvc.load_model(model_pth, index_path=index_path if index_path else None)
            current_model = model_name

        success, msg = realtime.start_stream(
            rvc_engine=rvc,
            model_name=model_name,
            pitch=pitch,
            input_device_id=input_device,
            output_device_id=output_device,
            f0method=f0method
        )
        if success:
            return jsonify({"success": True, "msg": msg})
        else:
            return jsonify({"error": msg}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/realtime/stop", methods=["POST"])
def api_realtime_stop():
    realtime.stop_stream()
    return jsonify({"success": True, "msg": "Stream stopped"})


@app.route("/api/realtime/status", methods=["GET"])
def api_realtime_status():
    return jsonify({
        "is_running": realtime.get_status(),
        "level": realtime.get_level()
    })

@app.route("/api/realtime/monitor", methods=["POST"])
def api_realtime_monitor():
    data = request.json
    device_id = data.get("input_device")
    if device_id is None:
        return jsonify({"error": "No device ID"}), 400
    
    # Start a minimal stream just for monitoring levels
    success = realtime.start_input_only(int(device_id))
    return jsonify({"success": success})

if __name__ == "__main__":
    print("=" * 40)
    print("  VoiceAI — AI Voice Changer")
    print("  http://localhost:5000")
    print("=" * 40)

    models = list_models()
    if models:
        print(f"\n  Models found: {len(models)}")
        for m in models:
            print(f"    • {m['name']}")
    else:
        print(f"\n  No models found in: {MODELS_DIR}")
        print("  Add .pth model files to rvc_models/<name>/ directory")

    print()
    app.run(host="0.0.0.0", port=5000, debug=False)
