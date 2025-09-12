from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from google.cloud import speech, storage
import yt_dlp
import os
import traceback
import re
import uuid
from pydub import AudioSegment

def _ensure_gcp_creds():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        path = "/tmp/google-creds.json"
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(creds_json)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path

_ensure_gcp_creds()

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))

app = Flask(
    __name__,
    static_folder=FRONTEND_DIR,
    static_url_path=""
)
CORS(app)

GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "desgrabador-youtube-audios")

# Subtítulos
def clean_srt_to_text(srt_content: str) -> str:
    lines = []
    for block in re.split(r"\n\s*\n", srt_content.strip()):
        parts = block.strip().splitlines()
        if not parts:
            continue
        if re.fullmatch(r"\d+", parts[0].strip()):
            parts = parts[1:]
        if parts and re.match(r"\d{2}:\d{2}:\d{2},\d{3}", parts[0]):
            parts = parts[1:]
        text = " ".join(parts)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)

def descargar_subtitulos(youtube_url, lang="es"):
    base = f"subs_{uuid.uuid4().hex}"
    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [lang],
        "subtitlesformat": "srt",
        "outtmpl": f"{base}.%(ext)s",
        "quiet": True,
        "no_warnings": True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(youtube_url, download=True)
    srt_file = f"{base}.srt"
    if not os.path.exists(srt_file):
        return None
    with open(srt_file, "r", encoding="utf-8", errors="ignore") as f:
        srt_content = f.read()
    os.remove(srt_file)
    return clean_srt_to_text(srt_content)

# Audio
def descargar_audio(youtube_url):
    base = f"audio_{uuid.uuid4().hex}"
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{base}.%(ext)s",
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "wav", "preferredquality": "192"},
        ],
        "postprocessor_args": ["-ac", "1", "-ar", "16000"],
        "noplaylist": True,
        "retries": 8,
        "fragment_retries": 8,
        "concurrent_fragments": 1,
        "forceipv4": True,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "geo_bypass_country": "US",
        "extractor_args": {"youtube": {"player_client": ["android"]}},
        "http_headers": {
            "User-Agent": "com.google.android.youtube/19.26.33 (Linux; U; Android 13)",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=True)
        filename = ydl.prepare_filename(info)
        wav_filename = os.path.splitext(filename)[0] + ".wav"
    return wav_filename

def cortar_audio_desde_18s(ruta_original):
    audio = AudioSegment.from_wav(ruta_original)
    recorte = audio[18000:]
    ruta_recortada = ruta_original.replace(".wav", "_recortado.wav")
    recorte.export(ruta_recortada, format="wav")
    return ruta_recortada

# GCS
def subir_audio_a_gcs(local_path, bucket_name, gcs_filename):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(gcs_filename)
    blob.upload_from_filename(local_path)
    return f"gs://{bucket_name}/{gcs_filename}"

def borrar_audio_gcs(gcs_filename, bucket_name):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(gcs_filename)
    if blob.exists():
        blob.delete()

# Google Speech
def transcribir_google(gcs_uri):
    client = speech.SpeechClient()
    audio = speech.RecognitionAudio(uri=gcs_uri)
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=16000,
        language_code="es-419",
        alternative_language_codes=["es-ES", "en-US", "en-GB", "pt-BR", "fr-FR"],
        enable_word_time_offsets=True,
        enable_automatic_punctuation=True,
        model="latest_long"
    )
    operation = client.long_running_recognize(config=config, audio=audio)
    print("Esperando que Google termine la transcripción...")
    response = operation.result(timeout=900)

    texto_completo = " ".join(
        [result.alternatives[0].transcript for result in response.results]
    )

    palabras_con_tiempos = []
    for result in response.results:
        if not result.alternatives:
            continue
        alternative = result.alternatives[0]
        for word_info in getattr(alternative, "words", []):
            palabras_con_tiempos.append({
                "word": word_info.word,
                "start_time": word_info.start_time.total_seconds(),
                "end_time": word_info.end_time.total_seconds()
            })

    return texto_completo, palabras_con_tiempos

# Rutas
@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")

@app.route("/healthz")
def healthz():
    return {"ok": True}

@app.route("/transcribir", methods=["POST"])
def transcribir():
    data = request.get_json(silent=True) or {}
    url = data.get("url")
    lang = data.get("lang", "es")
    if not url:
        return jsonify({"error": "No se proporcionó URL"}), 400

    # Intentar subtítulos primero
    try:
        texto_subs = descargar_subtitulos(url, lang)
        if texto_subs:
            return jsonify({
                "transcription": texto_subs,
                "timestamps": [],
                "language": lang,
                "source": "subtitles"
            })
    except Exception as e:
        print("No se pudieron obtener subtítulos:", e)

    # Si no hay subtítulos, usar audio + Google Cloud
    local_original = None
    local_cortado = None
    gcs_filename = None

    try:
        local_original = descargar_audio(url)
        local_cortado = cortar_audio_desde_18s(local_original)

        gcs_filename = os.path.basename(local_cortado)
        gcs_uri = subir_audio_a_gcs(local_cortado, GCS_BUCKET_NAME, gcs_filename)

        for f in [local_original, local_cortado]:
            try:
                if f and os.path.exists(f):
                    os.remove(f)
            except:
                pass

        texto, timestamps = transcribir_google(gcs_uri)
        borrar_audio_gcs(gcs_filename, GCS_BUCKET_NAME)

        return jsonify({
            "transcription": texto,
            "timestamps": timestamps,
            "language": "auto",
            "source": "audio"
        })

    except Exception as e:
        try:
            if gcs_filename:
                borrar_audio_gcs(gcs_filename, GCS_BUCKET_NAME)
        except:
            pass
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
