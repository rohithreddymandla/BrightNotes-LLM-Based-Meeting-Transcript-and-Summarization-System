# Speech-to-Insights: Minutes AI

Turn raw meeting audio into structured, editable, and searchable insights.

Minutes AI is a full-stack system that:

* Ingests meeting audio
* Transcribes it with optional speaker metadata
* Applies PII-aware processing and basic redaction
* Generates summaries, keywords, and sentiment
* Exports transcripts and summaries locally and to S3
* Is architected to plug into a serverless AWS pipeline (Step Functions, SNS, etc.)

Everything runs locally with FastAPI + Vue, with optional AWS integration for storage and orchestration.

---

## Core Features

### Audio ingestion and preprocessing

* Audio upload via FastAPI
* Local temp file management
* ffmpeg-based normalization and conversion
* Supports common formats (MP3, WAV, M4A, etc.)
* Configurable max upload size via `MAX_REALTIME_BYTES` (e.g., up to 500 MB)

### Transcription

* Chunked transcription pipeline for large files
* Whisper-style STT using OpenAI-compatible API
* Optional AssemblyAI fallback if configured
* Automatic merging of chunk outputs into a single transcript
* Speaker list support when diarization is available

### Storage and data model

* SQLite + SQLAlchemy backend
* Tables for transcriptions, speakers, and summaries with timestamps
* Deterministic file export for reproducibility:

  * `outputs/Transcripts/<timestamp>.txt`
  * `outputs/Summary/<timestamp>.md`
* Optional S3 mirroring for inputs/outputs using:

  * `TRANSFORM_INPUT_BUCKET`
  * `TRANSFORM_OUTPUT_BUCKET`
  * `OUTPUT_S3_BUCKET`
  * Prefixes like `TRANSFORM_INPUT_PREFIX` and `OUTPUT_S3_PREFIX`

### Summaries and insights

* Structured meeting summaries using `TEXT_MODEL_NAME` via OpenAI-compatible API
* Keyword extraction using token frequency
* Sentiment scoring via text model
* PII-aware processing (regex + utility functions to support redaction paths)
* SRT-style export with approximate timing

### Exports

* Transcript as `.txt`
* Summary as `.md`
* Subtitles as `.srt`
* Local filesystem + optional S3 uploads
* Deterministic filenames and non-duplicated saves

### Frontend (UI)

* Vue 3 + Vite + Tailwind interface
* Audio upload page
* Transcript viewer and speaker editor
* Markdown-based summary editor
* Export buttons for TXT, MD, and SRT
* Status toasts, error handling, and progress indicators
* CORS configured for local dev (FastAPI + frontend ports)

### AWS and serverless alignment

* S3 used for inputs/outputs with clear prefixes
* Optional Step Functions integration via `STATE_MACHINE_ARN`
* Optional SNS alerts via `NOTIFY_SNS_TOPIC_ARN`
* Local dev AWS credentials supported (for testing only)
* Presigned URL support via `PRESIGN_URL_EXPIRES` for secure, time-limited uploads

---

## Quick Start

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate      # On Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open the URL printed by Vite (usually `http://localhost:5173`).

---

## Configuration

Create a `.env` file in the `backend` directory.
Below is a **safe template** that mirrors your real config without exposing keys:

```env
# ----------------------------
# API Keys (Required)
# ----------------------------
OPENAI_API_KEY=your_openai_api_key_here
ASSEMBLYAI_API_KEY=your_assemblyai_api_key_here

# ----------------------------
# Model Configuration
# ----------------------------
TEXT_MODEL_NAME=gpt-4o-mini
OPENAI_BASE_URL=https://api.openai.com/v1

# ----------------------------
# Backend configuration
# ----------------------------
APP_TITLE=Speech to Insights API
APP_DESCRIPTION=Backend for Speech to Insights project
ENV=local
LOG_LEVEL=info

# ----------------------------
# AWS S3
# ----------------------------
TRANSFORM_INPUT_PREFIX=inputs
OUTPUT_S3_PREFIX=outputs

TRANSFORM_INPUT_BUCKET=your-input-bucket-name
OUTPUT_S3_BUCKET=your-output-bucket-name
TRANSFORM_OUTPUT_BUCKET=your-output-bucket-name

AWS_REGION=us-east-2

# ----------------------------
# Step Functions (optional)
# ----------------------------
STATE_MACHINE_ARN=           # if using an AWS Step Functions workflow

# ----------------------------
# Optional SNS
# ----------------------------
NOTIFY_SNS_TOPIC_ARN=        # if using SNS notifications

# ----------------------------
# Whisper / upload config
# ----------------------------
MAX_REALTIME_BYTES=524288000    # e.g., 500 MB
PRESIGN_URL_EXPIRES=900         # presigned URL TTL in seconds

# ----------------------------
# AWS Credentials (local dev only)
# DO NOT USE IAM USER KEYS IN PRODUCTION
# ----------------------------
AWS_ACCESS_KEY_ID=your_aws_access_key_for_local_dev
AWS_SECRET_ACCESS_KEY=your_aws_secret_key_for_local_dev

# ----------------------------
# CORS
# ----------------------------
ALLOW_ORIGINS=http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000,http://localhost:5173

# ----------------------------
# Uvicorn dev settings
# ----------------------------
UVICORN_RELOAD=true
UVICORN_WORKERS=1
PORT=8000
HOST=127.0.0.1
```

Notes:

* Keep the real keys only in your private `.env`, never in Git.
* `ALLOW_ORIGINS` should include your frontend dev URL.
* `MAX_REALTIME_BYTES` and `PRESIGN_URL_EXPIRES` control large file handling and presigned URL TTL.

---

## System Architecture

### 1. Ingestion

* User uploads audio from the frontend
* Backend receives via FastAPI `/upload` route
* File is stored in a temp directory
* ffmpeg converts/normalizes audio as needed
* For large workflows, file can be uploaded or mirrored to S3 (`TRANSFORM_INPUT_BUCKET`)

### 2. Transcription pipeline

Implemented primarily in `services.py`:

* If file is local: direct processing
* If file is in S3: accessed via S3 client, optionally via presigned URLs
* Large files are chunked using `MAX_REALTIME_BYTES` config
* Each chunk is sent to:

  * OpenAI Whisper-style STT through `OPENAI_BASE_URL`, or
  * AssemblyAI when `ASSEMBLYAI_API_KEY` is available
* Chunk transcripts are merged into a single coherent transcript
* Speaker metadata is attached when diarization is available

### 3. Storage layer

* SQLite database via SQLAlchemy
* Schema typically includes:

  * `id`
  * `filename`
  * `transcript`
  * `speakers`
  * `summary`
  * `created_at`
* On completion, files are exported deterministically to:

  * `outputs/Transcripts/...`
  * `outputs/Summary/...`
* If S3 is configured:

  * Exports are mirrored once to `OUTPUT_S3_BUCKET` under `OUTPUT_S3_PREFIX`

### 4. Summarization and insights

* Backend retrieves transcript from DB by ID
* Optional inclusion of speaker table in the prompt to the LLM
* Summary generated using `TEXT_MODEL_NAME` (e.g., `gpt-4o-mini`)
* Summary stored back in DB and exported to filesystem/S3
* Extra insight endpoints:

  * Keyword extraction
  * Sentiment scoring
  * PII-aware utility functions (support for redaction workflows)
  * SRT generation

### 5. Exports

* `/export/{id}` returns the markdown summary
* `/export/{id}/srt` returns SRT subtitles
* Files are available locally and optionally in S3 buckets for downstream use or serverless pipelines.

### 6. Frontend

* Vue 3 interface for:

  * Uploading audio
  * Viewing and editing transcripts
  * Editing speaker names/roles
  * Viewing and editing summaries in markdown
  * Triggering summarization and export actions
* Communicates with FastAPI backend via CORS-whitelisted origins.

### 7. AWS integration and serverless alignment

Even when running locally, the project is structured to play well with a serverless AWS stack:

* S3 buckets and prefixes for input/output segmentation
* `STATE_MACHINE_ARN` reserved for optional Step Functions orchestration
* `NOTIFY_SNS_TOPIC_ARN` reserved for alerting on failures or completion
* Local AWS credentials allow testing without deploying the full serverless pipeline yet.

---

## API Overview

| Method | Route                 | Purpose                                |
| ------ | --------------------- | -------------------------------------- |
| POST   | `/upload`             | Upload audio, transcribe, and persist  |
| GET    | `/transcription/{id}` | Retrieve transcript + speaker metadata |
| POST   | `/summarize/{id}`     | Generate and store structured summary  |
| GET    | `/export/{id}`        | Download summary as markdown           |
| GET    | `/export/{id}/srt`    | Download SRT subtitles                 |
| GET    | `/sentiment/{id}`     | Get sentiment analysis for transcript  |
| GET    | `/keywords/{id}`      | Get extracted keywords                 |
| POST   | `/transcript`         | Save user-provided raw transcript      |

(If you have a `/health` or `/config` endpoint, you can add those too.)

---

## End-to-End Flow

```text
Audio Upload (Frontend)
        ↓
FastAPI /upload + ffmpeg
        ↓
Chunked STT (OpenAI / AssemblyAI)
        ↓
Transcript + Speakers stored in SQLite
        ↓
Optional mirror to S3 (inputs/outputs)
        ↓
User views/edits transcript in UI
        ↓
User triggers /summarize/{id}
        ↓
LLM generates structured summary
        ↓
Summary stored + exported (TXT / MD / SRT)
        ↓
User downloads exports (local or via S3-backed endpoints)
```

---

## Security & Reliability

* All API keys loaded from environment variables
* Local dev IAM keys explicitly marked non-production
* Sanitized logging for provider responses
* Safe S3 key generation with prefixes
* Strict temp file cleanup after processing
* Configurable log level via `LOG_LEVEL`
* CORS restricted to known frontend origins

---

## Tech Stack

**Backend**

* FastAPI
* SQLAlchemy + SQLite
* ffmpeg
* OpenAI-compatible APIs
* AssemblyAI (optional)
* boto3 for AWS S3 integration
* Uvicorn for local dev

**Frontend**

* Vue 3
* Vite
* TailwindCSS

**Cloud (Optional / Already Integrated)**

* AWS S3 for input/output storage
* Step Functions (via `STATE_MACHINE_ARN`, when used)
* SNS (via `NOTIFY_SNS_TOPIC_ARN`, when used)

---

## Project Outcomes

Based on the current implementation, the project delivers:

* End-to-end speech-to-text pipeline
* Speaker metadata support and editing
* Summaries, keywords, sentiment, and PII-aware handling
* Multiple export formats (TXT, MD, SRT)
* Local + S3-backed data paths
* A working full-stack application with clear hooks into a serverless AWS design
