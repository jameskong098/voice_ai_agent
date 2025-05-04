# Voice AI Agent

This project implements a voice-based AI agent designed to handle simulated patient intake over a phone call. It uses Twilio for telephony, integrates with speech-to-text (STT) and text-to-speech (TTS) services, and leverages a large language model (LLM) for conversational interaction.

## Features

*   **Handles Phone Calls:** Connects to Twilio via WebSocket to manage incoming voice calls and stream audio.
*   **Conversational AI:** Engages users in natural conversation using either OpenAI (GPT-4o) or a local Ollama model.
*   **Speech-to-Text:** Transcribes the caller's speech in real-time using Deepgram.
*   **Text-to-Speech:** Synthesizes the AI's responses into natural-sounding speech using Cartesia.
*   **Information Gathering:** Designed to collect key information during the call, including:
    *   Name and Date of Birth
    *   Insurance Payer Name and ID
    *   Referral details (if applicable)
    *   Chief Medical Complaint / Reason for call
    *   Address (with validation via SmartyStreets API)
    *   Contact Information (Phone Number, optional Email)
*   **Appointment Scheduling Simulation:** Offers fictional available providers and appointment times.
*   **Call Completion Logic:** Aims to ensure all necessary information is gathered before concluding the interaction.
*   **Email Confirmation:** Sends a confirmation email summarizing appointment details upon call completion.

## Technology Stack

*   **Programming Language:** Python 3
*   **Framework:** FastAPI (for web server and WebSocket handling)
*   **AI Voice Pipeline:** Pipecat AI
*   **Telephony:** Twilio (Voice API, Media Streams)
*   **Speech-to-Text (STT):** Deepgram
*   **Text-to-Speech (TTS):** Cartesia
*   **Language Model (LLM):** OpenAI or Ollama
*   **Address Validation:** SmartyStreets (requires API keys)
*   **Email:** Python `smtplib` (requires SMTP server configuration)

## Setup

1.  **Clone the repository:**
    ```bash
    git clone <your-repo-url>
    cd voice_ai_agent
    ```
2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt 
    ```
3.  **Configure Environment Variables:** Create a `.env` file in the project root and add the necessary API keys and configuration details:
    ```env
    # Twilio
    TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    TWILIO_AUTH_TOKEN=your_auth_token

    # Deepgram
    DEEPGRAM_API_KEY=your_deepgram_api_key

    # Cartesia
    CARTESIA_API_KEY=your_cartesia_api_key
    CARTESIA_VOICE_ID=your_cartesia_voice_id # e.g., c599a4a1-f7f3-48ca-846e-585155c676b0

    # OpenAI (Optional, if not using Ollama)
    OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx

    # Ollama (Optional, if not using OpenAI or for development)
    USE_OLLAMA=true # Set to true to use Ollama
    OLLAMA_HOST=http://localhost:11434 # Adjust if needed
    OLLAMA_MODEL=mistral # Or your preferred model (make sure it supports tooling)

    # SmartyStreets (For Address Validation)
    SMARTY_AUTH_ID=your_smarty_auth_id
    SMARTY_AUTH_TOKEN=your_smarty_auth_token

    # Email Configuration
    SENDER_EMAIL=your_sender_email@example.com
    RECIPIENT_EMAILS=recipient1@example.com,recipient2@example.com
    SMTP_SERVER=smtp.example.com
    SMTP_PORT=587
    SMTP_USERNAME=your_smtp_username
    SMTP_PASSWORD=your_smtp_password

    # Server/Ngrok
    HOST=0.0.0.0
    PORT=8080
    NGROK_URL=https://your-ngrok-subdomain.ngrok-free.app # Your Ngrok forwarding URL for /ws endpoint
    ```
4.  **Run Ngrok (for local development):** Expose your local server to the internet so Twilio can reach it.
    ```bash
    ngrok http 8080
    ```
    Update the `NGROK_URL` in your `.env` file with the HTTPS URL provided by Ngrok (ensure it points to your `/ws` endpoint, e.g., `wss://your-ngrok-subdomain.ngrok-free.app/ws` for the Twilio Stream URL, but the `NGROK_URL` env var in the code seems to expect the base HTTPS URL for the TwiML generation). *Note: The current code uses the NGROK_URL directly for the WebSocket stream, ensure it's the correct `wss://` URL when configuring Twilio.*

5.  **Configure Twilio:**
    *   Get a Twilio phone number.
    *   Configure the number's voice webhook to point to your server's `/voice` endpoint (e.g., `https://your-ngrok-subdomain.ngrok-free.app/voice`). Use `HTTP POST`.

## Running the Agent

Start the FastAPI server:

```bash
python voice_ai_agent.py
```

Now, call your Twilio phone number. Twilio will hit the `/voice` endpoint, which returns TwiML instructing Twilio to connect to the WebSocket server (`/ws`). The Pipecat pipeline will then handle the call.

## How it Works

1.  A user calls the configured Twilio phone number.
2.  Twilio sends an HTTP POST request to the `/voice` webhook on the FastAPI server.
3.  The `/voice` endpoint responds with TwiML containing a `<Connect><Stream>` command, pointing to the `/ws` WebSocket endpoint (using the `NGROK_URL`).
4.  Twilio establishes a WebSocket connection to `/ws`.
5.  The `websocket_endpoint` function handles the WebSocket connection:
    *   It performs the initial Twilio handshake.
    *   It initializes the necessary services (STT, LLM, TTS) and the Pipecat transport/pipeline.
    *   The Pipecat pipeline processes the audio stream from Twilio:
        *   Audio -> STT (Deepgram) -> Text
        *   User Text -> LLM Context Aggregator -> LLM (OpenAI/Ollama) -> AI Text Response
        *   AI Text Response -> TTS (Cartesia) -> Audio
        *   AI Audio -> Sent back to Twilio over WebSocket.
6.  The conversation continues until the call ends or the agent determines all required information is collected.
7.  Upon call completion (or potentially triggered by the LLM), an email summary is sent.

## Documentation / Example References
- [Pipecat Flows - FlowManager](https://docs.pipecat.ai/server/frameworks/flows/pipecat-flows#flowmanager)
- [FlowManager Editor](https://flows.pipecat.ai/)
- [Cerebrium AI - Twilio Voice Agent Example](https://docs.cerebrium.ai/v4/examples/twilio-voice-agent)
- [Pipecat Flows - Static Patient Intake Example](https://github.com/pipecat-ai/pipecat-flows/blob/main/examples/static/patient_intake_openai.py)
- [Pipecat - Patient Intake Examples](https://github.com/pipecat-ai/pipecat/tree/main/examples/patient-intake)
- [Pipecat Flows - Dynamic Insurance Example](https://github.com/pipecat-ai/pipecat-flows/blob/main/examples/dynamic/insurance_gemini.py)

## Architecture References
- [Twilio](https://www.twilio.com/en-us)
- [Deepgram - Speech-to-Text](https://deepgram.com/product/speech-to-text)
- [Cartesia - Text-to-Speech](https://cartesia.ai/)
- [SmartyStreets](https://www.smarty.com/)
- [Ollama](https://ollama.com/search?c=tools)
- [Piper TTS Server](https://github.com/rhasspy/piper/blob/master/src/python_run/README_http.md)
- [Piper Integration w/ PipeCat](https://docs.pipecat.ai/server/services/tts/piper#piper)
- [Piper Voices](https://github.com/rhasspy/piper/blob/master/VOICES.md)
