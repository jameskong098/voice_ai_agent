'''
Voice AI Agent
This script sets up a FastAPI server that handles incoming voice calls via Twilio.
It uses a WebSocket connection to communicate with the Twilio service and processes audio using various services.
It includes speech-to-text (STT) and text-to-speech (TTS) capabilities, as well as a language model for conversation.

Author: James Kong
Date: May 3rd, 2025
'''

# Standard Library Imports
import asyncio
import json
import os
import smtplib
import ssl
import urllib.parse
from email.message import EmailMessage

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

        # 4. Setup Context and Pipeline
        messages = [
            {
                "role": "system",
                "content": "You are a helpful AI assistant on a phone call. Keep your responses concise and conversational. Your responses will be converted to speech.",
            },
            {"role": "assistant", "content": "Hello! How can I help you today?"}
        ]
        context = OpenAILLMContext(messages=messages)
        context_aggregator = llm.create_context_aggregator(context)

        pipeline = Pipeline(
            [
                transport.input(),          # Receive audio and control messages from Twilio
                stt,                        # Speech-to-Text
                context_aggregator.user(),  # Add user transcript to context
                llm,                        # Language Model processing
                tts,                        # Text-to-Speech
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

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info(f"Transport client disconnected: {client}")
            if task:
                await task.queue_frames([EndFrame()]) 

        # 6. Run the Pipeline
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
        logger.info(f"Cleaning up resources for call {call_sid if 'call_sid' in locals() else 'N/A'}.")
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
    