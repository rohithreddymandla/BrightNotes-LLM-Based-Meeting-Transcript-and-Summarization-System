# **Speech-to-Insights**

Serverless audio-to-transcript pipeline with realtime Whisper, batch SageMaker transforms, PII-safe processing, embeddings, semantic search, and indexing.

---

## **1. Overview**

Speech-to-Insights is a production-ready backend that ingests audio, stores it in S3, performs realtime or batch transcription, applies PII detection and redaction, embeds the resulting text, and supports downstream indexing and semantic search.

It is built around AWS Lambda, API Gateway, S3, SageMaker, Step Functions, and FastAPI.

Key features:

* Multipart upload API via FastAPI.
* Realtime Whisper inference for small files.
* Batch SageMaker Transform for larger audio.
* PII detection and redaction (regex, optional Comprehend, optional spaCy).
* Deterministic embedding provider with Sentence-Transformers, OpenAI, or local fallback.
* S3-triggered Lambda handlers for automated processing.
* Vector index (FAISS or numpy) with chunking, persistence, and cosine similarity search.
* Local development runner with FFmpeg-based audio normalization.

---

## **2. Repository Structure**

```
backend/
  app.py                  # FastAPI app factory
  routes.py               # REST API endpoints
  lambda_handlers.py      # Lambda entrypoints
  whisper.py              # Realtime + batch ASR
  transcribe.py           # Orchestration + FFmpeg normalization
  handlers.py             # Upload + workflow + S3 helpers
  embedding.py            # Pluggable embedding provider
  indexer.py              # Vector index + similarity search
  pii_detector.py         # PII detection and redaction
deploy_lambdas.sh         # Lambda deploy helper
local_run.sh              # Local dev + testing tool
.env                      # Local configuration
requirements.txt          # Python dependencies
```

---

## **3. How the system works**

### **3.1 Upload flow**

1. Client sends multipart audio to `/upload`.
2. The backend stores the file in the S3 input bucket under `inputs/<run_id>/filename.wav`.
3. Small files (<5MB default) trigger realtime Whisper in background.
4. Larger files go to asynchronous batch processing via SageMaker or Step Functions.

---

### **3.2 Realtime transcription**

Uses a configured Whisper realtime SageMaker endpoint.
Triggered when:

* File size ≤ `MAX_REALTIME_BYTES`
* `SAGEMAKER_ENDPOINT` is set

---

### **3.3 Batch transcription**

Large files run through a SageMaker Batch Transform job.
Outputs are stored in the configured output bucket.
If a Step Functions workflow is configured, additional steps like PII, summarization, embedding, and indexing can run automatically.

---

### **3.4 Embeddings**

Embedding backends:

1. Sentence-Transformers
2. OpenAI embedding API
3. Deterministic SHA-256 fallback (always available)

All outputs are deterministic float32 vectors.

---

### **3.5 PII detection and redaction**

Supports:

* Regex patterns for email, phone, SSN, credit card, IP, URL, and more
* Optional spaCy NER for person, org, location
* Optional AWS Comprehend PII API

Redaction is done span-wise with optional last-digit preservation for credit cards.

---

### **3.6 Vector indexing and semantic search**

The indexer provides:

* FAISS backend (if installed) or numpy fallback
* Cosine similarity search
* Built-in chunking for long documents
* Persistent index artifacts:

  ```
  <base>.faiss
  <base>.npy
  <base>_meta.json
  <base>_ids.json
  ```

---

## **4. API Endpoints**

### **POST /upload**

Accepts multipart audio. Returns:

* `upload_id`
* `s3_uri`
* `status`
* Optional workflow metadata

### **POST /start-workflow**

Starts a Step Functions execution for async processing.

### **GET /presign**

Returns a presigned PUT URL for client-side uploads.

### **GET /status/{upload_id}**

Reads the final `result.json` from the output bucket.

### **GET /health**

Simple health probe.

---

## **5. Lambda Handlers**

Available handlers:

* `api_upload_handler`
* `s3_event_handler`
* `start_transcription`
* `wait_for_transform_callback`
* `aggregate_results`
* `notify`
* `health_handler`

These glue together Whisper, S3, PII, and Step Functions.

---

## **6. Local Development**

### Run server locally

```
./local_run.sh
```

### Test local transcription

```
./local_run.sh --test-transcribe path/to/audio.wav
```

### Test PII redaction

```
./local_run.sh --test-pii "Call me at 555-123-4567"
```

---

## **7. Deployment**

Deploy Lambda functions:

```
./deploy_lambdas.sh \
  --bucket <artifact-bucket> \
  --prefix lambda-artifacts \
  --functions api_upload_handler,s3_event_handler \
  --role-arn arn:aws:iam::<account>:role/LambdaExecRole
```

The script packages code, uploads artifacts, and updates Lambda functions.

---

## **8. Environment Variables**

Configured in `.env`.
Important fields:

* `TRANSFORM_INPUT_BUCKET`
* `OUTPUT_S3_BUCKET`
* `SAGEMAKER_ENDPOINT`
* `SAGEMAKER_TRANSFORM_ROLE`
* `MAX_REALTIME_BYTES`
* `ST_MODEL_NAME`
* `AWS_COMPREHEND_ENABLED`

---

## **9. Testing**

### Embedding contract tests

Ensures:

* Deterministic outputs
* Correct vector dimension
* Batch consistency
* Non-zero vector norms
* Optional persistence support

### API upload tests

Ensures:

* Multipart upload works
* Missing-file errors behave correctly
* Responses contain expected fields

Run tests:

```
pytest -q
```

---

## **10. FFmpeg Requirements**

The transcription pipeline requires FFmpeg for normalization.

Install:

```
brew install ffmpeg
# or
sudo apt-get install ffmpeg
```

---

## **11. Semantic Search Index**

Example:

```python
from backend.indexer import VectorIndex

idx = VectorIndex()
idx.add(["hello world", "cloud computing is fun"])
idx.save("my_index")

loaded = VectorIndex.load("my_index")
results = loaded.nearest_k("hello", k=3)
print(results)
```

---

## **12. Minimal Setup Steps**

1. Install dependencies:

   ```
   pip install -r backend/requirements.txt
   ```
2. Configure `.env` with your S3 and SageMaker settings.
3. Start local server:

   ```
   ./local_run.sh
   ```
4. Upload an audio file to `/upload`.
5. Check console or CloudWatch logs.

---

## **13. License**

MIT license.

If you want a shorter GitHub-style README, a project-report version, or a production-ops version, I can generate that too.
