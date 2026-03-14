import os
import json
import time
import base64
import boto3
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

# ─── Logging ────────────────────────────────────────────────────────────────
# Sets up a logger so we can see what's happening in Vercel function logs.
# Without this, errors are silent and hard to debug.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── App ────────────────────────────────────────────────────────────────────
# FastAPI is the web framework - it listens for HTTP requests and routes them
# to the right function. Think of it as the "front door" of the server.
app = FastAPI()

# ─── CORS ───────────────────────────────────────────────────────────────────
# Browsers block requests between different origins (e.g. Vercel frontend → API)
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
# All settings come from Vercel environment variables — no hardcoded values.
# Set these in Vercel Project Settings → Environment Variables.
MODEL_ID = os.environ.get("MODEL_ID")
AWS_REGION = os.environ.get("AWS_REGION")
AWS_ROLE_ARN = os.environ.get("AWS_ROLE_ARN")

# DEBUG mode — set DEBUG=true in Vercel env vars to enable verbose logging.
# When False (default): only errors are logged (clean production output).
# When True: logs every request, messages, OIDC claims, token counts, and timing.
DEBUG = os.environ.get("DEBUG", "true").lower() == "true"

# SYSTEM_PROMPT — controls how the model behaves and its personality.
# Override by setting SYSTEM_PROMPT env var in Vercel. No code change needed.
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "You are a helpful, friendly, and concise assistant. Be clear and direct in your responses.",
)

# Fail fast at startup if required env vars are missing.
# Better to crash with a clear message than fail silently mid-request.
for _var, _val in [("MODEL_ID", MODEL_ID), ("AWS_REGION", AWS_REGION), ("AWS_ROLE_ARN", AWS_ROLE_ARN)]:
    if not _val:
        raise ValueError(f"Required environment variable {_var} is not set")


# ─── JWT Decode ─────────────────────────────────────────────────────────────
# Decodes the payload section of a JWT token without verifying the signature.
# Used only in DEBUG mode to inspect OIDC claims (e.g. issuer, audience, expiry).
# Never log JWT tokens in production — they contain sensitive identity info.
def decode_jwt_payload(token: str) -> dict:
    payload = token.split(".")[1]
    # JWT base64 padding must be a multiple of 4
    payload += "=" * (4 - len(payload) % 4)
    return json.loads(base64.b64decode(payload))


# ─── AWS Credentials via OIDC ───────────────────────────────────────────────
# Vercel injects an OIDC token as the x-vercel-oidc-token request header.
# We exchange that token for temporary AWS credentials using STS
# AssumeRoleWithWebIdentity — no static AWS keys needed.
# The IAM role (AWS_ROLE_ARN) must trust Vercel's OIDC provider.
def get_bedrock_client(oidc_token: str):
    sts = boto3.client("sts", region_name=AWS_REGION)
    assumed = sts.assume_role_with_web_identity(
        RoleArn=AWS_ROLE_ARN,
        RoleSessionName="vercel-bedrock-session",
        WebIdentityToken=oidc_token,
    )
    creds = assumed["Credentials"]
    if DEBUG:
        logger.info(f"Role assumed, expires {creds['Expiration']}")
    # Return a Bedrock client using the temporary credentials
    return boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


# ─── Health Check ───────────────────────────────────────────────────────────
# A simple endpoint to verify the function is running and OIDC is active.
# Hit GET /api/health to check. oidc: true means the token header is present.
@app.get("/api/health")
def health(request: Request):
    oidc_enabled = bool(request.headers.get("x-vercel-oidc-token"))
    return {"status": "ok", "model": MODEL_ID, "oidc": oidc_enabled}


# ─── Chat Endpoint ──────────────────────────────────────────────────────────
# Main endpoint. The React frontend POSTs to /api/chat with:
#   { "messages": [ { "role": "user", "content": "hello" }, ... ] }
# The full conversation history is sent every time — that's how the model
# "remembers" previous messages (it has no memory of its own).
@app.post("/api/chat")
async def chat(request: Request):
    # Vercel injects the OIDC token as a request header when OIDC is enabled
    # in Project Settings. Without it, we can't assume the AWS role.
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
        # Decode and log OIDC claims to verify token issuer, audience, expiry
        claims = decode_jwt_payload(oidc_token)
        # logger.info(f"OIDC claims: {json.dumps(claims, default=str)}")
        # Log each input message so you can see the full conversation being sent
        for msg in messages:
            logger.info(f"[{msg['role'].upper()}] {msg['content']}")

    # Convert messages to the format Bedrock's Converse API expects.
    # Bedrock wants: [{ "role": "user", "content": [{ "text": "..." }] }]
    # The frontend sends: [{ "role": "user", "content": "..." }]
    bedrock_messages = [
        {"role": msg["role"], "content": [{"text": msg["content"]}]}
        for msg in messages
    ]

    try:
        bedrock = get_bedrock_client(oidc_token)
        start = time.time()

        # converse_stream() opens a streaming connection to Bedrock.
        # system prompt sets the model's personality and behavior.
        # inferenceConfig controls response length and creativity.
        response = bedrock.converse_stream(
            modelId=MODEL_ID,
            system=[{"text": SYSTEM_PROMPT}],
            messages=bedrock_messages,
            inferenceConfig={
                "maxTokens": 2048,   # max output length — model stops here if hit
                "temperature": 0.7,  # 0 = deterministic, 1 = creative
            },
        )

        # Collect the full response from the stream events.
        # messageStop carries stopReason (end_turn or max_tokens).
        # metadata carries token usage counts for monitoring.
        full_text = ""
        stop_reason = None
        input_tokens = None
        output_tokens = None

        for event in response["stream"]:
            if "contentBlockDelta" in event:
                # contentBlockDelta is the event type that carries actual text chunks
                text = event["contentBlockDelta"]["delta"].get("text", "")
                if text:
                    full_text += text
            elif "messageStop" in event:
                # end_turn = finished naturally, max_tokens = response was cut off
                stop_reason = event["messageStop"].get("stopReason")
            elif "metadata" in event:
                # Token counts for cost monitoring and anomaly detection
                usage = event["metadata"].get("usage", {})
                input_tokens = usage.get("inputTokens")
                output_tokens = usage.get("outputTokens")

        elapsed = round(time.time() - start, 2)
        if DEBUG:
            logger.info(
                f"stopReason={stop_reason} inputTokens={input_tokens} "
                f"outputTokens={output_tokens} duration={elapsed}s"
            )
            if stop_reason == "max_tokens":
                # User received a truncated response — consider raising maxTokens
                logger.warning("Response truncated: max_tokens reached")
            # Log the full assistant response to see what was sent back
            logger.info(f"[ASSISTANT] {full_text}")

        # SSE (Server-Sent Events) format: "data: <json>\n\n"
        # The frontend reads this and renders the response text.
        sse_body = f"data: {json.dumps({'text': full_text})}\n\ndata: [DONE]\n\n"
        return Response(content=sse_body, media_type="text/event-stream")

    except Exception as e:
        # Always log errors regardless of DEBUG setting
        logger.error(f"Bedrock error: {e}")
        return Response(
            content=f"data: {json.dumps({'error': str(e)})}\n\n",
            media_type="text/event-stream",
            status_code=500,
        )
