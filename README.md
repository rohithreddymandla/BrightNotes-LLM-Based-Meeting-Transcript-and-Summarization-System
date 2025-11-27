**Speech-to-Insights**

Serverless audio-to-transcript pipeline with realtime Whisper, batch SageMaker transforms, PII-safe processing, embeddings, semantic search, and indexing.

---

## **1. Overview**

Speech-to-Insights is a production-ready backend that takes raw audio, stores it in S3, chooses realtime or batch transcription, performs PII detection and redaction, embeds the text, and supports downstream indexing and search.

The system is built for AWS Lambda, API Gateway, S3, SageMaker, and Step Functions.

Key features:

* Multipart upload API via FastAPI.
* Realtime Whisper inference for small files and batch SageMaker Transform for larger files.
* PII detection and redaction (regex + optional Comprehend + spaCy).

* Deterministic embedding provider with ST, OpenAI, or fully local fallback.

* S3-triggered Lambda handlers for automated transcription.

* Vector index (FAISS or numpy) with chunking, persistence, cosine similarity search.

* Local development runner with FFmpeg-based preprocessing.


---

## **2. Repository Structure**

```
backend/
  app.py                  # FastAPI app factory  :contentReference[oaicite:5]{index=5}
  routes.py               # REST API endpoints   :contentReference[oaicite:6]{index=6}
  lambda_handlers.py      # Lambda entrypoints   :contentReference[oaicite:7]{index=7}
  whisper.py              # Realtime + batch ASR :contentReference[oaicite:8]{index=8}
  transcribe.py           # Orchestration/FFmpeg :contentReference[oaicite:9]{index=9}
  handlers.py             # Upload + workflow    :contentReference[oaicite:10]{index=10}
  embedding.py            # Pluggable embeddings :contentReference[oaicite:11]{index=11}
  indexer.py              # Vector search        :contentReference[oaicite:12]{index=12}
  pii_detector.py         # PII detection        :contentReference[oaicite:13]{index=13}
  ...
deploy_lambdas.sh         # Deployment helper    :contentReference[oaicite:14]{index=14}
local_run.sh              # Local runner         :contentReference[oaicite:15]{index=15}
.env                      # Local config         :contentReference[oaicite:16]{index=16}
requirements.txt          # Python deps          :contentReference[oaicite:17]{index=17}
```

---

## **3. How the system works**

### **3.1 Upload flow**

1. Client uploads audio to `/upload` using multipart form.
   Handled by `routes.py` → `_save_upload_and_maybe_transcribe`.
2. File is stored in the S3 input bucket under `inputs/<run_id>/filename.wav`.
3. If the file is small enough (default: <5MB), realtime Whisper is triggered in background.
   MAX_REALTIME_BYTES is configurable.
4. If the file is larger, async workflow or batch inference kicks in.

---

### **3.2 Realtime transcription**

Realtime inference uses a SageMaker endpoint:
`whisper.transcribe_bytes_realtime()`


Used when:

* Audio size <= `MAX_REALTIME_BYTES`
* `SAGEMAKER_ENDPOINT` is configured.

---

### **3.3 Batch transcription**

Larger files trigger a SageMaker Batch Transform job:

`whisper.start_sagemaker_transform()`


Output artifacts are written to the configured output bucket.
A Step Functions pipeline may optionally orchestrate multi-stage processing (PII → summary → embedding → indexing).

---

### **3.4 Embeddings**

The embedding system chooses the first available backend:

1. **Sentence-Transformers**
2. **OpenAI embeddings**
3. **Deterministic SHA-256 fallback** (always available)

All produce deterministic float32 vectors.
Contract tested in `test_embedding_contract.py`.


---

### **3.5 PII detection and redaction**

PII detection supports:

* Regex patterns (emails, phones, SSNs, credit cards, IPs, URLs).
* Optional spaCy NER.
* Optional AWS Comprehend.

Redaction happens using span-wise masking.
Implemented in `pii_detector.py`.


---

### **3.6 Vector indexing and semantic search**

`indexer.py` offers:

* FAISS (if available) or numpy cosine similarity.
* Chunking long docs with overlap.
* Persistent index:

  ```
  <base>.faiss
  <base>.npy
  <base>_meta.json
  <base>_ids.json
  ```



---

## **4. API Endpoints**

### **POST /upload**

Accepts multipart audio.
Returns JSON with:

* `upload_id`
* `s3_uri`
* `status`
* Optional workflow execution metadata

Defined in `routes.py`.


---

### **POST /start-workflow**

Triggers Step Functions execution.
Good for async transcription pipelines.

---

### **GET /presign**

Returns S3 presigned URL for direct client upload.

---

### **GET /status/{upload_id}**

Fetches final `result.json` from the output bucket.

---

### **GET /health**

Simple health probe.

---

## **5. Lambda Handlers**

Located in `lambda_handlers.py`.


Handlers:

* `api_upload_handler`
* `s3_event_handler` (triggered when audio lands in S3)
* `start_transform_handler`
* `sagemaker_transform_callback`
* `health_handler`

These wrap Whisper, Transcribe, PII processing, and Step Functions.

---

## **6. Local Development**

### **Run local FastAPI server**

```
./local_run.sh
```

This loads `.env` and launches uvicorn with auto-reload.


---

### **Local transcription test**

```
./local_run.sh --test-transcribe sample.wav
```

### **Local PII test**

```
./local_run.sh --test-pii "Email me at aaku@example.com"
```

---

## **7. Deployment**

The repo includes a Lambda deploy helper script:
`deploy_lambdas.sh`


Usage:

```
./deploy_lambdas.sh \
  --bucket <artifact-bucket> \
  --prefix lambda-artifacts \
  --functions api_upload_handler,s3_event_handler \
  --role-arn arn:aws:iam::<acct>:role/LambdaExecRole
```

The script:

* Packages backend into a zip.
* Uploads to S3.
* Updates or creates Lambda functions with proper handlers.
* Supports environment variables, memory, timeouts.

---

## **8. Environment Variables**

Defined in `.env`


Important ones:

* `TRANSFORM_INPUT_BUCKET`
* `OUTPUT_S3_BUCKET`
* `SAGEMAKER_ENDPOINT`
* `SAGEMAKER_TRANSFORM_ROLE`
* `MAX_REALTIME_BYTES`
* `ST_MODEL_NAME`
* `AWS_COMPREHEND_ENABLED`

---

## **9. Testing**

Two core contract test suites:

### **Embedding contract tests**

`test_embedding_contract.py` ensures:


* Determinism
* Correct vector dim
* Batch consistency
* Non-zero norm
* Optional persistence API correctness

### **API upload tests**

`test_api_upload.py` ensures the API works with TestClient:


* Valid multipart uploads
* Missing-file errors
* JSON shape correctness

Run tests:

```
pytest -q
```

---

## **10. FFmpeg Requirements**

`transcribe.py` normalizes audio via FFmpeg:


Install locally:

```
brew install ffmpeg
# or
sudo apt-get install ffmpeg
```

---

## **11. Semantic Search Index**

Typical usage:

```python
from backend.indexer import VectorIndex

idx = VectorIndex()
idx.add(["hello world", "cloud computing is fun"])
idx.save("my_index")

idx2 = VectorIndex.load("my_index")
results = idx2.nearest_k("hello")
```

---

## **12. Minimal Setup Steps**

1. Install dependencies:

```
pip install -r backend/requirements.txt
```

2. Set up `.env` with S3 buckets and SageMaker config.

3. Start local server:

```
./local_run.sh
```

4. Hit `/upload` with an audio file.

5. Watch logs in CloudWatch (in AWS) or console (local).

---

## **13. License**

MIT license via `pyproject.toml`.
