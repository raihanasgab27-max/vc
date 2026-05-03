import os
import subprocess
import threading
import time
import requests
import torch

# --- CONFIGURATION ---
PORT = 5000  # Note: vc-ai-colab/app.py usually runs on 5000

def install_dependencies():
    print("[1/3] Memasang dependensi...")
    # Fix for dependency conflicts in Colab (Python 3.12)
    # Install dependencies manually to bypass faiss-cpu==1.7.3 requirement
    subprocess.run(["pip", "install", "flask", "flask-cors", "faiss-cpu", "ffmpeg-python", "praat-parselmouth", "pyworld", "numpy==1.26.4"], check=True)
    
    # Install infer_rvc_python in bypass mode
    print("Memasang infer_rvc_python (bypass mode)...")
    subprocess.run(["pip", "install", "infer_rvc_python", "--no-deps"], check=True)
    
def setup_cloudflared():
    print("[2/3] Menyiapkan Cloudflare Tunnel...")
    if not os.path.exists("cloudflared"):
        subprocess.run(["wget", "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64", "-O", "cloudflared"], check=True)
        subprocess.run(["chmod", "+x", "cloudflared"], check=True)

def run_tunnel():
    print("[3/3] Menjalankan Tunnel...")
    # Run cloudflared and capture the output to find the URL
    process = subprocess.Popen(
        ["./cloudflared", "tunnel", "--url", f"http://localhost:{PORT}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    tunnel_url = None
    for line in iter(process.stdout.readline, ""):
        if ".trycloudflare.com" in line:
            tunnel_url = "https://" + line.split("https://")[1].split()[0].strip()
            print("\n" + "="*50)
            print("  COLAB API URL READY!")
            print(f"  URL: {tunnel_url}")
            print("="*50 + "\n")
            print("Salin URL di atas dan tempelkan ke kolom 'Server API' di UI lokal.")
            break
    
    # Keep reading output so it doesn't block
    for _ in process.stdout:
        pass

if __name__ == "__main__":
    install_dependencies()
    setup_cloudflared()
    
    # Start tunnel in background
    threading.Thread(target=run_tunnel, daemon=True).start()
    
    print("\nMenunggu Tunnel siap... Setelah URL muncul, jalankan 'python app.py' di cell baru.")
    
    # Loop to keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopped.")
