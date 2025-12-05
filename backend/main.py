# main.py - corrected & debugged
# sanitize provider responses before returning to client

from fastapi import FastAPI, File, UploadFile, Depends, HTTPException, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

import os
import tempfile
import logging
import json
from datetime import datetime
from pathlib import Path
import signal
import sys
from typing import Optional, Any, Dict

from dotenv import load_dotenv
load_dotenv()

# SQLAlchemy Session typing for Depends
from sqlalchemy.orm import Session

# app logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("minutes")

# --- Project imports (adapt paths if your layout differs) ---
from models import get_db, Transcription

# services must implement the functions used below.
from services import (
    convert_to_mp3,
    transcribe_audio,
    summarize_meeting,
    save_transcript_and_summary,
    save_summary_as_markdown,       # kept for backward compatibility where used
    save_transcript_to_output,     # kept for backward compatibility
    generate_presigned_post,
    TRANSFORM_INPUT_BUCKET,
    OUTPUT_S3_BUCKET,
)

app = FastAPI(title="minutes API", version="1.0.0")

# CORS (open for dev; tighten in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _safe(obj: Any) -> Any:
    """
    Return a JSON-serializable version of obj.
    Strategy: json.dumps with default=str to coerce unknown objects to strings,
    then json.loads back to Python primitives (dict/list/str/...).
    """
    try:
        dumped = json.dumps(obj, default=str)
        return json.loads(dumped)
    except Exception:
        try:
            return str(obj)
        except Exception:
            return "(unserializable)"


@app.get("/")
async def root():
    return {"message": "minutes API", "version": app.version}


# --- Upload endpoint --------------------------------------------------------
@app.post("/upload")
async def upload_audio(
    file: UploadFile = File(...),
    transcribe: bool = Query(True, description="If true, run transcription after upload (requires provider configured)"),
    db: Session = Depends(get_db),
):
    """
    Accept file upload, convert to mp3 if needed, upload to input S3 (default) and optionally run transcription.
    After creating DB record, save transcript+summary to output bucket (deterministic paths).
    """
    tmp_path: Optional[str] = None
    mp3_path: Optional[str] = None
    upload_result: Optional[dict] = None

    try:
        # limit upload (configurable via env)
        MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE_BYTES", 100 * 1024 * 1024))  # 100MB default
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"File too large. Max {MAX_FILE_SIZE} bytes.")

        # write to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file.filename}") as fh:
            fh.write(content)
            tmp_path = fh.name
        logger.info("Saved upload to temporary file: %s", tmp_path)

        # convert (or shortcut if already mp3)
        try:
            mp3_path = convert_to_mp3(tmp_path)
            logger.info("convert_to_mp3 returned: %s", mp3_path)
        except Exception as ex:
            logger.exception("convert_to_mp3 failed")
            raise HTTPException(status_code=500, detail=f"convert_to_mp3 failed: {ex}")

        if not mp3_path or not Path(mp3_path).exists():
            raise HTTPException(status_code=500, detail="MP3 conversion failed: file missing")

        # Call services.transcribe_audio:
        # upload_only = not transcribe
        try:
            upload_result = transcribe_audio(mp3_path, upload_only=not transcribe)
            logger.info(
                "transcribe_audio returned keys: %s",
                list(upload_result.keys()) if isinstance(upload_result, dict) else str(upload_result),
            )
        except TypeError:
            logger.exception("transcribe_audio interface mismatch (upload_only flag missing)")
            raise HTTPException(status_code=500, detail="transcribe_audio interface mismatch (expecting upload_only flag)")
        except Exception as ex:
            logger.exception("transcribe_audio failed")
            raise HTTPException(status_code=500, detail=f"upload/transcribe step failed: {ex}")

        # Normalize result fields and sanitize provider objects
        s3_uri = None
        transcript_text = None
        speakers = []
        provider_raw = None
        upload_meta = None

        if isinstance(upload_result, dict):
            s3_uri = upload_result.get("s3_uri")
            transcript_text = upload_result.get("text")
            speakers = upload_result.get("speakers") or []
            provider_raw = upload_result.get("provider_raw")
            # sanitize upload_result for returning to client
            upload_meta = _safe(upload_result)
            # provider_raw may be nested inside upload_meta already; but ensure it's safe string too
            if "provider_raw" in upload_meta:
                upload_meta["provider_raw"] = _safe(upload_meta["provider_raw"])
        else:
            # fallback shape
            upload_meta = _safe(upload_result)

        transcript_text = transcript_text or (f"Uploaded to {s3_uri}" if s3_uri else "")
        provider_raw_sanitized = _safe(provider_raw)

        # Persist DB record
        transcription = Transcription(
            filename=file.filename,
            transcript=transcript_text,
            speakers=json.dumps(speakers),
        )
        db.add(transcription)
        db.commit()
        db.refresh(transcription)

        # Save transcript and (empty) summary to outputs: deterministic paths
        try:
            saved = save_transcript_and_summary(
                transcript=transcription.transcript or "",
                summary=transcription.summary or "",
                filename_base=transcription.filename or f"meeting_{transcription.id}",
            )
            transcript_s3 = saved.get("transcript_s3")
            summary_s3 = saved.get("summary_s3")
        except Exception:
            logger.exception("Failed to save transcript/summary to outputs (non-fatal)")
            transcript_s3 = None
            summary_s3 = None

        return {
            "id": transcription.id,
            "filename": transcription.filename,
            "transcript": transcription.transcript,
            "speakers": transcription.speakers,
            "created_at": transcription.created_at,
            "upload_meta": upload_meta,
            "provider_raw": provider_raw_sanitized,
            "transcript_s3": transcript_s3,
            "summary_s3": summary_s3,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unhandled error in /upload: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # best-effort cleanup
        for p in (tmp_path, mp3_path):
            try:
                if p and Path(p).exists():
                    Path(p).unlink()
            except Exception:
                logger.debug("Failed to delete temp file %s", p, exc_info=True)


# --- Direct transcript save -----------------------------------------------
class DirectTranscriptRequest(BaseModel):
    filename: str
    transcript: str
    speakers: str = "[]"


@app.post("/transcript")
async def save_direct_transcript(request: DirectTranscriptRequest, db: Session = Depends(get_db)):
    """Save user-provided transcript directly to DB."""
    try:
        transcription = Transcription(
            filename=request.filename,
            transcript=request.transcript,
            speakers=request.speakers,
        )
        db.add(transcription)
        db.commit()
        db.refresh(transcription)

        # Save transcript+no-summary to outputs
        try:
            saved = save_transcript_and_summary(
                transcript=transcription.transcript or "",
                summary=transcription.summary or "",
                filename_base=transcription.filename or f"meeting_{transcription.id}",
            )
            transcript_s3 = saved.get("transcript_s3")
            summary_s3 = saved.get("summary_s3")
        except Exception:
            logger.exception("Failed to save direct transcript to outputs (non-fatal)")
            transcript_s3 = None
            summary_s3 = None

        return {
            "id": transcription.id,
            "filename": transcription.filename,
            "transcript": transcription.transcript,
            "speakers": transcription.speakers,
            "created_at": transcription.created_at,
            "transcript_s3": transcript_s3,
            "summary_s3": summary_s3,
        }
    except Exception as e:
        logger.exception("Error saving direct transcript")
        raise HTTPException(status_code=500, detail=str(e))


# --- Read, summarize and export --------------------------------------------
@app.get("/transcription/{transcription_id}")
async def get_transcription(transcription_id: int, db: Session = Depends(get_db)):
    t = db.query(Transcription).filter(Transcription.id == transcription_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcription not found")
    return {
        "id": t.id,
        "filename": t.filename,
        "transcript": t.transcript,
        "speakers": t.speakers,
        "summary": t.summary,
        "created_at": t.created_at
    }


@app.post("/summarize/{transcription_id}")
async def create_summary(transcription_id: int, language: str = "en", temperature: float = 0.8, db: Session = Depends(get_db)):
    t = db.query(Transcription).filter(Transcription.id == transcription_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcription not found")

    try:
        speaker_table = None
        if t.speakers:
            try:
                speakers_data = json.loads(t.speakers)
                speaker_table = [[s.get("speaker", "Unknown"), s.get("description", "")] for s in speakers_data]
            except Exception:
                logger.warning("Could not parse speakers JSON for %s", transcription_id)

        summary = summarize_meeting(t.transcript, speaker_table=speaker_table, system_prompt_language=language, temperature=temperature)
        t.summary = summary
        db.commit()

        # Save transcript+summary to outputs (deterministic)
        saved = None
        try:
            saved = save_transcript_and_summary(
                transcript=t.transcript or "",
                summary=summary or "",
                filename_base=t.filename or f"meeting_{t.id}",
            )
        except Exception:
            logger.exception("Failed to save transcript+summary to outputs (non-fatal)")

        return {
            "summary": summary,
            "transcript_s3": (saved.get("transcript_s3") if saved else None),
            "summary_s3": (saved.get("summary_s3") if saved else None),
        }
    except Exception as e:
        logger.exception("Error creating summary for %s", transcription_id)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/export/{transcription_id}")
async def export_markdown(transcription_id: int, db: Session = Depends(get_db)):
    t = db.query(Transcription).filter(Transcription.id == transcription_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcription not found")

    try:
        # Prefer returning presigned S3 link if summary already uploaded (or upload now and return s3_uri).
        try:
            # Attempt to save/upload summary deterministically
            saved = save_transcript_and_summary(
                transcript=t.transcript or "",
                summary=t.summary or "",
                filename_base=t.filename or f"meeting_{t.id}",
            )
            s3_uri = saved.get("summary_s3")
            s3_key = None
            if s3_uri and s3_uri.startswith("s3://"):
                _, s3_key = s3_uri[5:].split("/", 1)
            if s3_uri:
                return {"s3_uri": s3_uri, "s3_key": s3_key, "bucket": OUTPUT_S3_BUCKET}
            else:
                # fallback: return local file
                local_path = saved.get("summary_local") if saved else None
                if local_path and Path(local_path).exists():
                    return FileResponse(local_path, media_type="text/markdown", filename=f"{t.filename or f'meeting_{t.id}'}_summary.md")
                raise RuntimeError("No summary available to export")
        except Exception:
            logger.exception("Failed to upload summary during export; trying local-only save")
            local_save = save_summary_as_markdown(t.transcript or "", t.summary or "", t.filename or f"meeting_{t.id}", upload_to_s3=False, transcription_id=str(t.id))
            local_path = local_save.get("local_path")
            return FileResponse(local_path, media_type="text/markdown", filename=f"{t.filename or f'meeting_{t.id}'}_summary.md")
    except Exception as e:
        logger.exception("Error exporting markdown for %s", transcription_id)
        raise HTTPException(status_code=500, detail=str(e))


# --- Presign upload (browser -> S3) ---------------------------------------
class PresignRequest(BaseModel):
    filename: str


@app.post("/s3/presign")
async def presign_upload(req: PresignRequest = Body(...)):
    """
    Return a presigned POST payload for browser direct upload to S3.
    """
    try:
        filename = req.filename
        if not filename or not filename.strip():
            raise HTTPException(status_code=400, detail="filename is required")

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        prefix = os.getenv("TRANSFORM_INPUT_PREFIX", "inputs").rstrip("/")
        key = f"{prefix}/{timestamp}_{os.path.basename(filename)}"

        presign = generate_presigned_post(key)
        return {"presign": presign, "key": key, "bucket": TRANSFORM_INPUT_BUCKET}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to generate presigned POST")
        raise HTTPException(status_code=500, detail=str(e))


# --- S3 trigger (client uploaded file) ------------------------------------
class S3TriggerRequest(BaseModel):
    s3_key: str
    transcribe: Optional[bool] = False  # whether to run transcription after upload


@app.post("/s3/trigger")
async def s3_trigger(req: S3TriggerRequest, db: Session = Depends(get_db)):
    """
    Trigger post-upload processing for an existing object in S3.
    This implementation stores a DB record referencing the S3 object.
    If req.transcribe is True, attempts to call services.transcribe_audio(s3_uri, upload_only=False).
    Note: your services.transcribe_audio must support S3 URIs for that flow; otherwise this may fail.
    """
    try:
        s3_key = req.s3_key
        if not s3_key or not s3_key.strip():
            raise HTTPException(status_code=400, detail="s3_key is required")

        if not TRANSFORM_INPUT_BUCKET:
            raise HTTPException(status_code=500, detail="Server misconfigured: TRANSFORM_INPUT_BUCKET not set")

        s3_uri = f"s3://{TRANSFORM_INPUT_BUCKET}/{s3_key.lstrip('/')}"
        logger.info("Received S3 trigger for: %s", s3_uri)

        transcript_text = f"Uploaded to {s3_uri}"
        speakers_json = json.dumps([])
        upload_meta = {"s3_uri": s3_uri}
        provider_raw = None

        if req.transcribe:
            try:
                result = transcribe_audio(s3_uri, upload_only=False)
                transcript_text = result.get("text") or transcript_text
                speakers_json = json.dumps(result.get("speakers") or [])
                upload_meta = _safe(result)
                provider_raw = _safe(result.get("provider_raw"))
            except Exception as e:
                logger.exception("Transcription from S3 failed for %s", s3_uri)
                raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")

        filename = os.path.basename(s3_key)
        transcription = Transcription(
            filename=filename,
            transcript=transcript_text,
            speakers=speakers_json
        )
        db.add(transcription)
        db.commit()
        db.refresh(transcription)

        # Save transcript+summary to outputs (deterministic)
        try:
            saved = save_transcript_and_summary(
                transcript=transcription.transcript or "",
                summary=transcription.summary or "",
                filename_base=transcription.filename or f"meeting_{transcription.id}",
            )
            transcript_s3 = saved.get("transcript_s3")
            summary_s3 = saved.get("summary_s3")
        except Exception:
            logger.exception("Failed to save transcript/summary to outputs (non-fatal)")
            transcript_s3 = None
            summary_s3 = None

        return {
            "id": transcription.id,
            "filename": transcription.filename,
            "transcript": transcription.transcript,
            "speakers": transcription.speakers,
            "created_at": transcription.created_at,
            "upload_meta": upload_meta,
            "provider_raw": provider_raw,
            "transcript_s3": transcript_s3,
            "summary_s3": summary_s3,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error in /s3/trigger")
        raise HTTPException(status_code=500, detail=str(e))


# --- Shutdown utilities ----------------------------------------------------
def _signal_handler(sig, frame):
    logger.info("Received shutdown signal. Gracefully shutting down.")
    sys.exit(0)


if __name__ == "__main__":
    import uvicorn

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info("🚀 Starting minutes API...")
    try:
        uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", 8000)))
    except KeyboardInterrupt:
        logger.info("🛑 Server stopped by user")
    except Exception as e:
        logger.exception("❌ Server error during startup: %s", e)
    finally:
        logger.info("👋 minutes API shutdown complete")
