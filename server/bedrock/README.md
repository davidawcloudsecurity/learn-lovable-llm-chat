# learn-lovable-llm-chat — Bedrock Server

Python + FastAPI backend for the learn-lovable-llm-chat app. Streams responses from AWS Bedrock to the React frontend using Server-Sent Events (SSE).

## Quick Start

```bash
pip install fastapi uvicorn boto3
uvicorn index:app --port 8000 --reload
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MODEL_ID` | `anthropic.claude-3-5-sonnet-20241022-v2:0` | Bedrock model to use |
| `AWS_REGION` | `us-east-1` | AWS region |
| `AWS_ACCESS_KEY_ID` | — | AWS credentials |
| `AWS_SECRET_ACCESS_KEY` | — | AWS credentials |

## Endpoints

- `GET /api/health` — health check
- `POST /api/chat` — accepts `{ messages: [] }`, streams SSE response

---

## Changelog

### 2026-03-14 — feat: add Python/FastAPI server (`index.py`)

**Type:** feature

Added `index.py` as a Python alternative to the existing `index.js` Node.js server. Both servers expose the same API contract so the React frontend works with either without changes.

**What was added:**
- FastAPI app with CORS middleware
- `GET /api/health` endpoint
- `POST /api/chat` endpoint using `boto3` `converse_stream()`
- SSE streaming via `StreamingResponse` and a Python generator
- Logging via `logging` module
- `MODEL_ID` and `AWS_REGION` read from environment variables — no hardcoded values
