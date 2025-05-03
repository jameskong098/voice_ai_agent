'''
Voice AI Agent
This script sets up a FastAPI server that handles incoming voice calls via Twilio.
It uses a WebSocket connection to communicate with the Twilio service and processes audio using various services.
It includes speech-to-text (STT) and text-to-speech (TTS) capabilities, as well as a language model for conversation,
managed by a stateful flow manager.

Author: James Kong
Date: May 3rd, 2025
'''

# Standard Library Imports
import asyncio
import json
import os
import smtplib
import ssl
import sys
import urllib.parse
from email.message import EmailMessage
from pathlib import Path 
from typing import Dict, Any 

# Third-Party Imports
import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response
from fastapi.responses import HTMLResponse
from loguru import logger

# Pipecat Core Imports
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    AudioRawFrame,
    ErrorFrame,
    Frame,
    TextFrame,
    LLMMessagesFrame,
    EndFrame
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.ollama.llm import OLLamaLLMService
from pipecat.transports.network.fastapi_websocket import (
    FastAPIWebsocketTransport,
    FastAPIWebsocketParams,
)
from pipecat_flows import (
    FlowManager,
    FlowConfig,
    FlowArgs,
    FlowResult,
    ContextStrategy,
    ContextStrategyConfig,
)

# Ensure the project root is in the path if running script directly
sys.path.append(str(Path(__file__).parent.parent))

# Load environment variables
load_dotenv(override=True)

# --- Environment Variables ---
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY")
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID")

# --- Ollama Configuration ---
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:12b")
USE_OLLAMA = os.getenv("USE_OLLAMA", "false").lower() == "true" or not OPENAI_API_KEY

# --- SmartyStreets Address Validation ---
SMARTY_AUTH_ID = os.getenv("SMARTY_AUTH_ID")
SMARTY_AUTH_TOKEN = os.getenv("SMARTY_AUTH_TOKEN")
SMARTY_URL = os.getenv("SMARTY_URL")

# --- Email Configuration ---
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
RECIPIENT_EMAILS = os.getenv("RECIPIENT_EMAILS", "").split(',')
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = os.getenv("SMTP_PORT", 587)
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# --- FastAPI Configuration ---
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 8080))

app = FastAPI(title="Voice AI Agent")

# --- Global Storage (for simplicity, replace with DB in production) ---
# Added global dict to store call data temporarily
call_data_store: Dict[str, Dict[str, Any]] = {}

# --- Flow Handler Functions ---

class SimpleResult(FlowResult):
    success: bool
    message: str | None = None

async def verify_identity(args: FlowArgs) -> SimpleResult:
    """Placeholder: Verify patient identity."""
    name = args.get("name")
    birthday = args.get("birthday")
    logger.info(f"Verifying identity: Name={name}, Birthday={birthday}")
    # In a real app, we would query a database here
    verified = True  # Assume verified for now
    if verified:
        call_data_store.setdefault("global", {})['name'] = name
        call_data_store["global"]['birthday'] = birthday
        return SimpleResult(success=True, message="Identity verified.")
    else:
        return SimpleResult(success=False, message="Sorry, I couldn't verify that information.")

async def record_insurance(args: FlowArgs) -> SimpleResult:
    """Record insurance details."""
    provider = args.get("provider_name")
    payer_id = args.get("payer_id")
    logger.info(f"Recording insurance: Provider={provider}, PayerID={payer_id}")
    call_data_store.setdefault("global", {})['insurance_provider'] = provider
    call_data_store["global"]['insurance_payer_id'] = payer_id
    return SimpleResult(success=True, message="Insurance recorded.")

async def record_referral(args: FlowArgs) -> SimpleResult:
    """Record referral information."""
    has_referral = args.get("has_referral")
    physician = args.get("physician_name", "N/A")
    logger.info(f"Recording referral: HasReferral={has_referral}, Physician={physician}")
    call_data_store.setdefault("global", {})['has_referral'] = has_referral
    call_data_store["global"]['referring_physician'] = physician if has_referral else None
    return SimpleResult(success=True, message="Referral info recorded.")

async def record_reason(args: FlowArgs) -> SimpleResult:
    """Record reason for visit."""
    reason = args.get("reason")
    logger.info(f"Recording reason for visit: {reason}")
    call_data_store.setdefault("global", {})['visit_reason'] = reason
    return SimpleResult(success=True, message="Reason for visit recorded.")

async def validate_address(args: FlowArgs) -> SimpleResult:
    """Validate address using SmartyStreets."""
    address = args.get("address")
    logger.info(f"Validating address: {address}")

    if not SMARTY_AUTH_ID or not SMARTY_AUTH_TOKEN:
        logger.warning("SmartyStreets credentials not set. Skipping validation.")
        call_data_store.setdefault("global", {})['address'] = address  
        return SimpleResult(success=True, message="Address recorded (validation skipped).")

    params = {
        'auth-id': SMARTY_AUTH_ID,
        'auth-token': SMARTY_AUTH_TOKEN,
        'street': address,
        'candidates': 1
    }
    try:
        response = requests.get(SMARTY_URL, params=params)
        response.raise_for_status()
        results = response.json()
        if results:
            validated_address = results[0]['delivery_line_1']
            last_line = results[0]['last_line']
            full_address = f"{validated_address}, {last_line}"
            logger.info(f"Address validated: {full_address}")
            call_data_store.setdefault("global", {})['address'] = full_address
            return SimpleResult(success=True, message="Address validated.")
        else:
            logger.warning("Address validation failed: No candidates found.")
            return SimpleResult(success=False, message="I couldn't validate that address. Could you please provide it again?")
    except requests.exceptions.RequestException as e:
        logger.error(f"SmartyStreets API error: {e}")
        call_data_store.setdefault("global", {})['address'] = address + " (Unvalidated)"
        return SimpleResult(success=True, message="There was an issue validating the address, but I've recorded what you provided.")

async def record_contact(args: FlowArgs) -> SimpleResult:
    """Record contact information."""
    phone = args.get("phone")
    email = args.get("email", "N/A")
    logger.info(f"Recording contact info: Phone={phone}, Email={email}")
    call_data_store.setdefault("global", {})['phone'] = phone
    call_data_store["global"]['email'] = email
    return SimpleResult(success=True, message="Contact info recorded.")

async def schedule_appointment(args: FlowArgs) -> SimpleResult:
    """Placeholder: Schedule appointment."""
    provider = args.get("provider")
    time = args.get("appointment_time")
    logger.info(f"Scheduling appointment: Provider={provider}, Time={time}")
    call_data_store.setdefault("global", {})['scheduled_provider'] = provider
    call_data_store["global"]['scheduled_time'] = time
    return SimpleResult(success=True, message="Appointment scheduled.")

async def send_email(args: FlowArgs) -> SimpleResult:
    """Send confirmation email."""
    logger.info("Preparing to send confirmation email.")

    if not all([SENDER_EMAIL, RECIPIENT_EMAILS, SMTP_SERVER, SMTP_USERNAME, SMTP_PASSWORD]):
        logger.warning("Email configuration incomplete. Skipping email.")
        return SimpleResult(success=True, message="Email skipped due to configuration.")

    call_info = call_data_store.get("global", {})
    subject = "New Patient Intake Summary"
    body_lines = ["Patient Intake Summary:\n"]
    for key, value in call_info.items():
        body_lines.append(f"{key.replace('_', ' ').title()}: {value}")
    body = "\n".join(body_lines)

    em = EmailMessage()
    em['From'] = SENDER_EMAIL
    em['To'] = RECIPIENT_EMAILS  
    em['Subject'] = subject
    em.set_content(body)

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.starttls(context=context)
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.sendmail(SENDER_EMAIL, RECIPIENT_EMAILS, em.as_string())
        logger.info(f"Confirmation email sent successfully to {', '.join(RECIPIENT_EMAILS)}.")
        return SimpleResult(success=True, message="Confirmation email sent.")
    except smtplib.SMTPException as e:
        logger.error(f"Failed to send email: {e}")
        return SimpleResult(success=False, message="Failed to send confirmation email.")
    finally:
        # Clean up stored data after attempting email
        call_data_store.clear()
        logger.info("Cleaned up stored data.")


# --- Load and Prepare Flow Configuration ---

def load_flow_config(filepath: str) -> FlowConfig:
    """Loads flow config from JSON and maps handlers."""
    # Map handler names (strings) to actual function objects
    handler_map = {
        "verify_identity": verify_identity,
        "record_insurance": record_insurance,
        "record_referral": record_referral,
        "record_reason": record_reason,
        "validate_address": validate_address,
        "record_contact": record_contact,
        "schedule_appointment": schedule_appointment,
        "send_email": send_email,
    }

    config_path = Path(filepath)
    if not config_path.exists():
        raise FileNotFoundError(f"Flow configuration file not found: {filepath}")

    with open(config_path, 'r') as f:
        config_data = json.load(f)

    # Recursively replace handler strings with functions
    def map_handlers(node):
        if isinstance(node, dict):
            if node.get("handler") and isinstance(node["handler"], str):
                handler_name = node["handler"].split(":")[-1]
                if handler_name in handler_map:
                    node["handler"] = handler_map[handler_name]
                else:
                    logger.warning(f"Handler function '{handler_name}' not found in handler_map.")
            for key, value in node.items():
                map_handlers(value)
        elif isinstance(node, list):
            for item in node:
                map_handlers(item)

    map_handlers(config_data)
    return config_data

# Load the flow configuration globally or pass it appropriately
flow_config_path = os.getenv("FLOW_CONFIG_PATH", "/home/jameskong/voice_ai_agent/voice_ai_agent_call_flow.json")
try:
    flow_config = load_flow_config(flow_config_path)
    logger.info(f"Successfully loaded and processed flow configuration from {flow_config_path}")
except Exception as e:
    logger.error(f"Failed to load or process flow configuration: {e}")
    # Exit or handle appropriately if config is critical
    sys.exit(1)


# --- Routes ---
@app.get("/", response_class=HTMLResponse)
def status_page():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <title>Voice AI Agent Status</title>
    </head>
    <body>
      <h1>✅ Voice AI Agent is Running</h1>
      <p>If you see this page, ngrok is forwarding correctly.</p>
    </body>
    </html>
    """

@app.post("/voice")
def voice_webhook(request: Request):
    # Currently using grok URL for development
    # In production, this would be hosted on a public server
    ngrok_url = os.getenv("NGROK_URL")
    if not ngrok_url:
        logger.error("NGROK_URL environment variable not set.")
        # Return an error response to Twilio
        twiml_error = """
        <Response>
            <Say>Configuration error. Unable to connect the call.</Say>
        </Response>
        """
        # Use FastAPI's Response
        return Response(content=twiml_error, media_type='text/xml', status_code=500)
    logger.info(f"Incoming call... generating TwiML to connect to: {ngrok_url}")

    # Generate TwiML response
    twiml = f"""
    <Response>
        <Connect>
            <Stream url="{ngrok_url}" />
        </Connect>
        <Say>Connecting you to the AI agent. Please wait.</Say>
    </Response>
    """
    return Response(content=twiml, media_type='text/xml')

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket connection accepted.")
    task = None
    transport = None
    flow_manager = None 
    call_sid = "unknown_call" 

    try:
        # 1. Initial Twilio Handshake
        connected_msg_raw = await websocket.receive_text()
        logger.debug(f"Received first message: {connected_msg_raw}")
        connected_data = json.loads(connected_msg_raw)
        if connected_data.get("event") != "connected":
            logger.error(f"Expected 'connected' event, got: {connected_data.get('event')}")
            await websocket.close(code=1008, reason="Protocol error: Expected 'connected' event.")
            return

        start_msg_raw = await websocket.receive_text()
        logger.debug(f"Received second message: {start_msg_raw}")
        start_data = json.loads(start_msg_raw)
        if start_data.get("event") != "start":
            logger.error(f"Expected 'start' event, got: {start_data.get('event')}")
            await websocket.close(code=1008, reason="Protocol error: Expected 'start' event.")
            return

        stream_sid = start_data.get("start", {}).get("streamSid")
        call_sid = start_data.get("start", {}).get("callSid") 

        if not stream_sid or not call_sid:
            logger.error(f"Missing streamSid or callSid in start data: {start_data}")
            await websocket.close(code=1008, reason="Protocol error: Missing streamSid or callSid.")
            return

        logger.info(f"Twilio call started: CallSid={call_sid}, StreamSid={stream_sid}")

        # 2. Setup services
        if USE_OLLAMA:
            logger.info(f"Using Ollama LLM with model {OLLAMA_MODEL}")
            llm = OLLamaLLMService(
                model=OLLAMA_MODEL,
                base_url=f"{OLLAMA_HOST}/v1"
            )
        else:
            logger.info("Using OpenAI LLM with GPT-4o")
            llm = OpenAILLMService(
                api_key=OPENAI_API_KEY,
                model="gpt-4o"
            )

        stt = DeepgramSTTService(
            api_key=DEEPGRAM_API_KEY,
            model="nova-2-phonecall"
        )

        tts = CartesiaTTSService(
            api_key=CARTESIA_API_KEY,
            voice_id=CARTESIA_VOICE_ID,
            model_id="sonic-english",
            output_format="pcm_s16le", 
            sample_rate=8000 
        )

        # 3. Setup Transport with Serializer
        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_out_enabled=True,
                add_wav_header=False, 
                vad_enabled=True,
                vad_analyzer=SileroVADAnalyzer(),
                vad_audio_passthrough=True, 
                serializer=TwilioFrameSerializer(
                    stream_sid=stream_sid,
                    call_sid=call_sid,
                    account_sid=TWILIO_ACCOUNT_SID,
                    auth_token=TWILIO_AUTH_TOKEN,
                ),
            ),
        )

        # 4. Setup Context, Pipeline, and FlowManager 
        # Remove initial messages, FlowManager handles the start
        context = OpenAILLMContext()
        context_aggregator = llm.create_context_aggregator(context)

        pipeline = Pipeline(
            [
                transport.input(),          # Receive audio and control messages from Twilio
                stt,                        # Speech-to-Text
                context_aggregator.user(),  # Add user transcript to context
                llm,                        # Language Model processing (used by FlowManager)
                tts,                        # Text-to-Speech (used by FlowManager)
                transport.output(),         # Send audio and control messages back to Twilio
                context_aggregator.assistant(), # Add assistant response to context
            ]
        )

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=True,
                enable_vad=True,
                audio_in_sample_rate=8000,
                audio_out_sample_rate=8000,
            ),
        )

        flow_manager = FlowManager(
            task=task,
            llm=llm,
            context_aggregator=context_aggregator,
            tts=tts,
            flow_config=flow_config,
        )

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info(f"Transport client disconnected: {client}")
            # Clean up call data when client disconnects
            if call_sid in call_data_store:
                del call_data_store[call_sid]
                logger.info(f"[{call_sid}] Cleaned up stored data on disconnect.")
            if task:
                await task.queue_frames([EndFrame()])

        # 5. Initialize Flow and Run Pipeline 
        # Initialize the flow *before* running the pipeline task
        await flow_manager.initialize()

        runner = PipelineRunner(handle_sigint=False)
        await runner.run(task)

    except WebSocketDisconnect as e:
        logger.warning(f"WebSocket disconnected: {e.code} {e.reason}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON during handshake: {e}")
        # Attempt to close gracefully if possible
        if websocket.client_state == websocket.client_state.CONNECTED:
             await websocket.close(code=1008, reason="Protocol error: Invalid JSON.")
    except Exception as e:
        logger.exception(f"Error in WebSocket handler: {e}")
        # Attempt to close gracefully if possible
        if websocket.client_state == websocket.client_state.CONNECTED:
            await websocket.close(code=1011, reason="Internal server error.")
    finally:
        logger.info(f"Cleaning up resources for call {call_sid}.")
        # Clean up call data in case of errors or normal termination if not already done
        if call_sid in call_data_store:
            try:
                del call_data_store[call_sid]
                logger.info(f"[{call_sid}] Cleaned up stored data in finally block.")
            except KeyError:
                pass # Already deleted, possibly by disconnect handler
        if task:
            await task.cancel() # Ensure task is cancelled
        if transport:
            await transport.close() # Ensure transport is closed
        # Ensure WebSocket is closed if not already
        if websocket.client_state == websocket.client_state.CONNECTED:
            await websocket.close(code=1000)

if __name__ == "__main__":
    logger.info(f"Starting FastAPI server on {HOST}:{PORT}")

    uvicorn.run(
        "voice_ai_agent:app", 
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info"
    )
