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

from strands import Agent
from strands_tools import file_read

from strands.models import BedrockModel

# shell_tool is our custom tool in tools/shell_tool.py
sys.path.insert(0, str(Path(__file__).parent))

# file_read comes from the strands-agents-tools pip package

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── App ────────────────────────────────────────────────────────────────────
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Config ─────────────────────────────────────────────────────────────────
MODEL_ID     = os.environ.get("MODEL_ID")
AWS_REGION   = os.environ.get("AWS_REGION")
AWS_ROLE_ARN = os.environ.get("AWS_ROLE_ARN")
DEBUG        = os.environ.get("DEBUG", "true").lower() == "true"
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    (
        "You are a helpful, friendly, and concise assistant running on a Linux server. "
        "You have the following tools available:\n"
        "- file_read: read files, list directories, search file contents, and compare files\n"
    ),
)
INPUT_RATE_PER_MTOK  = float(os.environ.get("INPUT_RATE_PER_MTOK", "1.0"))
OUTPUT_RATE_PER_MTOK = float(os.environ.get("OUTPUT_RATE_PER_MTOK", "5.0"))

for _var, _val in [("MODEL_ID", MODEL_ID), ("AWS_REGION", AWS_REGION), ("AWS_ROLE_ARN", AWS_ROLE_ARN)]:
    if not _val:
        raise ValueError(f"Required environment variable {_var} is not set")


# ─── JWT Decode ─────────────────────────────────────────────────────────────
def decode_jwt_payload(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (4 - len(payload) % 4)
    return json.loads(base64.b64decode(payload))


# ─── AWS Credentials via OIDC ───────────────────────────────────────────────
def get_aws_credentials(oidc_token: str) -> dict:
    sts = boto3.client("sts", region_name=AWS_REGION)
    assumed = sts.assume_role_with_web_identity(
        RoleArn=AWS_ROLE_ARN,
        RoleSessionName="vercel-strands-session",
        WebIdentityToken=oidc_token,
    )
    creds = assumed["Credentials"]
    if DEBUG:
        logger.info(f"Role assumed, expires {creds['Expiration']}")
    return creds


# ─── History Replay ─────────────────────────────────────────────────────────
def run_history(agent: Agent, messages: list) -> str:
    """Replay prior conversation turns into the agent's memory.
    Vercel is stateless — each request starts a fresh Agent, so we feed it
    the previous messages before sending the real user message.
    Returns the last user message content.
    """
    for msg in messages[:-1]:
        if msg["role"] == "user":
            agent(msg["content"])
    return messages[-1]["content"]


# ─── Health Check ───────────────────────────────────────────────────────────
@app.get("/api/strands/health")
def health(request: Request):
    oidc_enabled = bool(request.headers.get("x-vercel-oidc-token"))
    return {"status": "ok", "model": MODEL_ID, "mode": "strands", "oidc": oidc_enabled}


# ─── Chat Endpoint ──────────────────────────────────────────────────────────
@app.post("/api/strands")
async def chat(request: Request):
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
        return Response(
            content=f"data: {json.dumps({'error': 'messages array is required'})}\n\n",
            media_type="text/event-stream",
            status_code=400,
        )

    if DEBUG:
        logger.info(f"Strands chat — {len(messages)} message(s)")

    try:
        creds = get_aws_credentials(oidc_token)
        start = time.time()

        # Build boto3 session with temporary OIDC credentials,
        # then pass it to BedrockModel — Strands uses it internally
        boto_session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=AWS_REGION,
        )
        bedrock_model = BedrockModel(
            model_id=MODEL_ID,
            boto_session=boto_session,
        )

        # callback_handler=None silences the word-by-word token logging to stdout
        agent = Agent(
            model=bedrock_model,
            tools=[file_read], # where the tools get called
            system_prompt=SYSTEM_PROMPT,
            callback_handler=None,
        )

        last_message = run_history(agent, messages)
        response = agent(last_message)

        elapsed = round(time.time() - start, 2)
        full_text = response.message["content"][0]["text"]

        # accumulated_usage is a plain dict: {'inputTokens': N, 'outputTokens': N}
        metrics = getattr(response, "metrics", None)
        usage = getattr(metrics, "accumulated_usage", {}) if metrics else {}
        input_tokens  = usage.get("inputTokens", 0)
        output_tokens = usage.get("outputTokens", 0)

        if DEBUG:
            logger.info(f"duration={elapsed}s input={input_tokens} output={output_tokens}")
            logger.info(f"[ASSISTANT] {full_text}")

        credits = round(
            (input_tokens / 1_000_000 * INPUT_RATE_PER_MTOK) +
            (output_tokens / 1_000_000 * OUTPUT_RATE_PER_MTOK),
            6,
        )

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
        logger.error(f"Strands error: {e}")
        return Response(
            content=f"data: {json.dumps({'error': str(e)})}\n\n",
            media_type="text/event-stream",
            status_code=500,
        )