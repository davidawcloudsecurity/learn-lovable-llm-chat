import os
import sys
import json
import time
import base64
import boto3
import logging
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

# Add this file's directory to sys.path so 'tools' package can be found
# regardless of what working directory Vercel sets at runtime.
sys.path.insert(0, str(Path(__file__).parent))

from tools import os_info_tool

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

# Token pricing rates (USD per million tokens).
# Defaults match Claude Haiku 4.5. Override via Vercel env vars if you switch models.
INPUT_RATE_PER_MTOK = float(os.environ.get("INPUT_RATE_PER_MTOK", "1.0"))
OUTPUT_RATE_PER_MTOK = float(os.environ.get("OUTPUT_RATE_PER_MTOK", "5.0"))

# ─── Inference Config ────────────────────────────────────────────────────────
# Controls how the model generates responses. All values are configurable via
# Vercel env vars — no code change needed to tune behaviour per deployment.
# Only these 4 keys are valid for Bedrock's Converse API.
VALID_INFERENCE_PARAMS = {"maxTokens", "temperature", "topP", "stopSequences"}

INFERENCE_CONFIG = {
    "maxTokens": int(os.environ.get("MAX_TOKENS", "2048")),    # max output length
    "temperature": float(os.environ.get("TEMPERATURE", "0.7")), # 0=deterministic, 1=creative
}

# Validate at startup — catches typos or invalid keys before any request is served.
invalid_keys = [k for k in INFERENCE_CONFIG if k not in VALID_INFERENCE_PARAMS]
if invalid_keys:
    raise ValueError(f"Invalid inferenceConfig keys: {invalid_keys}. Valid keys: {VALID_INFERENCE_PARAMS}")

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


# ─── Tool Config ────────────────────────────────────────────────────────────
# Register tools the LLM can call. Add more tools here as you build them.
TOOL_CONFIG = {
    "tools": [os_info_tool.get_tool_spec()]
}

# Maps tool name → handler function
TOOL_HANDLERS = {
    "Shell_Tool": os_info_tool.fetch_data,
}


def invoke_tool(tool_use_block: dict) -> dict:
    """Run the tool the LLM requested and return the result."""
    name = tool_use_block["name"]
    tool_input = tool_use_block.get("input", {})
    tool_use_id = tool_use_block["toolUseId"]

    handler = TOOL_HANDLERS.get(name)
    if not handler:
        result = {"error": f"Unknown tool: {name}"}
    else:
        try:
            result = handler(tool_input)
        except Exception as e:
            result = {"error": str(e)}

    if DEBUG:
        logger.info(f"Tool {name} result: {json.dumps(result, default=str)}")

    return {
        "toolUseId": tool_use_id,
        "content": [{"json": result}],
    }


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

        # Tool-calling loop — keeps going until the model says end_turn.
        # Each iteration: call Bedrock → if it wants a tool, run it and loop back.
        conversation = bedrock_messages
        full_text = ""
        stop_reason = None
        input_tokens = 0
        output_tokens = 0

        while True:
            response = bedrock.converse(
                modelId=MODEL_ID,
                system=[{"text": SYSTEM_PROMPT}],
                messages=conversation,
                inferenceConfig=INFERENCE_CONFIG,
                toolConfig=TOOL_CONFIG,
            )

            # Accumulate token counts across all turns
            usage = response.get("usage", {})
            input_tokens += usage.get("inputTokens", 0)
            output_tokens += usage.get("outputTokens", 0)

            stop_reason = response["stopReason"]
            assistant_message = response["output"]["message"]
            conversation.append(assistant_message)

            if stop_reason == "end_turn":
                # Extract the final text response
                for block in assistant_message["content"]:
                    if "text" in block:
                        full_text = block["text"]
                break

            elif stop_reason == "tool_use":
                # Run each tool the model requested, collect results
                tool_results = []
                for block in assistant_message["content"]:
                    if "toolUse" in block:
                        if DEBUG:
                            logger.info(f"Tool call: {block['toolUse']['name']} input={block['toolUse'].get('input')}")
                        result = invoke_tool(block["toolUse"])
                        tool_results.append({"toolResult": result})

                # Feed tool results back as a user message
                conversation.append({"role": "user", "content": tool_results})

            else:
                # Unexpected stop — bail out
                full_text = f"Unexpected stop reason: {stop_reason}"
                break

        elapsed = round(time.time() - start, 2)
        if DEBUG:
            logger.info(
                f"stopReason={stop_reason} inputTokens={input_tokens} "
                f"outputTokens={output_tokens} duration={elapsed}s"
            )
            if stop_reason == "max_tokens":
                logger.warning("Response truncated: max_tokens reached")
            logger.info(f"[ASSISTANT] {full_text}")

        # Haiku 4.5 pricing: $1/MTok input, $5/MTok output
        credits = round(
            ((input_tokens or 0) / 1_000_000 * INPUT_RATE_PER_MTOK) +
            ((output_tokens or 0) / 1_000_000 * OUTPUT_RATE_PER_MTOK),
            6
        )

        # SSE (Server-Sent Events) format: "data: <json>\n\n"
        # The frontend reads this and renders the response text + metadata.
        payload = {
            "text": full_text,
            "elapsed": elapsed,
            "credits": credits,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        sse_body = f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n"
        return Response(content=sse_body, media_type="text/event-stream")

    except Exception as e:
        # Always log errors regardless of DEBUG setting
        logger.error(f"Bedrock error: {e}")
        return Response(
            content=f"data: {json.dumps({'error': str(e)})}\n\n",
            media_type="text/event-stream",
            status_code=500,
        )
