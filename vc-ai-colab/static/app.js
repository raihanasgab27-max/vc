let API = localStorage.getItem("rvc_api_url") || "";

// Elements
const modelSelect = document.getElementById("modelSelect");
const btnRefresh = document.getElementById("btnRefresh");
const btnUploadModel = document.getElementById("btnUploadModel");
const modelUploadArea = document.getElementById("modelUploadArea");
const modelFileInput = document.getElementById("modelFileInput");
const modelNameInput = document.getElementById("modelNameInput");
const modelDropZone = document.getElementById("modelDropZone");

const tabUpload = document.getElementById("tabUpload");
const tabRecord = document.getElementById("tabRecord");
const tabRealtime = document.getElementById("tabRealtime");
const panelUpload = document.getElementById("panelUpload");
const panelRecord = document.getElementById("panelRecord");
const panelRealtime = document.getElementById("panelRealtime");

const audioDropZone = document.getElementById("audioDropZone");
const audioFileInput = document.getElementById("audioFileInput");
const fileBadge = document.getElementById("fileBadge");
const fileLabel = document.getElementById("fileLabel");
const btnRemoveFile = document.getElementById("btnRemoveFile");

const btnMic = document.getElementById("btnMic");
const recLabel = document.getElementById("recLabel");
const recTime = document.getElementById("recTime");

const inputDevice = document.getElementById("inputDevice");
const outputDevice = document.getElementById("outputDevice");
const btnRealtimeStart = document.getElementById("btnRealtimeStart");
const btnRealtimeStop = document.getElementById("btnRealtimeStop");
const btnRefreshDevices = document.getElementById("btnRefreshDevices");
const vuBar = document.getElementById("vuBar");

const audioPreview = document.getElementById("audioPreview");
const audioPlayer = document.getElementById("audioPlayer");

const pitchSlider = document.getElementById("pitchSlider");
const pitchVal = document.getElementById("pitchVal");
const f0method = document.getElementById("f0method");
const outputFormat = document.getElementById("outputFormat");

const btnConvert = document.getElementById("btnConvert");
const convertLabel = document.getElementById("convertLabel");
const resultSection = document.getElementById("resultSection");
const resultPlayer = document.getElementById("resultPlayer");
const btnDownload = document.getElementById("btnDownload");

const statusBar = document.getElementById("statusBar");
const statusText = document.getElementById("statusText");

const apiUrlInput = document.getElementById("apiUrlInput");
const btnSaveApi = document.getElementById("btnSaveApi");
const serverStatus = document.getElementById("serverStatus");
const statusLabel = document.getElementById("statusLabel");

// State
let audioFile = null;
let mediaRecorder = null;
let recordedChunks = [];
let isRecording = false;
let recInterval = null;
let recSeconds = 0;

// ── API Configuration ──────────────────────

function initApi() {
    if (apiUrlInput) {
        apiUrlInput.value = API;
        updateStatus();
    }
    loadModels();
}

function updateStatus(isConnecting = false) {
    if (!serverStatus) return;
    serverStatus.classList.remove("status-local", "status-remote", "status-error");
    
    if (isConnecting) {
        statusLabel.textContent = "Connecting...";
        return;
    }

    if (!API) {
        serverStatus.classList.add("status-local");
        statusLabel.textContent = "Local";
    } else {
        serverStatus.classList.add("status-remote");
        statusLabel.textContent = "Remote";
    }
}

if (btnSaveApi) {
    btnSaveApi.addEventListener("click", async () => {
        let url = apiUrlInput.value.trim();
        if (url && !url.startsWith("http")) url = "https://" + url;
        if (url.endsWith("/")) url = url.slice(0, -1);
        
        API = url;
        localStorage.setItem("rvc_api_url", API);
        
        updateStatus(true);
        const ok = await testConnection();
        if (ok) {
            updateStatus();
            loadModels();
        } else {
            serverStatus.classList.add("status-error");
            statusLabel.textContent = "Offline";
        }
    });
}

async function testConnection() {
    try {
        const res = await fetch(`${API}/api/models`, { signal: AbortSignal.timeout(5000) });
        return res.ok;
    } catch (e) {
        return false;
    }
}

// ── Models ─────────────────────────────────

async function loadModels() {
    try {
        const res = await fetch(`${API}/api/models`);
        const models = await res.json();
        modelSelect.innerHTML = '<option value="">-- Pilih Model --</option>';
        models.forEach(m => {
            const opt = document.createElement("option");
            opt.value = m.name;
            opt.textContent = m.name + (m.index ? " (+ index)" : "");
            modelSelect.appendChild(opt);
        });
    } catch (e) {
        console.error("Failed to load models:", e);
        if (API && serverStatus) {
            serverStatus.classList.add("status-error");
            statusLabel.textContent = "Offline";
        }
    }
    updateConvertBtn();
}

btnRefresh.addEventListener("click", loadModels);

btnUploadModel.addEventListener("click", () => {
    modelUploadArea.classList.toggle("hidden");
});

modelDropZone.addEventListener("click", () => modelFileInput.click());
modelDropZone.addEventListener("dragover", e => { e.preventDefault(); modelDropZone.classList.add("drag-over"); });
modelDropZone.addEventListener("dragleave", () => modelDropZone.classList.remove("drag-over"));
modelDropZone.addEventListener("drop", e => {
    e.preventDefault();
    modelDropZone.classList.remove("drag-over");
    if (e.dataTransfer.files.length) uploadModel(e.dataTransfer.files[0]);
});
modelFileInput.addEventListener("change", () => {
    if (modelFileInput.files[0]) uploadModel(modelFileInput.files[0]);
});

async function uploadModel(file) {
    const name = modelNameInput.value.trim() || file.name.replace(/\.[^.]+$/, "");
    showStatus("Uploading model...");
    try {
        const fd = new FormData();
        fd.append("model", file);
        fd.append("name", name);
        const res = await fetch(`${API}/api/upload-model`, { method: "POST", body: fd });
        const data = await res.json();
        if (data.success) {
            modelUploadArea.classList.add("hidden");
            modelNameInput.value = "";
            modelFileInput.value = "";
            await loadModels();
        } else {
            alert("Error: " + (data.error || "Upload failed"));
        }
    } catch (e) {
        alert("Upload error: " + e.message);
    }
    hideStatus();
}

// ── Tabs ───────────────────────────────────

function switchTab(tabId) {
    [tabUpload, tabRecord, tabRealtime].forEach(t => t.classList.remove("active"));
    [panelUpload, panelRecord, panelRealtime].forEach(p => p.classList.remove("active"));
    
    if(tabId === "upload") {
        tabUpload.classList.add("active");
        panelUpload.classList.add("active");
        btnConvert.style.display = "block";
    } else if (tabId === "record") {
        tabRecord.classList.add("active");
        panelRecord.classList.add("active");
        btnConvert.style.display = "block";
    } else {
        tabRealtime.classList.add("active");
        panelRealtime.classList.add("active");
        btnConvert.style.display = "none";
        loadDevices();
    }
}

tabUpload.addEventListener("click", () => switchTab("upload"));
tabRecord.addEventListener("click", () => switchTab("record"));
tabRealtime.addEventListener("click", () => switchTab("realtime"));

// ── Real-Time Status Polling ────────────────
let statusInterval = null;

function startStatusPolling() {
    if (statusInterval) return;
    statusInterval = setInterval(async () => {
        try {
            const res = await fetch(`${API}/api/realtime/status`);
            const data = await res.json();
            
            // Update VU Meter
            if (data.level !== undefined) {
                // High sensitivity for debugging (0.0 to 1.0)
                let percent = Math.min(100, data.level * 1000); 
                vuBar.style.width = percent + "%";
                if (data.level > 0.001) {
                    console.log("Mic Level:", data.level);
                }
            }
        } catch(e) {}
    }, 100);
}

function stopStatusPolling() {
    clearInterval(statusInterval);
    statusInterval = null;
}

// Monitor level when input device changes
inputDevice.addEventListener("change", async () => {
    monitorMic();
});

async function monitorMic() {
    if (inputDevice.value) {
        await fetch(`${API}/api/realtime/monitor`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ input_device: inputDevice.value })
        });
        startStatusPolling();
    }
}

// ── Real-Time ──────────────────────────────
async function loadDevices(force = false) {
    if (!force && inputDevice.options.length > 1) return; // already loaded
    try {
        const res = await fetch(`${API}/api/devices`);
        const devices = await res.json();
        
        inputDevice.innerHTML = '';
        outputDevice.innerHTML = '';
        
        if (devices.inputs.length === 0) {
            inputDevice.innerHTML = '<option value="">-- No Input Devices Found --</option>';
        }
        
        devices.inputs.forEach(d => {
            const opt = document.createElement("option");
            opt.value = d.id;
            opt.textContent = d.name;
            inputDevice.appendChild(opt);
        });
        
        devices.outputs.forEach(d => {
            const opt = document.createElement("option");
            opt.value = d.id;
            opt.textContent = d.name;
            outputDevice.appendChild(opt);
        });

        // Automatically start monitoring the first device
        monitorMic();
    } catch(e) {
        console.error("Failed to load devices", e);
    }
}

btnRefreshDevices.addEventListener("click", () => loadDevices(true));

btnRealtimeStart.addEventListener("click", async () => {
    if (!modelSelect.value) {
        alert("Pilih model terlebih dahulu!");
        return;
    }
    
    btnRealtimeStart.disabled = true;
    showStatus("Memulai Real-Time Stream...");
    
    try {
        const res = await fetch(`${API}/api/realtime/start`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                model: modelSelect.value,
                pitch: pitchSlider.value,
                f0method: f0method.value,
                input_device: parseInt(inputDevice.value),
                output_device: parseInt(outputDevice.value)
            })
        });
        
        const data = await res.json();
        if (data.success) {
            btnRealtimeStart.classList.add("hidden");
            btnRealtimeStop.classList.remove("hidden");
            showStatus("Real-Time aktif! Bicaralah ke mic.");
        } else {
            alert("Error: " + data.error);
            hideStatus();
        }
    } catch(e) {
        alert("Error: " + e.message);
        hideStatus();
    }
    btnRealtimeStart.disabled = false;
});

btnRealtimeStop.addEventListener("click", async () => {
    btnRealtimeStop.disabled = true;
    try {
        await fetch(`${API}/api/realtime/stop`, {method: "POST"});
        btnRealtimeStart.classList.remove("hidden");
        btnRealtimeStop.classList.add("hidden");
        hideStatus();
    } catch(e) {
        console.error(e);
    }
    btnRealtimeStop.disabled = false;
});

// ── Audio File Upload ──────────────────────

audioDropZone.addEventListener("click", () => audioFileInput.click());
audioDropZone.addEventListener("dragover", e => { e.preventDefault(); audioDropZone.classList.add("drag-over"); });
audioDropZone.addEventListener("dragleave", () => audioDropZone.classList.remove("drag-over"));
audioDropZone.addEventListener("drop", e => {
    e.preventDefault();
    audioDropZone.classList.remove("drag-over");
    if (e.dataTransfer.files.length) setAudioFile(e.dataTransfer.files[0]);
});
audioFileInput.addEventListener("change", () => {
    if (audioFileInput.files[0]) setAudioFile(audioFileInput.files[0]);
});

function setAudioFile(file) {
    audioFile = file;
    fileLabel.textContent = file.name;
    fileBadge.classList.remove("hidden");
    audioDropZone.style.display = "none";

    const url = URL.createObjectURL(file);
    audioPlayer.src = url;
    audioPreview.classList.remove("hidden");
    updateConvertBtn();
}

btnRemoveFile.addEventListener("click", () => {
    audioFile = null;
    fileBadge.classList.add("hidden");
    audioDropZone.style.display = "";
    audioFileInput.value = "";
    audioPreview.classList.add("hidden");
    audioPlayer.src = "";
    updateConvertBtn();
});

// ── Recording ──────────────────────────────

btnMic.addEventListener("click", async () => {
    if (isRecording) {
        stopRecording();
    } else {
        await startRecording();
    }
});

async function startRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        recordedChunks = [];
        mediaRecorder = new MediaRecorder(stream);
        mediaRecorder.ondataavailable = e => { if (e.data.size > 0) recordedChunks.push(e.data); };
        mediaRecorder.onstop = () => {
            stream.getTracks().forEach(t => t.stop());
            const blob = new Blob(recordedChunks, { type: "audio/webm" });
            const file = new File([blob], "recording.webm", { type: "audio/webm" });
            setAudioFile(file);
            // Switch to upload tab to show file
            tabUpload.click();
        };
        mediaRecorder.start();
        isRecording = true;
        btnMic.classList.add("recording");
        recLabel.textContent = "Stop";
        recTime.classList.remove("hidden");
        recSeconds = 0;
        recTime.textContent = "00:00";
        recInterval = setInterval(() => {
            recSeconds++;
            const m = String(Math.floor(recSeconds / 60)).padStart(2, "0");
            const s = String(recSeconds % 60).padStart(2, "0");
            recTime.textContent = `${m}:${s}`;
        }, 1000);
    } catch (e) {
        alert("Gagal akses mikrofon: " + e.message);
    }
}

function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
        mediaRecorder.stop();
    }
    isRecording = false;
    btnMic.classList.remove("recording");
    recLabel.textContent = "Mulai Rekam";
    recTime.classList.add("hidden");
    clearInterval(recInterval);
}

// ── Parameters ─────────────────────────────

pitchSlider.addEventListener("input", () => {
    pitchVal.textContent = pitchSlider.value;
});

// ── Convert ────────────────────────────────

function updateConvertBtn() {
    btnConvert.disabled = !(audioFile && modelSelect.value);
}

modelSelect.addEventListener("change", updateConvertBtn);

btnConvert.addEventListener("click", async () => {
    if (!audioFile || !modelSelect.value) return;

    showStatus("Mengkonversi suara... Ini bisa makan waktu beberapa detik.");
    btnConvert.disabled = true;
    convertLabel.textContent = "Memproses...";
    resultSection.classList.add("hidden");

    try {
        const fd = new FormData();
        fd.append("audio", audioFile);
        fd.append("model", modelSelect.value);
        fd.append("pitch", pitchSlider.value);
        fd.append("f0method", f0method.value);
        fd.append("format", outputFormat.value);

        const res = await fetch(`${API}/api/convert`, { method: "POST", body: fd });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || "Conversion failed");
        }

        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        resultPlayer.src = url;
        btnDownload.href = url;
        btnDownload.download = `converted_${Date.now()}.${outputFormat.value}`;
        resultSection.classList.remove("hidden");
    } catch (e) {
        alert("Error: " + e.message);
    }

    hideStatus();
    btnConvert.disabled = false;
    convertLabel.textContent = "Konversi Suara";
    updateConvertBtn();
});

// ── Status ─────────────────────────────────

function showStatus(msg) {
    statusText.textContent = msg;
    statusBar.classList.remove("hidden");
}

function hideStatus() {
    statusBar.classList.add("hidden");
}

// ── Init ───────────────────────────────────

initApi();
