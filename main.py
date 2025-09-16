import os
import io
from fastapi import FastAPI, Request
from reportlab.pdfgen import canvas

# Конфиг из ENV
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
SPEECH_LOCATION = os.getenv("SPEECH_LOCATION", "global")
PDF_BUCKET = os.getenv("PDF_BUCKET")
LANG = os.getenv("LANG", "ru-RU")
SPEAKER_COUNT = int(os.getenv("SPEAKER_COUNT", "2"))
GENAI_MODEL = os.getenv("GENAI_MODEL", "gemini-2.5-flash")  # через google-genai

app = FastAPI()

@app.get("/")
def health():
    return {"status": "ok"}  # Cloud Run healthcheck

def get_storage_client():
    from google.cloud import storage
    return storage.Client()

def get_speech_client():
    from google.cloud import speech_v2
    return speech_v2.SpeechClient()

def get_genai_client():
    # Инициализируем клиента только когда нужен
    from google import genai
    return genai.Client()  # использует GOOGLE_GENAI_USE_VERTEXAI и проект/локацию из ENV

def transcribe_gcs_uri(gcs_uri: str) -> str:
    from google.cloud.speech_v2.types import cloud_speech
    speech_client = get_speech_client()

    req = cloud_speech.BatchRecognizeRequest(
        recognizer=f"projects/{PROJECT_ID}/locations/{SPEECH_LOCATION}/recognizers/_",
        config=cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=[LANG],
            model="latest_long",
            features=cloud_speech.RecognitionFeatures(
                enable_automatic_punctuation=True,
                enable_spoken_punctuation=True,
                enable_word_time_offsets=True,
                enable_speaker_diarization=True,
                diarization_speaker_count=SPEAKER_COUNT,
            ),
        ),
        files=[cloud_speech.BatchRecognizeFileMetadata(uri=gcs_uri)],
        recognition_output_config=cloud_speech.RecognitionOutputConfig(
            inline_response_config=cloud_speech.InlineOutputConfig()
        ),
    )
    op = speech_client.batch_recognize(request=req)
    resp = op.result(timeout=3600)

    # Склейка текста
    lines = []
    for file_result in resp.results:
        for r in file_result.transcript.results:
            if r.alternatives:
                t = r.alternatives[0].transcript.strip()
                if t:
                    lines.append(t)
    return "\n".join(lines)

def summarize(text: str, extra_prompt: str = "") -> str:
    client = get_genai_client()
    prompt = f"""Ты помощник по встречам.
Нужно выдать:
1) Краткое резюме (3–5 предложений)
2) Решения/риски/ответственные
3) Action items: кто/что/срок
4) Открытые вопросы

Стенограмма:
\"\"\"{text[:180000]}\"\"\""""
    if extra_prompt:
        prompt += f"\nДоп.инструкции:\n{extra_prompt}\n"

    r = client.models.generate_content(
        model=GENAI_MODEL,
        contents=prompt,
    )
    return r.text

def make_pdf(content: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for line in content.split("\n"):
        c.drawString(40, y, line[:110])
        y -= 14
        if y < 40:
            c.showPage(); y = 800
    c.save()
    buf.seek(0)
    return buf.read()

def parse_event(ev: dict):
    # Eventarc (Cloud Storage → Pub/Sub) формат
    if "message" in ev and "attributes" in ev["message"]:
        attrs = ev["message"]["attributes"]
        return attrs.get("bucketId"), attrs.get("objectId")
    # Прямой Cloud Storage push (редко)
    if "bucket" in ev and "name"
