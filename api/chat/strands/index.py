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
from strands_tools import file_read, file_write, http_request

from strands.models import BedrockModel

sys.path.insert(0, str(Path(__file__).parent))

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
DEBUG        = os.environ.get("DEBUG", "false").lower() == "true"
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "You are a helpful assistant with file_read, file_write, and http_request tools. IMPORTANT: Never use recursive file listing on large directories. Always use non-recursive mode first.",
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


# ─── Callback Handler ───────────────────────────────────────────────────────
def make_callback_handler(trace: list) -> callable:
    """
    Returns a callback that logs every LLM/tool event and appends it to `trace`.
    `trace` is a list of dicts — one per event — returned to the client so you
    can see exactly what the agent did, in order.

    Strands calls this with keyword-only arguments. Known keys:
      - data          : streaming token text (we skip this to avoid noise)
      - complete      : bool, True when the full message is assembled
      - current_tool_use : dict with 'name' and 'input' when a tool is being called
      - message       : full assembled message dict (role + content blocks)
      - event         : raw Bedrock converse-stream event (low-level, rarely needed)
    """

    def callback_handler(**kwargs):
        # ── Skip raw streaming tokens (too noisy) ──────────────────────────
        if "data" in kwargs and not kwargs.get("complete"):
            return

        # ── LLM finished generating a message ─────────────────────────────
        if kwargs.get("complete") and "message" in kwargs:
            message = kwargs["message"]
            for block in message.get("content", []):

                # Plain text thinking/response
                if block.get("type") == "text" and block.get("text", "").strip():
                    entry = {
                        "event": "llm_text",
                        "text": block["text"].strip(),
                    }
                    trace.append(entry)
                    logger.info(f"[LLM] {block['text'].strip()[:300]}")

                # Tool call — LLM decided to use a tool
                elif block.get("type") == "tool_use":
                    entry = {
                        "event": "tool_call",
                        "tool": block.get("name"),
                        "input": block.get("input"),
                    }
                    trace.append(entry)
                    logger.info(
                        f"[TOOL CALL] {block.get('name')} "
                        f"input={json.dumps(block.get('input'), default=str)[:300]}"
                    )

                # Tool result — what the tool actually returned
                elif block.get("type") == "tool_result":
                    content_blocks = block.get("content", [])
                    result_text = " ".join(
                        b.get("text", "") for b in content_blocks if b.get("type") == "text"
                    )
                    entry = {
                        "event": "tool_result",
                        "tool_use_id": block.get("tool_use_id"),
                        "result_preview": result_text[:500],  # cap size for readability
                        "result_length": len(result_text),
                    }
                    trace.append(entry)
                    logger.info(
                        f"[TOOL RESULT] id={block.get('tool_use_id')} "
                        f"len={len(result_text)} preview={result_text[:200]}"
                    )

        # ── Active tool use (fires while tool is mid-call) ─────────────────
        # Disabled to reduce log noise - tool calls are logged when complete above
        # elif "current_tool_use" in kwargs and kwargs["current_tool_use"]:
        #     tool_info = kwargs["current_tool_use"]
        #     logger.info(
        #         f"[TOOL RUNNING] {tool_info.get('name')} "
        #         f"input={json.dumps(tool_info.get('input'), default=str)[:200]}"
        #     )

    return callback_handler


# ─── History Replay ─────────────────────────────────────────────────────────
def run_history(agent: Agent, messages: list) -> str:
    """Replay prior conversation turns into the agent's memory."""
    # Skip history entirely to avoid context overflow
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
        logger.info(f"[USER] {messages[-1].get('content', '')[:300]}")

    try:
        creds = get_aws_credentials(oidc_token)
        start = time.time()

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

        # trace collects every event in order — returned to client for inspection
        trace = []
        callback = make_callback_handler(trace)

        agent = Agent(
            model=bedrock_model,
            tools=[file_read, file_write, http_request],
            system_prompt=SYSTEM_PROMPT,
            callback_handler=callback,
        )

        last_message = run_history(agent, messages)
        response = agent(last_message)

        elapsed = round(time.time() - start, 2)

        # Extract final text response — Bedrock blocks use {"text": "..."} not {"type":"text"}
        content_blocks = response.message.get("content", [])
        text_blocks = [b["text"] for b in content_blocks if "text" in b]
        full_text = "\n".join(text_blocks).strip() or response.message["content"][0].get("text", "")

        # Token usage
        metrics = getattr(response, "metrics", None)
        usage = getattr(metrics, "accumulated_usage", {}) if metrics else {}
        input_tokens  = usage.get("inputTokens", 0)
        output_tokens = usage.get("outputTokens", 0)

        credits = round(
            (input_tokens / 1_000_000 * INPUT_RATE_PER_MTOK) +
            (output_tokens / 1_000_000 * OUTPUT_RATE_PER_MTOK),
            6,
        )

        # Summarise what tools were actually used
        tools_used = [e["tool"] for e in trace if e["event"] == "tool_call"]

        if DEBUG:
            logger.info(f"duration={elapsed}s input={input_tokens} output={output_tokens}")
            logger.info(f"tools_used={tools_used}")
            logger.info(f"[ASSISTANT] {full_text[:300]}")

        payload = {
            "text": full_text,
            "elapsed": elapsed,
            "credits": credits,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            # ↓ These let you verify on the client side what actually happened
            "tools_used": tools_used,          # e.g. ["file_read", "file_read"]
            "trace": trace,                    # full ordered event log
        }
        sse_body = f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n"
        return Response(content=sse_body, media_type="text/event-stream")

    except Exception as e:
        logger.error(f"Strands error: {e}", exc_info=True)
        return Response(
            content=f"data: {json.dumps({'error': str(e)})}\n\n",
            media_type="text/event-stream",
            status_code=500,
        )
