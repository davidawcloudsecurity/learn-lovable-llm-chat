import os
import json
import time
import boto3
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_ID = os.environ.get("MODEL_ID")
AWS_REGION = os.environ.get("AWS_REGION")
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

# boto3 automatically picks up AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY from env
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)


@app.get("/api/health")
def health():
    return {"status": "ok", "model": MODEL_ID}


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])

    if not messages:
        return {"error": "messages array is required"}

    if DEBUG:
        logger.info(f"Chat request — {len(messages)} message(s), model: {MODEL_ID}")

    bedrock_messages = [
        {"role": msg["role"], "content": [{"text": msg["content"]}]}
        for msg in messages
    ]

    try:
        start = time.time()

        response = bedrock.converse_stream(
            modelId=MODEL_ID,
            messages=bedrock_messages,
            inferenceConfig={"maxTokens": 2048, "temperature": 0.7},
        )

        full_text = ""
        for event in response["stream"]:
            if "contentBlockDelta" in event:
                text = event["contentBlockDelta"]["delta"].get("text", "")
                if text:
                    full_text += text

        if DEBUG:
            logger.info(f"Done in {round(time.time() - start, 2)}s")

        sse_body = f"data: {json.dumps({'text': full_text})}\n\ndata: [DONE]\n\n"
        return Response(content=sse_body, media_type="text/event-stream")

    except Exception as e:
        logger.error(f"Bedrock error: {e}")
        return Response(
            content=f"data: {json.dumps({'error': str(e)})}\n\n",
            media_type="text/event-stream",
            status_code=500,
        )
