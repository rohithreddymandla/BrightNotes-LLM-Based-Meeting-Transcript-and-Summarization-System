# **Speech-to-Insights: Minutes AI**

Turn raw meeting audio into structured, editable, and searchable insights.

Minutes AI is a full-stack system that ingests meeting recordings, transcribes them, detects speakers, summarizes conversations, and exports clean transcripts. It runs fully locally with FastAPI + Vue, with optional AWS S3 + Step Functions alignment for a serverless future.

---

# **📸 UI Screenshots**

These images come directly from your repo’s `Screenshots/` folder.

### **Start New Meeting Session**

![Start Session](https://raw.githubusercontent.com/Akash-Kadali/Speech-to-Insights/main/Screenshots/1.png)

---

### **Audio Upload Processing**

![Audio Upload](https://raw.githubusercontent.com/Akash-Kadali/Speech-to-Insights/main/Screenshots/2.png)

---

### **Transcript Editor + Speaker Management**

![Transcript Editor](https://raw.githubusercontent.com/Akash-Kadali/Speech-to-Insights/main/Screenshots/3.png)

---

### **Generated Summary View**

![Summary View](https://raw.githubusercontent.com/Akash-Kadali/Speech-to-Insights/main/Screenshots/4.png)

---

### **Final Meeting Details Page**

![Final Output](https://raw.githubusercontent.com/Akash-Kadali/Speech-to-Insights/main/Screenshots/5.png)

---

# **🔥 Overview**

Minutes AI converts messy audio into clean meeting intelligence:

* Audio upload and preprocessing
* Whisper-style STT via OpenAI-compatible API
* Optional AssemblyAI fallback
* Speaker detection and manual editing
* PII-aware cleaning and utilities
* Structured LLM-generated summary
* Keywords and sentiment extraction
* Export to TXT, Markdown, SRT
* Optional S3 mirroring for serverless workflows

---

# **🚀 Core Features**

### **Audio Ingestion**

* Upload MP3, WAV, M4A, FLAC
* ffmpeg normalization
* Chunked processing for large recordings

### **Transcription**

* Whisper-style STT pipeline
* Automatic chunk merging
* Speaker diarization (when supported)

### **Storage**

* SQLite + SQLAlchemy
* Immutable, timestamped output files
* Optional S3 mirroring of all inputs/outputs

### **Summaries & Insights**

* Structured summary with action items
* Keyword extraction
* Sentiment scoring
* PII-aware cleaning
* SRT subtitle generation

### **Frontend UI**

* Vue 3 + Vite + Tailwind
* Upload → Transcript → Summary workflow
* Speaker renaming
* Markdown preview
* Export options (TXT / MD / SRT)

---

# **⚙️ Quick Start**

## Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Open:
`http://localhost:5173`

---

# **🔧 Backend Configuration (`backend/.env`)**

```env
OPENAI_API_KEY=your_openai_key
ASSEMBLYAI_API_KEY=your_assembly_key

TEXT_MODEL_NAME=gpt-4o-mini
OPENAI_BASE_URL=https://api.openai.com/v1

MAX_REALTIME_BYTES=524288000
PRESIGN_URL_EXPIRES=900

TRANSFORM_INPUT_BUCKET=your-input-bucket
OUTPUT_S3_BUCKET=your-output-bucket
TRANSFORM_OUTPUT_BUCKET=your-output-bucket
AWS_REGION=us-east-2

STATE_MACHINE_ARN=
NOTIFY_SNS_TOPIC_ARN=

ALLOW_ORIGINS=http://localhost:5173,http://127.0.0.1:8000
```

---

# **📡 API Endpoints**

| Method | Route                 | Purpose                     |
| ------ | --------------------- | --------------------------- |
| POST   | `/upload`             | Upload + transcribe audio   |
| GET    | `/transcription/{id}` | Fetch transcript            |
| POST   | `/summarize/{id}`     | Generate structured summary |
| GET    | `/export/{id}`        | Export summary (Markdown)   |
| GET    | `/export/{id}/srt`    | Export subtitles            |
| GET    | `/sentiment/{id}`     | Sentiment analysis          |
| GET    | `/keywords/{id}`      | Keyword extraction          |
| POST   | `/transcript`         | Save raw transcript         |

---

# **🧩 Architecture Flow**

```
Frontend (Vue)
   ↓
Audio Upload
   ↓
FastAPI Ingestion + ffmpeg
   ↓
Chunked STT (OpenAI or AssemblyAI)
   ↓
SQLite storage
   ↓
LLM Summary Generation
   ↓
TXT / MD / SRT Exports
   ↓
Optional S3 Mirror
```

---

# **🔒 Security**

* No keys in source control
* Sanitized logs
* Strict CORS settings
* Presigned URL expiration
* Temporary file cleanup
* Dev-only IAM credentials clearly marked

---

# **🛠 Tech Stack**

**Backend:** FastAPI, SQLAlchemy, ffmpeg, boto3, OpenAI-compatible APIs
**Frontend:** Vue 3, Vite, TailwindCSS
**Cloud (Optional):** AWS S3, Step Functions, SNS

---

# **🏁 Project Outcomes**

This system is production-aligned and delivers:

* Complete STT pipeline
* Strong UI for reviewing/editing transcripts
* Actionable meeting summaries
* Clean exports
* AWS-aligned design
* Local-first privacy-protecting workflow
