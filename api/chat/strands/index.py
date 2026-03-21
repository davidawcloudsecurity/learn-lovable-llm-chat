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

# ── Strands imports ──────────────────────────────────────────────────────────
# Agent: the main class that runs the tool loop for us
# tool: a decorator that turns a plain Python function into a tool the LLM can call
# BedrockModel: wraps boto3 bedrock so Strands knows how to talk to it
from strands import Agent, tool
from strands.models import BedrockModel

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
MODEL_ID    = os.environ.get("MODEL_ID")
AWS_REGION  = os.environ.get("AWS_REGION")
AWS_ROLE_ARN = os.environ.get("AWS_ROLE_ARN")
DEBUG       = os.environ.get("DEBUG", "true").lower() == "true"
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "You are a helpful, friendly, and concise assistant. Be clear and direct in your responses.",
)
INPUT_RATE_PER_MTOK  = float(os.environ.get("INPUT_RATE_PER_MTOK", "1.0"))
OUTPUT_RATE_PER_MTOK = float(os.environ.get("OUTPUT_RATE_PER_MTOK", "5.0"))

for _var, _val in [("MODEL_ID", MODEL_ID), ("AWS_REGION", AWS_REGION), ("AWS_ROLE_ARN", AWS_ROLE_ARN)]:
    if not _val:
        raise ValueError(f"Required environment variable {_var} is not set")


# ─── Shell Tool (Strands style) ──────────────────────────────────────────────
# In our raw boto3 version we had to write get_tool_spec() manually.
# With Strands, the @tool decorator reads the docstring and type hints
# and builds the tool spec automatically. Much less boilerplate.
import subprocess
import shlex

ALLOWED_COMMANDS = {
    "uname", "hostname", "uptime", "free", "df", "lscpu",
    "cat", "whoami", "id", "ps", "top", "lsb_release",
    "env", "printenv", "arch", "nproc", "lsmem",
}

@tool
def shell_tool(command: str) -> dict:
    """Run a read-only Linux shell command on the host server and return the output.
    Use this to answer questions about the OS, kernel, CPU, memory, disk, uptime,
    hostname, running processes, or any other system information.
    Only safe, read-only commands are permitted.
    Examples: 'uname -a', 'free -h', 'df -h', 'lscpu', 'uptime -p'

    Args:
        command: The Linux shell command to run.

    Returns:
        A dict with stdout, stderr, and exit_code.
    """
    command = command.strip()
    if not command:
        return {"error": "No command provided"}

    try:
        parts = shlex.split(command)
    except ValueError as e:
        return {"error": f"Invalid command syntax: {e}"}

    base_cmd = parts[0]
    if base_cmd not in ALLOWED_COMMANDS:
        return {
            "error": f"Command '{base_cmd}' is not permitted.",
            "allowed_commands": sorted(ALLOWED_COMMANDS),
        }

    try:
        result = subprocess.run(parts, capture_output=True, text=True, timeout=5)
        return {
            "command": command,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "exit_code": result.returncode,
        }
    except FileNotFoundError:
        return {"error": f"Command not found: {base_cmd}"}
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out after 5 seconds"}
    except Exception as e:
        return {"error": str(e)}


# ─── JWT Decode ─────────────────────────────────────────────────────────────
def decode_jwt_payload(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (4 - len(payload) % 4)
    return json.loads(base64.b64decode(payload))


# ─── AWS Credentials via OIDC ───────────────────────────────────────────────
# Same as the raw boto3 version — we still need to assume the IAM role
# using the Vercel OIDC token before we can call Bedrock.
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
        return {"error": "messages array is required"}

    if DEBUG:
        logger.info(f"Strands chat — {len(messages)} message(s)")

    try:
        creds = get_aws_credentials(oidc_token)
        start = time.time()

        # ── Build a BedrockModel with the temporary OIDC credentials ────────
        # This is the Strands way of configuring which model to use.
        # We pass the temporary AWS credentials so it can call Bedrock.
        bedrock_model = BedrockModel(
            model_id=MODEL_ID,
            region_name=AWS_REGION,
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )

        # ── Build the Agent ──────────────────────────────────────────────────
        # This is the key difference from raw boto3.
        # We don't write a while loop — Agent handles it internally.
        # tools=[shell_tool] registers our @tool function.
        # Strands reads the docstring to build the tool spec for the LLM.
        agent = Agent(
            model=bedrock_model,
            tools=[shell_tool],
            system_prompt=SYSTEM_PROMPT,
        )

        # ── Replay conversation history ──────────────────────────────────────
        # Strands Agent has its own internal message list (agent.messages).
        # Since each Vercel request is stateless, we need to feed the full
        # conversation history in before asking the final question.
        # We replay all messages except the last one as context, then invoke
        # the agent with the last user message.
        #
        # Why not just pass all messages at once?
        # Strands Agent is designed to be called turn by turn, not given a
        # pre-built history. So we simulate the history by calling agent()
        # for each prior turn, then the real call is the last message.
        history = messages[:-1]   # everything except the last message
        last_message = messages[-1]["content"]

        # Replay prior turns silently to build up agent.messages context
        for msg in history:
            if msg["role"] == "user":
                # We call the agent but discard the response — we only care
                # about the conversation history being built up in agent.messages
                agent(msg["content"])

        # Now make the real call with the latest user message
        response = agent(last_message)

        elapsed = round(time.time() - start, 2)

        # ── Extract text and token usage from Strands response ───────────────
        # response.message is the raw Bedrock message dict
        # response.metrics has token counts if available
        full_text = response.message["content"][0]["text"]

        # Strands exposes usage via response.metrics or the message itself
        input_tokens  = getattr(response, "metrics", {}).get("inputTokens", 0) if hasattr(response, "metrics") else 0
        output_tokens = getattr(response, "metrics", {}).get("outputTokens", 0) if hasattr(response, "metrics") else 0

        if DEBUG:
            logger.info(f"Strands response duration={elapsed}s")
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
