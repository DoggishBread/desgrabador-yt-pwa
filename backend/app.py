from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from google.cloud import speech, storage
import yt_dlp
import os
import traceback
from pydub import AudioSegment
import json
import uuid

def _ensure_gcp_creds():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        path = "/tmp/google-creds.json"
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(creds_json)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path

_ensure_gcp_creds()

# Rutas Frontend
FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))

app = Flask(
    __name__,
    static_folder=FRONTEND_DIR,
    static_url_path=""
)
CORS(app)

# Config
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "desgrabador-youtube-audios")

# Utilidades GCS
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

# Descarga
def descargar_audio(youtube_url):

    base = f"audio_{uuid.uuid4().hex}"
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{base}.%(ext)s",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
            "preferredquality": "192",
        }],
        "postprocessor_args": ["-ac", "1", "-ar", "16000"],
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=True)
        filename = ydl.prepare_filename(info)
        wav_filename = os.path.splitext(filename)[0] + ".wav"
    return wav_filename

def cortar_audio_desde_18s(local_path):
    audio = AudioSegment.from_file(local_path)
    audio_cortado = audio[18_000:]
    archivo_cortado = os.path.splitext(local_path)[0] + "_cortado.wav"
    audio_cortado.export(archivo_cortado, format="wav")
    return archivo_cortado

# Transcripción
def transcribir_google(gcs_uri):
    try:
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
        response = operation.result(timeout=900)  # 15 min

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

    except Exception as e:
        print("\n ERROR EN GOOGLE SPEECH")
        print("Tipo:", type(e).__name__)
        print("Mensaje:", str(e))
        print("Traceback completo:")
        traceback.print_exc()
        raise e

# Frontend
@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")

@app.route("/healthz")
def healthz():
    return {"ok": True}

# API
@app.route("/transcribir", methods=["POST"])
def transcribir():
    data = request.get_json(silent=True) or {}
    url = data.get("url")
    if not url:
        return jsonify({"error": "No se proporcionó URL"}), 400

    local_original = None
    local_cortado = None
    gcs_filename = None

    try:
        local_original = descargar_audio(url)
        local_cortado = cortar_audio_desde_18s(local_original)

        gcs_filename = os.path.basename(local_cortado)
        gcs_uri = subir_audio_a_gcs(local_cortado, GCS_BUCKET_NAME, gcs_filename)

        # Limpia disco local
        try:
            if local_original and os.path.exists(local_original):
                os.remove(local_original)
        except:
            pass
        try:
            if local_cortado and os.path.exists(local_cortado):
                os.remove(local_cortado)
        except:
            pass

        # Transcribe
        texto, timestamps = transcribir_google(gcs_uri)

        borrar_audio_gcs(gcs_filename, GCS_BUCKET_NAME)

        return jsonify({
            "transcription": texto,
            "timestamps": timestamps,
            "language": "auto"
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