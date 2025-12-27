# **Speech-to-Insights (BrightNotes Edition)**

Turn long meeting recordings into clean transcripts, structured summaries, and exportable insights.

This project runs fully locally with FastAPI + Vue, and integrates with AWS S3 for storage and serverless workflows.

---

# **📸 UI Screenshots**

### Start a new session

![Start Session](https://raw.githubusercontent.com/Akash-Kadali/Speech-to-Insights/main/Screenshots/1.png)

### Audio upload

![Audio Upload](https://raw.githubusercontent.com/Akash-Kadali/Speech-to-Insights/main/Screenshots/2.png)

### Transcript editor

![Transcript Editor](https://raw.githubusercontent.com/Akash-Kadali/Speech-to-Insights/main/Screenshots/3.png)

### Summary view

![Summary View](https://raw.githubusercontent.com/Akash-Kadali/Speech-to-Insights/main/Screenshots/4.png)

### Final meeting details

![Final Output](https://raw.githubusercontent.com/Akash-Kadali/Speech-to-Insights/main/Screenshots/5.png)

---

# **Overview**

Speech-to-Insights processes any meeting audio into a structured record:

* Upload and normalize audio
* Convert to mp3/wav via ffmpeg
* Chunked or full STT using

  * OpenAI Whisper (`whisper-1`) or
  * AssemblyAI (if configured)
* Speaker metadata
* Clean transcript stored in SQLite
* LLM summary generation
* Export to Markdown, TXT, SRT
* Optional S3 storage for input audio and output summaries
* Browser-to-S3 presigned uploads

Backend is FastAPI.
Frontend is Vue 3 + Vite + Tailwind.
Deployment uses Nginx as a static server + API proxy.

---

# **Features**

### Audio ingestion

* Supports MP3, WAV, M4A, FLAC
* Automatic ffmpeg conversion
* File size checks
* Chunk splitting for large audio (based on provider limits)

### Transcription

* AssemblyAI transcription with diarization
* OpenAI Whisper chunked fallback
* S3 upload path preserved
* Provider response sanitized before returning to UI

### Storage

* SQLite database at `/app/data/database.db`
* SQLAlchemy models manage transcripts and summaries

### Summaries

* LLM summary using configurable OpenAI endpoint
* Speaker-aware summary generation
* Deterministic output file naming
* TXT + MD saved locally and optionally uploaded to S3

### Frontend

* Vite + Vue 3
* Axios API calls
* Markdown editor via `@xiangfa/mdeditor` 
* Proxy config `/api -> http://localhost:8000` (vite config) 
* TailwindCSS + PostCSS config included 
* `index.html` updated branding and metadata 

---

# **Project Structure**

```
backend/
   main.py
   models.py
   services.py
   requirements.txt
   data/ (auto-created for SQLite)
frontend/
   src/
   index.html
   vite.config.js
   tailwind.config.js
   postcss.config.js
nginx/
   nginx.conf
Screenshots/
Dockerfile
```

---

# **Backend Setup**

```bash
cd backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Dependencies include FastAPI, SQLAlchemy, boto3, AssemblyAI, OpenAI, ffmpeg, etc. 

---

# **Frontend Setup**

```bash
cd frontend
npm install
npm run dev
```

Opens at:

```
http://localhost:5173
```

Vite proxy automatically maps frontend `/api/*` calls → FastAPI backend. 

---

# **Environment Variables**

Backend expects:

```
OPENAI_API_KEY=
ASSEMBLYAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
TEXT_MODEL_NAME=gpt-4o-mini
OPENAI_SPEECH_MODEL=whisper-1

AWS_REGION=us-east-2
TRANSFORM_INPUT_BUCKET=
TRANSFORM_OUTPUT_BUCKET=
PRESIGN_URL_EXPIRES=900

MAX_REALTIME_BYTES=5242880
PROVIDER_MAX_BYTES=25000000

ALLOW_ORIGINS=http://localhost:5173
```

---

# **API Endpoints**

### Upload

POST `/upload`
Uploads audio, converts to mp3, stores in S3, optionally runs transcription.

### Get transcription

GET `/transcription/{id}`

### Summarize

POST `/summarize/{id}`
Uses LLM + speaker table from database.

### Export Markdown

GET `/export/{id}`
Returns S3 link or local MD file.

### Save transcript manually

POST `/transcript`

### Create presigned upload URL

POST `/s3/presign`

These endpoints all come from `main.py`. 

---

# **How the System Works**

```
Frontend → FastAPI → services.py pipeline

1. Upload file
2. Convert via ffmpeg
3. Upload mp3 to S3
4. If transcribe=True:
       AssemblyAI → or OpenAI Whisper chunked STT
5. Store text + speakers in SQLite
6. On summary:
       LLM generates structured summary
7. Deterministic artifact saving:
       outputs/Transcripts/timestamp.txt
       outputs/Summary/timestamp.md
8. Upload to S3 if OUTPUT_S3_BUCKET set
```

Everything here is based directly on your backend processing pipeline. 

---

# **Nginx Deployment**

Your nginx config supports:

* SPA routing
* Long-lived caching for JS/CSS
* `/api` reverse proxy to backend
* Optional HTTPS block
* Brotli/gzip compression

See full config: 

---

# **Security**

* Sanitized provider responses
* No API keys in source
* CORS configured with explicit origins
* Temp file cleanup for all uploads
* S3 objects isolated by timestamped keys
* SQLite stored in a dedicated `/app/data` directory created at runtime 

---

# **Tech Stack**

**Backend:** FastAPI, SQLAlchemy, boto3, AssemblyAI, OpenAI, ffmpeg
**Frontend:** Vue 3, Vite, Tailwind, Markdown editor
**Storage:** SQLite (local), S3 (optional)
**Infra:** Docker + Nginx reverse proxy

---

# **Outcome**

You get a clean, deterministic workflow:

✓ Audio → Transcript
✓ Transcript → Speaker-aware Summary
✓ Exportable Markdown / TXT / SRT
✓ Local or S3 storage
✓ Deployable stack with Nginx + Docker
✓ Fully reproducible artifacts
