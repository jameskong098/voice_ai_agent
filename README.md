# Voice AI Agent

![Kong Health Clinic](assets/Kong_Health_Clinic_Banner.png)

**Author:** James Kong

This project implements a voice-based AI agent designed to handle simulated patient intake over a phone call. It uses Twilio for telephony, integrates with speech-to-text (STT) and text-to-speech (TTS) services, and leverages a large language model (LLM) for conversational interaction.

[Video Demo](https://www.youtube.com/watch?v=hkxatmPiI08)

![Video Demo Screenshot](assets/Video%20Screenshot.png)

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
*   **Text-to-Speech (TTS):** Cartesia or Piper
*   **Language Model (LLM):** OpenAI or Ollama
*   **Address Validation:** SmartyStreets (requires API keys)
*   **Email:** Python `smtplib` (requires SMTP server configuration)

## Setup

1.  **Prerequisites:**
    * Python 3.10 or later
    * [Ngrok](https://ngrok.com/) account and CLI tool installed (for development)
    * Twilio account with a phone number
    * API keys for selected services

2.  **Clone the repository:**
    ```bash
    git clone <your-repo-url>
    cd voice_ai_agent
    ```

3.  **Set up a virtual environment:**
    ```bash
    # Create a virtual environment
    python -m venv venv
    
    # Activate the virtual environment
    # On Windows:
    venv\Scripts\activate
    # On macOS/Linux:
    source venv/bin/activate
    ```

4.  **Install dependencies:**
    ```bash
    # Install all required packages
    pip install -r requirements.txt 
    ```

5.  **Configure Environment Variables:** Create a `.env` file in the project root and add the necessary API keys and configuration details:
    ```env
    # Twilio
    TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    TWILIO_AUTH_TOKEN=your_auth_token
    TWILIO_PHONE_NUMBER=+1xxxxxxxxxx

    # Deepgram
    DEEPGRAM_API_KEY=your_deepgram_api_key

    # Cartesia
    USE_LOCAL_TTS=true # Set to true to use local TTS
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
    SMARTY_URL=https://us-street.api.smartystreets.com/street-address

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
    NGROK_URL=wss://your-ngrok-subdomain.ngrok-free.app/ws 
    ```

6.  **Optional: Set up local services:**
    * **For local LLM with Ollama:**
      ```bash
      # Install Ollama (see https://ollama.com for instructions)
      # Start the Ollama server
      ollama serve
      # In another terminal, pull a model with function calling capabilities
      ollama pull mistral
      ```
    
    * **For local TTS with Piper:**
      ```bash
      # Install Piper (see https://github.com/rhasspy/piper for instructions)
      # Start the Piper HTTP server with your chosen voice model
      python -m piper.http_server --host 0.0.0.0 --port 5000 --model /path/to/voice/model.onnx
      ```

7.  **Run Ngrok (for local development):** Expose your local server to the internet so Twilio can reach it.
    ```bash
    ngrok http 8080
    ```
    Update the `NGROK_URL` in your `.env` file with the HTTPS URL provided by Ngrok (ensure it points to your `/ws` endpoint, e.g., `wss://your-ngrok-subdomain.ngrok-free.app/ws` for the Twilio Stream URL, but the `NGROK_URL` env var in the code seems to expect the base HTTPS URL for the TwiML generation). *Note: The current code uses the NGROK_URL directly for the WebSocket stream, ensure it's the correct `wss://` URL when configuring Twilio.*

8.  **Configure Twilio:**
    *   Get a Twilio phone number.
    *   Configure the number's voice webhook to point to your server's `/voice` endpoint (e.g., `https://your-ngrok-subdomain.ngrok-free.app/voice`). Use `HTTP POST`.
    *   For testing with the free Twilio account, add your phone numbers as verified caller IDs in the Twilio console.

9.  **Create directory for email assets (optional):**
    ```bash
    mkdir -p assets
    # Add your clinic logo or other assets here for email templates
    ```

## Local Development vs Production

### Using Local Services to Save API Credits

During development, you can use local alternatives for expensive API services to minimize costs:

1. **Local LLM with Ollama:**
   - Set `USE_OLLAMA=true` in your `.env` file
   - Install [Ollama](https://ollama.com/) locally and run it (`ollama serve`)
   - Pull a model with tool use capabilities: `ollama pull mistral` or another suitable model
   - This eliminates OpenAI API usage costs during testing

2. **Local TTS with Piper:**
   - Set `USE_LOCAL_TTS=true` in your `.env` file
   - Set up [Piper TTS Server](https://github.com/rhasspy/piper/blob/master/src/python_run/README_http.md) locally
   - Choose a suitable [Piper voice](https://github.com/rhasspy/piper/blob/master/VOICES.md)
   - This eliminates Cartesia API costs during testing

3. **Handling Twilio and Deepgram:**
   - Twilio and Deepgram costs per call are minimal, so using these services even during development is reasonable
   - Each test call typically uses a negligible amount of API credits

### Twilio Free vs Paid Account Considerations

- **Free Twilio Account Limitations:**
  - Only phone numbers registered as "verified caller IDs" in your Twilio account can call your agent
  - Every call begins with a promotional message: "You're using the Twilio Free Trial..."
  - Limited functionality for testing with multiple users

- **Paid Twilio Account ($20 minimum):**
  - Requires minimum $20 to upgrade to a paid account
  - Allows any phone number to call your agent
  - Removes the promotional message
  - Recommended for any serious testing or production use

### Using Ngrok for Development

Ngrok creates a secure tunnel to your local development server:

1. Start your FastAPI server: `python voice_ai_agent.py`
2. In another terminal, run ngrok: `ngrok http 8080`
3. Copy the HTTPS URL provided by ngrok (e.g., `https://a1b2-192-168-1-1.ngrok-free.app`)
4. Update your `.env` file's `NGROK_URL` variable
5. Configure your Twilio phone number's webhook to point to this URL + `/voice` endpoint
6. Each time you restart ngrok, you'll need to update both your `.env` file and the Twilio webhook URL

### Production Deployment

For production use:

1. Deploy the code to a cloud service (AWS, Google Cloud, Azure, etc.)
2. Configure environment variables to use production APIs:
   - Set `USE_OLLAMA=false` to use OpenAI
   - Set `USE_LOCAL_TTS=false` to use Cartesia
   - Ensure all API keys are properly configured
3. Point your Twilio webhook to your production server's URL
4. Use a production-ready SMTP service for sending emails
5. Monitor API usage to manage costs effectively

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
