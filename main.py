import os, io
from fastapi import FastAPI, Request
from google.cloud import storage
from google.cloud import speech_v2
from google.cloud.speech_v2.types import cloud_speech
from google import genai
from reportlab.pdfgen import canvas

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
SPEECH_LOCATION = os.getenv("SPEECH_LOCATION", "global")
PDF_BUCKET = os.getenv("PDF_BUCKET")
LANG = os.getenv("LANG", "ru-RU")
SPEAKER_COUNT = int(os.getenv("SPEAKER_COUNT", "2"))

app = FastAPI()
storage_client = storage.Client()
speech_client = speech_v2.SpeechClient()
genai_client = genai.Client()  # Vertex AI (через google-genai)

def transcribe_gcs_uri(gcs_uri: str) -> str:
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
    lines=[]
    for file_result in resp.results:
        for r in file_result.transcript.results:
            if r.alternatives:
                t = r.alternatives[0].transcript.strip()
                if t: lines.append(t)
    return "\n".join(lines)

def summarize(text: str, extra_prompt: str="") -> str:
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
    r = genai_client.models.generate_content(
        model=os.getenv("GENAI_MODEL", "gemini-2.5-flash"),
        contents=prompt,
    )
    return r.text

def make_pdf(content:str)->bytes:
    buf = io.BytesIO()
    from reportlab.pdfgen import canvas
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
    # Eventarc (Storage → Pub/Sub) вариант
    if "message" in ev and "attributes" in ev["message"]:
        attrs = ev["message"]["attributes"]
        return attrs.get("bucketId"), attrs.get("objectId")
    # Прямой вариант
    if "bucket" in ev and "name" in ev: return ev["bucket"], ev["name"]
    # Универсальный через data
    if "data" in ev and isinstance(ev["data"], dict):
        d = ev["data"]
        if "bucket" in d and "name" in d: return d["bucket"], d["name"]
    return None, None

app = FastAPI()

@app.post("/ingest")
async def ingest(request: Request):
    event = await request.json()
    bucket, name = parse_event(event)
    if not bucket or not name or not name.lower().endswith(".mp4"):
        return {"status":"skipped"}

    gcs_uri = f"gs://{bucket}/{name}"
    text = transcribe_gcs_uri(gcs_uri)
    summary = summarize(text, extra_prompt=os.getenv("SUMMARY_PROMPT","Анализ, выводы встречи тезисно."))
    pdf_bytes = make_pdf(summary)

    outfile = name.rsplit("/",1)[-1].rsplit(".",1)[0] + ".pdf"
    storage_client.bucket(PDF_BUCKET).blob(outfile).upload_from_string(
        pdf_bytes, content_type="application/pdf"
    )
    return {"status":"ok","pdf":f"gs://{PDF_BUCKET}/{outfile}"}
