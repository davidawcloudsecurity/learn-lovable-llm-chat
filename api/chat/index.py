import os
import json
import time
import base64
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
AWS_ROLE_ARN = os.environ.get("AWS_ROLE_ARN")
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "You are a helpful, friendly, and concise assistant. Be clear and direct in your responses.",
)


def decode_jwt_payload(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (4 - len(payload) % 4)
    return json.loads(base64.b64decode(payload))


def get_bedrock_client(oidc_token: str):
    """Exchange Vercel OIDC token for temporary AWS credentials.
    Vercel injects the token as the x-vercel-oidc-token request header."""
    sts = boto3.client("sts", region_name=AWS_REGION)
    assumed = sts.assume_role_with_web_identity(
        RoleArn=AWS_ROLE_ARN,
        RoleSessionName="vercel-bedrock-session",
        WebIdentityToken=oidc_token,
    )
    creds = assumed["Credentials"]
    if DEBUG:
        logger.info(f"Role assumed, expires {creds['Expiration']}")
    return boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


@app.get("/api/health")
def health(request: Request):
    oidc_enabled = bool(request.headers.get("x-vercel-oidc-token"))
    return {"status": "ok", "model": MODEL_ID, "oidc": oidc_enabled}


@app.post("/api/chat")
async def chat(request: Request):
    # Vercel sends the OIDC token as a request header (same as awsCredentialsProvider does in JS)
    oidc_token = request.headers.get("x-vercel-oidc-token")
    if not oidc_token:
        return Response(
            content=f"data: {json.dumps({'error': 'x-vercel-oidc-token header missing'})}\n\n",
            media_type="text/event-stream",
            status_code=401,
        )

    body = await request.json()
    messages = body.get("messages", [])

    if not messages:
        return {"error": "messages array is required"}

    if DEBUG:
        logger.info(f"Chat request — {len(messages)} message(s), model: {MODEL_ID}")
        claims = decode_jwt_payload(oidc_token)
        logger.info(f"OIDC claims: {json.dumps(claims, default=str)}")
        for msg in messages:
            logger.info(f"[{msg['role'].upper()}] {msg['content']}")

    bedrock_messages = [
        {"role": msg["role"], "content": [{"text": msg["content"]}]}
        for msg in messages
    ]

    try:
        bedrock = get_bedrock_client(oidc_token)
        start = time.time()

        response = bedrock.converse_stream(
            modelId=MODEL_ID,
            system=[{"text": SYSTEM_PROMPT}],
            messages=bedrock_messages,
            inferenceConfig={"maxTokens": 2048, "temperature": 0.7},
        )

        full_text = ""
        stop_reason = None
        input_tokens = None
        output_tokens = None

        for event in response["stream"]:
            if "contentBlockDelta" in event:
                text = event["contentBlockDelta"]["delta"].get("text", "")
                if text:
                    full_text += text
            elif "messageStop" in event:
                stop_reason = event["messageStop"].get("stopReason")
            elif "metadata" in event:
                usage = event["metadata"].get("usage", {})
                input_tokens = usage.get("inputTokens")
                output_tokens = usage.get("outputTokens")

        elapsed = round(time.time() - start, 2)
        if DEBUG:
            logger.info(
                f"stopReason={stop_reason} inputTokens={input_tokens} outputTokens={output_tokens} duration={elapsed}s"
            )
            if stop_reason == "max_tokens":
                logger.warning("Response truncated: max_tokens reached")
            logger.info(f"[ASSISTANT] {full_text}")

        sse_body = f"data: {json.dumps({'text': full_text})}\n\ndata: [DONE]\n\n"
        return Response(content=sse_body, media_type="text/event-stream")

    except Exception as e:
        logger.error(f"Bedrock error: {e}")
        return Response(
            content=f"data: {json.dumps({'error': str(e)})}\n\n",
            media_type="text/event-stream",
            status_code=500,
        )
