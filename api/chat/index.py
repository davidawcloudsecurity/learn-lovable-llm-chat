import os
import json
import boto3
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from mangum import Mangum

# ─── Logging ────────────────────────────────────────────────────────────────
# Sets up a logger so we can see what's happening in the terminal.
# Without this, errors are silent and hard to debug.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── App ────────────────────────────────────────────────────────────────────
# FastAPI is the web framework - it listens for HTTP requests and routes them
# to the right function. Think of it as the "front door" of the server.
app = FastAPI()

# ─── CORS ───────────────────────────────────────────────────────────────────
# Browsers block requests between different origins (e.g. localhost:5173 → localhost:8000)
# unless the server explicitly says "yes, that origin is allowed".
# allow_origins=["*"] means ANY frontend can talk to this server.
# In production you'd lock this down to your actual domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Config ─────────────────────────────────────────────────────────────────
# Read settings from environment variables (.env file).
# os.environ.get("KEY", "default") means: use the env var if set, otherwise use the default.
MODEL_ID = os.environ.get("MODEL_ID")
AWS_REGION = os.environ.get("AWS_REGION")

# DEBUG mode - set DEBUG=true in your .env to enable verbose request/response logging.
# When False, only errors are logged (clean production output).
# When True, logs every request, message count, stream start/finish, and timing.
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

# ─── Bedrock Client ─────────────────────────────────────────────────────────
# boto3 is the AWS SDK for Python. This creates a client that can talk to
# the Bedrock Runtime service (the one that actually runs AI models).
# It uses your AWS credentials from environment variables or ~/.aws/credentials.
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)


# ─── Health Check ───────────────────────────────────────────────────────────
# A simple endpoint so you can verify the server is running.
# Hit GET /api/health in your browser or with curl to check.
@app.get("/api/health")
def health():
    return {"status": "ok", "model": MODEL_ID}


# ─── Chat Endpoint ──────────────────────────────────────────────────────────
# This is the main endpoint. The React frontend POSTs to /api/chat with:
#   { "messages": [ { "role": "user", "content": "hello" }, ... ] }
# The full conversation history is sent every time - that's how the LLM
# "remembers" previous messages (it has no memory of its own).
@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])

    if not messages:
        return {"error": "messages array is required"}

    if DEBUG:
        logger.info(f"Chat request received — {len(messages)} message(s), model: {MODEL_ID}")

    # Convert messages to the format Bedrock's Converse API expects.
    # Bedrock wants: [{ "role": "user", "content": [{ "text": "..." }] }]
    # The frontend sends: [{ "role": "user", "content": "..." }]
    # So we wrap the content string inside a list with a text dict.
    bedrock_messages = [
        {"role": msg["role"], "content": [{"text": msg["content"]}]}
        for msg in messages
    ]

    # ─── Generator Function ─────────────────────────────────────────────────
    # This is a Python generator - a function that yields values one at a time
    # instead of returning everything at once.
    # We use it to stream the response chunk by chunk to the browser,
    # so the user sees words appearing as they're generated (like ChatGPT).
    def stream():
        try:
            if DEBUG:
                logger.info("Calling Bedrock converse_stream...")

            start = __import__("time").time()

            # converse_stream() opens a streaming connection to Bedrock.
            # It sends the full messages array and starts getting tokens back.
            response = bedrock.converse_stream(
                modelId=MODEL_ID,
                messages=bedrock_messages,
                inferenceConfig={
                    "maxTokens": 2048,   # max length of the response
                    "temperature": 0.7,  # 0 = deterministic, 1 = creative
                },
            )

            if DEBUG:
                logger.info("Stream opened, receiving chunks...")

            # Bedrock sends back a stream of events.
            # We loop through each event and look for text chunks.
            for event in response["stream"]:
                # contentBlockDelta is the event type that carries actual text.
                # Other event types exist (like message start/stop) but we ignore them.
                if "contentBlockDelta" in event:
                    text = event["contentBlockDelta"]["delta"].get("text", "")
                    if text:
                        # SSE (Server-Sent Events) format: "data: <json>\n\n"
                        # The frontend reads this format and calls onDelta(text)
                        # for each chunk, appending it to the message on screen.
                        yield f"data: {json.dumps({'text': text})}\n\n"

            if DEBUG:
                elapsed = round(__import__("time").time() - start, 2)
                logger.info(f"Stream complete in {elapsed}s")

            # Signal to the frontend that the stream is finished.
            # The frontend's chat-api.ts looks for this exact string.
            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.error(f"Bedrock error: {e}")
            # If something goes wrong mid-stream, send an error event
            # so the frontend can show an error message instead of hanging.
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    # StreamingResponse wraps the generator and sends each yielded chunk
    # to the browser as it's produced, using the SSE content type.
    return StreamingResponse(stream(), media_type="text/event-stream")

# Vercel serverless handler
handler = Mangum(app)
