import os
import tempfile
import subprocess
import shutil
import logging
import json
import re
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

import certifi
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# assemblyai (old preferred STT provider if configured)
try:
    import assemblyai as aai
except Exception:
    aai = None

# openai client (new pipeline fallback)
from openai import OpenAI

logger = logging.getLogger("minutes.services")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# -------------------------
# Configuration (from env)
# -------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
TEXT_MODEL_NAME = os.getenv("TEXT_MODEL_NAME", "gemini/gemini-2.0-flash")

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY", "")
AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
TRANSFORM_INPUT_BUCKET = os.getenv("TRANSFORM_INPUT_BUCKET")
TRANSFORM_INPUT_PREFIX = os.getenv("TRANSFORM_INPUT_PREFIX", "inputs")
OUTPUT_S3_BUCKET = os.getenv("TRANSFORM_OUTPUT_BUCKET") or os.getenv("OUTPUT_S3_BUCKET")
PRESIGN_URL_EXPIRES = int(os.getenv("PRESIGN_URL_EXPIRES", "900"))

MAX_REALTIME_BYTES = int(os.getenv("MAX_REALTIME_BYTES", "5242880"))
OPENAI_SPEECH_MODEL = os.getenv("OPENAI_SPEECH_MODEL", "whisper-1")
USE_OPENAI_TRANSCRIBE = os.getenv("USE_OPENAI_TRANSCRIBE", "true").lower() in ("1", "true", "yes")
PROVIDER_MAX_BYTES = int(os.getenv("PROVIDER_MAX_BYTES", 25_000_000))

VERIFY_PATH = os.getenv("AWS_CA_BUNDLE") or os.getenv("REQUESTS_CA_BUNDLE") or certifi.where()
BOTOCONFIG = Config(
    region_name=AWS_REGION,
    connect_timeout=int(os.getenv("BOTO_CONNECT_TIMEOUT", "60")),
    read_timeout=int(os.getenv("BOTO_READ_TIMEOUT", "300")),
    retries={"max_attempts": int(os.getenv("BOTO_MAX_RETRIES", "4")), "mode": "standard"},
)

# Initialize boto3 S3 client/resource
try:
    S3 = boto3.client("s3", region_name=AWS_REGION, config=BOTOCONFIG, verify=VERIFY_PATH)
    S3_RESOURCE = boto3.resource("s3", region_name=AWS_REGION, config=BOTOCONFIG, verify=VERIFY_PATH)
    logger.info("Boto3 S3 initialized (region=%s). Input bucket: %s. verify=%s", AWS_REGION, TRANSFORM_INPUT_BUCKET, VERIFY_PATH)
except Exception as e:
    logger.exception("Failed to create boto3 S3 clients: %s", e)
    S3 = None
    S3_RESOURCE = None

# Initialize AssemblyAI SDK if available
if aai is not None:
    try:
        aai.settings.api_key = ASSEMBLYAI_API_KEY
        aai.settings.http_timeout = int(os.getenv("ASSEMBLYAI_HTTP_TIMEOUT", "900"))
        logger.info("AssemblyAI configured (api_key set: %s)", bool(ASSEMBLYAI_API_KEY))
    except Exception:
        logger.exception("Failed to configure AssemblyAI (SDK present but config failed)")
else:
    logger.info("AssemblyAI SDK not installed; skipping AssemblyAI configuration")

# OpenAI client container
client: Optional[OpenAI] = None

def initialize_openai_client() -> bool:
    global client
    try:
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY not set")
        client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL, timeout=120.0)
        logger.info("OpenAI client initialized (base_url=%s, speech_model=%s)", OPENAI_BASE_URL, OPENAI_SPEECH_MODEL)
        return True
    except Exception as e:
        client = None
        logger.warning("OpenAI client init failed: %s", e)
        return False

_initialize_ok = initialize_openai_client()

# -------------------------
# Utility helpers
# -------------------------
def is_s3_uri(p: str) -> bool:
    return isinstance(p, str) and p.startswith("s3://")

def parse_s3_uri(uri: str):
    if not is_s3_uri(uri):
        raise ValueError("Not an s3:// URI")
    path = uri[5:]
    parts = path.split("/", 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else ""
    return bucket, key

def download_s3_to_temp(s3_uri: str) -> str:
    if S3 is None:
        raise RuntimeError("S3 client not configured")
    bucket, key = parse_s3_uri(s3_uri)
    fd, tmp_path = tempfile.mkstemp(prefix="s3dl_", suffix="_" + Path(key).name)
    os.close(fd)
    try:
        logger.info("Downloading %s -> %s", s3_uri, tmp_path)
        S3.download_file(bucket, key, tmp_path)
        return tmp_path
    except Exception as e:
        try:
            Path(tmp_path).unlink()
        except Exception:
            pass
        logger.exception("Failed to download from S3: %s", e)
        raise RuntimeError(f"Failed to download {s3_uri}: {e}")

# FFmpeg helpers (unchanged)...
def find_ffmpeg() -> Optional[str]:
    env_path = os.getenv("FFMPEG_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists() and p.is_file():
            return str(p)
    for name in ("ffmpeg", "ffmpeg.exe"):
        which = shutil.which(name)
        if which:
            return which
    return None

def run_cmd(cmd: List[str], capture_stderr=True):
    try:
        proc = subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return proc
    except subprocess.CalledProcessError as cpe:
        stderr = cpe.stderr.decode("utf-8", errors="replace") if cpe.stderr else ""
        raise RuntimeError(stderr) from cpe

def convert_to_mp3(input_path: str, target_samplerate: int = 16000) -> str:
    input_p = Path(input_path)
    if not input_p.exists():
        raise RuntimeError(f"Input file does not exist: {input_path}")
    if input_p.suffix.lower() == ".mp3":
        return str(input_p)
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found; set FFMPEG_PATH or install ffmpeg")
    out_dir = input_p.parent if input_p.parent.exists() else Path(tempfile.gettempdir())
    out_path = out_dir / (input_p.stem + "_converted.mp3")
    cmd = [ffmpeg, "-y", "-i", str(input_p), "-ac", "1", "-ar", str(target_samplerate), "-vn", str(out_path)]
    logger.info("Converting to mp3: %s ...", " ".join([Path(cmd[0]).name] + cmd[1:4]))
    run_cmd(cmd)
    if not out_path.exists():
        raise RuntimeError("ffmpeg produced no MP3 file")
    return str(out_path)

def convert_to_wav_for_transcription(input_path: str, target_samplerate: int = 16000) -> str:
    inp = Path(input_path)
    if not inp.exists():
        raise RuntimeError(f"Input does not exist: {input_path}")
    if inp.suffix.lower() == ".wav":
        return str(inp)
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found; set FFMPEG_PATH or install ffmpeg")
    out_dir = inp.parent if inp.parent.exists() else Path(tempfile.gettempdir())
    out_path = out_dir / (inp.stem + "_for_stt.wav")
    cmd = [ffmpeg, "-y", "-i", str(inp), "-ac", "1", "-ar", str(target_samplerate), "-vn", "-acodec", "pcm_s16le", str(out_path)]
    logger.info("Creating WAV for STT: %s ...", " ".join([Path(cmd[0]).name] + cmd[1:4]))
    run_cmd(cmd)
    if not out_path.exists():
        raise RuntimeError("ffmpeg produced no wav file")
    return str(out_path)

def get_audio_duration_seconds(path: str) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        size = os.path.getsize(path)
        estimate = max(1.0, size / 32000.0)
        logger.warning("ffprobe not found; estimating duration from size: %.2fs", estimate)
        return estimate
    cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        dur = float(out.decode().strip())
        return dur
    except Exception:
        size = os.path.getsize(path)
        estimate = max(1.0, size / 32000.0)
        logger.warning("ffprobe failed; falling back to estimate: %.2fs", estimate)
        return estimate

def split_wav_to_chunks(wav_path: str, max_bytes: int = PROVIDER_MAX_BYTES) -> List[str]:
    wav = Path(wav_path)
    if not wav.exists():
        raise RuntimeError("WAV path missing: " + wav_path)
    file_size = wav.stat().st_size
    duration = get_audio_duration_seconds(wav_path)
    logger.info("split_wav: size=%d bytes duration=%.2fs", file_size, duration)
    if file_size <= max_bytes:
        return [wav_path]
    bps = file_size / max(0.001, duration)
    target_seconds = max(5, int(max_bytes / bps))
    if target_seconds < 5:
        target_seconds = 5
    if target_seconds >= int(duration):
        return [wav_path]
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found (needed for splitting)")
    out_dir = wav.parent
    out_pattern = str(out_dir / (wav.stem + "_chunk_%03d.wav"))
    cmd = [ffmpeg, "-y", "-i", str(wav), "-f", "segment", "-segment_time", str(target_seconds), "-c", "copy", out_pattern]
    logger.info("Splitting WAV to chunks: target_seconds=%ds", target_seconds)
    run_cmd(cmd)
    chunks = sorted([str(p) for p in out_dir.glob(f"{wav.stem}_chunk_*.wav")])
    final_chunks: List[str] = []
    for ch in chunks:
        sz = Path(ch).stat().st_size
        if sz > max_bytes:
            logger.warning("Chunk %s still > max_bytes (%d > %d). Further splitting.", ch, sz, max_bytes)
            final_chunks.extend(split_wav_to_chunks(ch, max_bytes=max_bytes//2))
            try:
                Path(ch).unlink()
            except Exception:
                pass
        else:
            final_chunks.append(ch)
    if not final_chunks:
        return [wav_path]
    return final_chunks

def clean_temp_files(file_list: List[str]):
    for file_path in file_list:
        try:
            if file_path and Path(file_path).exists():
                Path(file_path).unlink()
                logger.debug("Deleted temp file: %s", file_path)
        except Exception as e:
            logger.warning("Could not delete temp file %s: %s", file_path, e)

# -------------------------
# S3 helpers (fixed)
# -------------------------
def upload_file_to_s3(local_path: str, key: str, bucket: Optional[str] = None) -> str:
    """
    Upload a local file to S3 and return s3:// URI.
    By default uploads to TRANSFORM_INPUT_BUCKET unless a `bucket` override is provided.
    """
    target_bucket = bucket or TRANSFORM_INPUT_BUCKET
    if not target_bucket:
        raise RuntimeError("No S3 bucket configured (TRANSFORM_INPUT_BUCKET or explicit bucket required)")
    if S3 is None:
        raise RuntimeError("S3 client not configured")
    try:
        S3.upload_file(local_path, target_bucket, key)
        s3_uri = f"s3://{target_bucket}/{key}"
        logger.info("Uploaded %s to %s", local_path, s3_uri)
        return s3_uri
    except Exception as e:
        logger.exception("S3 upload failed: %s", e)
        raise RuntimeError(f"S3 upload failed: {e}")

def generate_presigned_post(key: str, bucket: Optional[str] = None, expires_in: int = PRESIGN_URL_EXPIRES):
    bucket = bucket or TRANSFORM_INPUT_BUCKET
    if not bucket:
        raise RuntimeError("TRANSFORM_INPUT_BUCKET not configured")
    if S3 is None:
        raise RuntimeError("S3 client not configured")
    try:
        post = S3.generate_presigned_post(bucket, key, ExpiresIn=expires_in)
        logger.debug("Generated presigned POST for s3://%s/%s", bucket, key)
        return post
    except ClientError as e:
        logger.exception("Presign generation failed: %s", e)
        raise RuntimeError(f"Presign generation failed: {e}")

# -------------------------
# Simple outputs saver (minimal, deterministic)
# -------------------------
def _now_timestamp_str() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")

def save_transcript_and_summary(
    transcript: str,
    summary: str,
    filename_base: Optional[str] = None,
    output_bucket: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """
    Save transcript (txt) -> outputs/Transcripts/{timestamp}.txt
    Save summary (md)     -> outputs/Summary/{timestamp}.md
    Each artifact is created and uploaded exactly once.
    Returns dict containing local paths and s3 URIs (or None if upload not configured).
    """
    ts = _now_timestamp_str()
    safe_base = (filename_base or "meeting").replace(" ", "_")
    outputs_dir = os.path.join(tempfile.gettempdir(), "meeting_summaries")
    os.makedirs(outputs_dir, exist_ok=True)

    # build file names (separate files and separate keys)
    transcript_fname = f"{safe_base}_{ts}.txt"
    summary_fname = f"{safe_base}_{ts}.md"

    transcript_local = os.path.join(outputs_dir, transcript_fname)
    summary_local = os.path.join(outputs_dir, summary_fname)

    # write transcript only to transcript_local (no duplication)
    try:
        with open(transcript_local, "w", encoding="utf-8") as f:
            f.write(transcript or "")
    except Exception as e:
        logger.exception("Failed to save transcript locally: %s", e)
        raise RuntimeError(f"Failed to save transcript locally: {e}")

    # write summary only to summary_local (do NOT include full transcript here)
    try:
        md_content = f"# Summary ({safe_base} - {ts})\n\n{summary or ''}\n\n"
        with open(summary_local, "w", encoding="utf-8") as f:
            f.write(md_content)
    except Exception as e:
        logger.exception("Failed to save summary locally: %s", e)
        raise RuntimeError(f"Failed to save summary locally: {e}")

    # determine upload target (use OUTPUT_S3_BUCKET by default)
    target_bucket = output_bucket or OUTPUT_S3_BUCKET
    transcript_s3 = None
    summary_s3 = None

    if not target_bucket:
        logger.warning("OUTPUT_S3_BUCKET not configured; local files created but not uploaded")
        return {
            "transcript_local": transcript_local,
            "transcript_s3": None,
            "summary_local": summary_local,
            "summary_s3": None,
            "timestamp": ts,
        }

    # deterministic S3 keys (separate folders)
    transcript_key = f"outputs/Transcripts/{ts}.txt"
    summary_key = f"outputs/Summary/{ts}.md"

    # upload transcript once
    try:
        transcript_s3 = upload_file_to_s3(transcript_local, transcript_key, bucket=target_bucket)
    except Exception:
        logger.exception("Failed to upload transcript to S3 (non-fatal)")

    # upload summary once
    try:
        summary_s3 = upload_file_to_s3(summary_local, summary_key, bucket=target_bucket)
    except Exception:
        logger.exception("Failed to upload summary to S3 (non-fatal)")

    return {
        "transcript_local": transcript_local,
        "transcript_s3": transcript_s3,
        "summary_local": summary_local,
        "summary_s3": summary_s3,
        "timestamp": ts,
    }

# -------------------------
# Compatibility helpers (unchanged; kept for other callers)
# -------------------------
def sanitize_for_key(s: str, max_len: int = 120) -> str:
    if not s:
        return "unnamed"
    s = str(s)
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    if cleaned == "":
        return "unnamed"
    return cleaned

def upload_summary_to_s3(local_path: str, key: str, bucket: Optional[str] = None) -> str:
    target_bucket = bucket or OUTPUT_S3_BUCKET
    if not target_bucket:
        raise RuntimeError("OUTPUT_S3_BUCKET (or TRANSFORM_OUTPUT_BUCKET) not configured")
    return upload_file_to_s3(local_path, key, bucket=target_bucket)

# -------------------------
# AssemblyAI transcription (old code) - prefer this if configured
# -------------------------
def transcribe_with_assemblyai(local_audio_path: str, word_boost: str = "", language: str = "auto") -> Dict[str, Any]:
    if aai is None:
        raise RuntimeError("AssemblyAI SDK not installed")
    if not ASSEMBLYAI_API_KEY:
        raise RuntimeError("ASSEMBLYAI_API_KEY not configured")

    mp3 = convert_to_mp3(local_audio_path)
    logger.info("Uploading to AssemblyAI: %s", mp3)

    config_kwargs = {
        "speaker_labels": True,
        "language_detection": True if language == "auto" else False,
        "language_code": None if language == "auto" else language,
        "word_boost": [w.strip() for w in word_boost.split(",")] if word_boost else None,
    }
    config = aai.TranscriptionConfig(**{k: v for k, v in config_kwargs.items() if v is not None})
    transcriber = aai.Transcriber()

    transcript = transcriber.transcribe(mp3, config=config)
    logger.info("AssemblyAI transcription submitted: id=%s status=%s", getattr(transcript, "id", None), getattr(transcript, "status", None))

    if getattr(transcript, "status", None) == aai.TranscriptStatus.completed:
        transcript_text = ""
        speaker_set = set()
        if getattr(transcript, "utterances", None):
            for utterance in transcript.utterances:
                speaker = utterance.speaker if getattr(utterance, "speaker", None) else "Unknown"
                transcript_text += f"Speaker {speaker}: {utterance.text}\n"
                speaker_set.add(speaker)
        else:
            transcript_text = getattr(transcript, "text", "(No speech detected or transcription empty)")
            speaker_set.add("Unknown")

        sorted_speakers = []
        try:
            sorted_speakers = sorted(list(speaker_set), key=lambda x: (int(x) if str(x).isdigit() else float('inf'), x))
        except Exception:
            sorted_speakers = sorted(list(speaker_set))

        speakers_data = [{"speaker": s, "description": ""} for s in sorted_speakers]
        return {"text": transcript_text, "speakers": speakers_data, "raw": transcript}
    elif getattr(transcript, "status", None) == aai.TranscriptStatus.error:
        raise RuntimeError(f"AssemblyAI transcription error: {getattr(transcript, 'error', 'unknown')}")
    else:
        raise RuntimeError(f"AssemblyAI returned intermediate status: {getattr(transcript, 'status', 'unknown')}")

# -------------------------
# OpenAI per-chunk transcription
# -------------------------
def transcribe_with_openai_chunk(audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
    if client is None:
        if not initialize_openai_client():
            raise RuntimeError("OpenAI client not initialized")
    try:
        with open(audio_path, "rb") as fh:
            logger.info("Calling OpenAI transcription for chunk %s", audio_path)
            response = client.audio.transcriptions.create(
                model=OPENAI_SPEECH_MODEL,
                file=fh,
                language=language if language else None
            )
    except Exception as e_file:
        logger.debug("file-like call failed: %s; trying bytes fallback", e_file)
        try:
            with open(audio_path, "rb") as fh2:
                data = fh2.read()
            response = client.audio.transcriptions.create(
                model=OPENAI_SPEECH_MODEL,
                file=data,
                language=language if language else None
            )
        except Exception as e_bytes:
            logger.exception("OpenAI transcription call failed for chunk: %s", e_bytes)
            raise

    # defensive extraction
    text = None
    try:
        text = getattr(response, "text", None)
        if not text and isinstance(response, dict):
            text = response.get("text") or response.get("transcript")
    except Exception:
        text = None
    if not text:
        try:
            if isinstance(response, dict) and "data" in response and isinstance(response["data"], list):
                text = response["data"][0].get("text")
        except Exception:
            text = None
    if not text:
        try:
            text = str(response)
        except Exception:
            text = "(no text extracted)"
    return {"text": text, "raw": response}

# -------------------------
# Main transcribe_audio (uploads + chooses provider)
# -------------------------
def transcribe_audio(audio_file: str, upload_only: bool = True, word_boost: str = "", language: str = "en") -> Dict[str, Any]:
    """
    Accept local path or s3:// URI.
    - upload_only=True -> upload an mp3 to the input S3 and return {'s3_uri', 'file_size'}
    - upload_only=False -> attempt to transcribe using AssemblyAI if configured, else chunked OpenAI fallback
    """
    if not audio_file:
        raise ValueError("No audio file provided")

    created_temp: List[str] = []
    try:
        # download s3 if given
        local_in = audio_file
        if is_s3_uri(audio_file):
            local_in = download_s3_to_temp(audio_file)
            created_temp.append(local_in)

        # convert to mp3 for storage/upload
        mp3_path = convert_to_mp3(local_in)
        if mp3_path != local_in:
            created_temp.append(mp3_path)

        # upload mp3 to input S3 (keep audio in input bucket)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = Path(mp3_path).name
        key = f"{TRANSFORM_INPUT_PREFIX.rstrip('/')}/{timestamp}_{filename}"
        s3_uri = upload_file_to_s3(mp3_path, key)
        file_size = os.path.getsize(mp3_path)
        logger.info("Uploaded and ready: %s (size=%d bytes)", s3_uri, file_size)

        base = {"s3_uri": s3_uri, "file_size": file_size}

        if upload_only:
            return base

        # Prefer AssemblyAI if configured
        if aai is not None and ASSEMBLYAI_API_KEY:
            logger.info("Using AssemblyAI for transcription")
            try:
                aa_result = transcribe_with_assemblyai(mp3_path, word_boost=word_boost, language=language if language != "auto" else "auto")
                text = aa_result.get("text") or ""
                speakers = aa_result.get("speakers") or []
                provider_raw = aa_result.get("raw")
                return {**base, "text": text, "speakers": speakers, "provider_raw": provider_raw}
            except Exception as e:
                logger.exception("AssemblyAI transcription failed; falling back to chunked OpenAI: %s", e)

        # Fallback: chunked OpenAI transcription
        if not USE_OPENAI_TRANSCRIBE:
            logger.info("No STT provider configured; returning upload-only metadata")
            return base

        logger.info("Using chunked OpenAI transcription fallback")
        wav_for_stt = convert_to_wav_for_transcription(mp3_path, target_samplerate=16000)
        if wav_for_stt != mp3_path:
            created_temp.append(wav_for_stt)

        wav_size = os.path.getsize(wav_for_stt)
        logger.info("WAV for STT size=%d bytes", wav_size)

        chunk_paths = [wav_for_stt]
        if wav_size > PROVIDER_MAX_BYTES:
            logger.info("WAV exceeds provider limit (%d > %d). Splitting...", wav_size, PROVIDER_MAX_BYTES)
            chunk_paths = split_wav_to_chunks(wav_for_stt, max_bytes=PROVIDER_MAX_BYTES)
            logger.info("Created %d chunks", len(chunk_paths))

        combined_texts: List[str] = []
        provider_raw_list: List[Any] = []
        for idx, ch in enumerate(chunk_paths):
            try:
                chunk_res = transcribe_with_openai_chunk(ch, language=(None if language == "auto" else language))
                combined_texts.append(chunk_res.get("text") or "")
                provider_raw_list.append(chunk_res.get("raw"))
            except Exception as e:
                logger.exception("Chunk transcription failed (index=%d): %s", idx, e)
                provider_raw_list.append({"chunk_error": str(e)})
                combined_texts.append("(error transcribing chunk)")

        final_text = "\n".join([t for t in combined_texts if t]).strip()
        result = {**base, "text": final_text, "speakers": [], "provider_raw": provider_raw_list}
        logger.info("transcribe_audio returned keys: %s", list(result.keys()))
        return result

    except Exception as e:
        logger.exception("Error during upload/transcription process")
        raise RuntimeError(f"Error during upload/transcription process: {e}")
    finally:
        # cleanup temps
        try:
            for p in created_temp:
                try:
                    if Path(p).exists():
                        Path(p).unlink()
                except Exception:
                    logger.debug("Failed removing temp %s", p, exc_info=True)
        except Exception:
            logger.debug("cleanup error", exc_info=True)

# -------------------------
# Summarization + save helpers (updated)
# -------------------------
SYSTEM_PROMPTS = {
    "en": "...",
    "cn": "..."
}

def summarize_meeting(transcript: str, speaker_table: Optional[List[List[str]]] = None, system_prompt_language: str = "en", temperature: float = 0.8) -> str:
    if not transcript or transcript.strip() == "" or transcript.strip() == "(No speech detected or transcription empty)":
        return "No transcript available to summarize."
    if client is None:
        logger.warning("OpenAI client None; trying to reinit")
        if not initialize_openai_client():
            raise RuntimeError("OpenAI client not initialized")
    system_prompt = SYSTEM_PROMPTS.get(system_prompt_language, SYSTEM_PROMPTS.get("en", ""))
    try:
        speaker_info_str = ""
        if speaker_table:
            speaker_info_str = "\n\nSpeaker Information:\n" + "\n".join(
                [f"Speaker {row[0]}: {row[1]}" for row in speaker_table if len(row) > 1 and row[1].strip()]
            )
        content = f"Transcription:\n{transcript}\n----{speaker_info_str}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content}
        ]
        logger.info("Sending summarization request")
        response = client.chat.completions.create(model=TEXT_MODEL_NAME, messages=messages, temperature=temperature)
        summary = None
        try:
            summary = response.choices[0].message.content.strip()
        except Exception:
            summary = getattr(response, "text", None) or str(response)
        if not summary:
            return "(Summary generation failed or produced empty result)"
        return summary
    except Exception as e:
        logger.exception("Summarization error")
        raise RuntimeError(f"Summarization error: {e}")

def save_summary_as_markdown(
    transcript: str,
    summary: str,
    filename_base: Optional[str] = None,
    upload_to_s3: bool = True,
    transcription_id: Optional[str] = None,
    output_bucket: Optional[str] = None
) -> Dict[str, Optional[str]]:
    """
    Backwards-compatible: saves markdown locally and optionally uploads to OUTPUT_S3_BUCKET.
    Returns dict: { 'local_path': ..., 's3_uri': ... or None, 's3_key': ... or None }
    """
    now = datetime.utcnow()
    now_str = now.strftime("%Y%m%d_%H%M%S")
    safe_base = sanitize_for_key(filename_base or "meeting")
    filename = f"{safe_base}_{now_str}_summary.md"
    save_dir = os.path.join(tempfile.gettempdir(), "meeting_summaries")
    os.makedirs(save_dir, exist_ok=True)
    local_path = os.path.join(save_dir, filename)

    content = f"## {filename_base or 'Meeting'} - {now_str}\n\n***\n\n### Summary\n\n{summary}\n\n"
    if transcript:
        content += f"\n\n***\n\n### Full Transcript\n\n{transcript}\n\n"

    try:
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("Summary saved locally: %s", local_path)
    except Exception as e:
        logger.exception("Error saving markdown file locally")
        raise RuntimeError(f"Error saving markdown file: {e}")

    s3_uri = None
    s3_key = None

    if upload_to_s3:
        date_prefix = now.strftime("%Y/%m/%d")
        id_part = sanitize_for_key(str(transcription_id)) + "_" if transcription_id else ""
        s3_key = f"outputs/{date_prefix}/{safe_base}/{id_part}{filename}"
        try:
            target_bucket = output_bucket or OUTPUT_S3_BUCKET
            if not target_bucket:
                raise RuntimeError("OUTPUT_S3_BUCKET not configured")
            s3_uri = upload_summary_to_s3(local_path, s3_key, bucket=target_bucket)
            logger.info("Uploaded summary to output bucket: %s", s3_uri)
        except Exception:
            logger.exception("Failed uploading summary to S3; returning local path only")
            s3_uri = None

    return {"local_path": local_path, "s3_uri": s3_uri, "s3_key": s3_key}

def save_transcript_to_output(transcript: str, filename_base: Optional[str] = None, transcription_id: Optional[str] = None, output_bucket: Optional[str] = None) -> Dict[str, Optional[str]]:
    """
    Backwards-compatible: Save the raw transcript as a .txt locally and optionally upload to OUTPUT_S3_BUCKET.
    Returns dict: { 'local_path', 's3_uri', 's3_key' }
    """
    now = datetime.utcnow()
    now_str = now.strftime("%Y%m%d_%H%M%S")
    safe_base = sanitize_for_key(filename_base or "meeting")
    filename = f"{safe_base}_{now_str}_transcript.txt"
    save_dir = os.path.join(tempfile.gettempdir(), "meeting_summaries")
    os.makedirs(save_dir, exist_ok=True)
    local_path = os.path.join(save_dir, filename)
    try:
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(transcript or "")
        logger.info("Transcript saved locally: %s", local_path)
    except Exception:
        logger.exception("Failed to save transcript locally")
        raise RuntimeError("Failed to save transcript locally")

    s3_uri = None
    s3_key = None
    date_prefix = now.strftime("%Y/%m/%d")
    id_part = sanitize_for_key(str(transcription_id)) + "_" if transcription_id else ""
    s3_key = f"outputs/{date_prefix}/{safe_base}/{id_part}{filename}"
    try:
        target_bucket = output_bucket or OUTPUT_S3_BUCKET
        if not target_bucket:
            raise RuntimeError("OUTPUT_S3_BUCKET not configured")
        s3_uri = upload_file_to_s3(local_path, s3_key, bucket=target_bucket)
        logger.info("Uploaded transcript to output bucket: %s", s3_uri)
    except Exception:
        logger.exception("Failed to upload transcript to S3")
        s3_uri = None

    return {"local_path": local_path, "s3_uri": s3_uri, "s3_key": s3_key}
