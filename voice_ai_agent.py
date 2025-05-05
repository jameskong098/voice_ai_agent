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
from contextlib import asynccontextmanager 
from datetime import datetime, timedelta
from email import encoders
from email.message import EmailMessage
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage 
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path 
from typing import Dict, Any 

# Third-Party Imports
import aiohttp 
import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response
from fastapi.responses import HTMLResponse
from icalendar import Calendar, Event
from loguru import logger
import pytz

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
from pipecat.services.piper.tts import PiperTTSService 
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

# --- TTS Configuration ---
USE_LOCAL_TTS = os.getenv("USE_LOCAL_TTS")
PIPER_TTS_URL = os.getenv("PIPER_TTS_URL", "http://localhost:5000") 

# --- Ollama Configuration ---
OLLAMA_HOST = os.getenv("OLLAMA_HOST")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL")
USE_OLLAMA = os.getenv("USE_OLLAMA", "false").lower() == "true" or not OPENAI_API_KEY

# --- SmartyStreets Address Validation ---
SMARTY_AUTH_ID = os.getenv("SMARTY_AUTH_ID")
SMARTY_AUTH_TOKEN = os.getenv("SMARTY_AUTH_TOKEN")
SMARTY_URL = os.getenv("SMARTY_URL")

# --- Email Configuration ---
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = os.getenv("SMTP_PORT", 587)
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# --- FastAPI Configuration ---
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 8080))

# --- Global State ---
# Global aiohttp session for Piper TTS
http_session: aiohttp.ClientSession | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create session on startup
    global http_session
    http_session = aiohttp.ClientSession()
    logger.info("Created aiohttp session for Piper TTS.")
    yield
    # Close session on shutdown
    if http_session:
        await http_session.close()
        logger.info("Closed aiohttp session.")

app = FastAPI(title="Voice AI Agent", lifespan=lifespan) 

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

def format_date_with_ordinal(dt_str, include_time=True):
    """Format a date string to a professional format with ordinal suffix."""
    try:
        # Try to parse the datetime string
        if include_time:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        else:
            dt = datetime.strptime(dt_str, "%Y-%m-%d")
        
        # Get day with ordinal suffix
        day = dt.day
        if 4 <= day <= 20 or 24 <= day <= 30:
            suffix = "th"
        else:
            suffix = ["st", "nd", "rd"][day % 10 - 1] if day % 10 < 4 else "th"
        
        # Format with day of week, month, day with suffix, year
        if include_time:
            # Include time with AM/PM
            return dt.strftime("%A, %B ") + f"{day}{suffix}, {dt.year} at {dt.strftime('%I:%M %p')} EST"
        else:
            return dt.strftime("%A, %B ") + f"{day}{suffix}, {dt.year}"
    
    except (ValueError, TypeError):
        # If parsing fails, return the original string
        return dt_str

async def send_email(args=None):
    """Send a confirmation email to the patient with their appointment details."""
    logger.info("Attempting to finalize intake and send internal notification.")
    
    # Check if patient provided an email. Skip internal email if not.
    # Modify this condition if the internal email should *always* be sent.
    if call_data_store["global"]["email"] == "N/A" or not call_data_store["global"]["email"]:
        logger.info("Patient did not provide an email address. Skipping internal email notification.")
        # Clean up stored data even if email is skipped
        if "global" in call_data_store:
            call_data_store.clear()
            logger.info("Cleaned up stored data.")
        return SimpleResult(success=True, message="Intake complete (internal email skipped).")

    # Proceed with sending email if configuration is complete and patient email was provided
    if not all([SENDER_EMAIL, SMTP_SERVER, SMTP_USERNAME, SMTP_PASSWORD]):
        logger.warning("Email configuration incomplete. Skipping email.")
        # Clean up stored data even if email is skipped due to config
        if "global" in call_data_store:
            call_data_store.clear()
            logger.info("Cleaned up stored data.")
        return SimpleResult(success=True, message="Intake complete (email skipped due to configuration).")

    subject = f"Patient Intake Summary - {call_data_store['global']['name']}"

    # --- Image Handling ---
    image_cid = "logo_image"
    image_path = Path(__file__).parent / "assets" / "Kong_Health_Clinic_Banner.png" 
    img_data = None
    try:
        with open(image_path, 'rb') as fp:
            img_data = fp.read()
        logger.info(f"Successfully read logo image from {image_path}")
    except FileNotFoundError:
        logger.warning(f"Logo image not found at {image_path}. Email will be sent without logo.")
    # --- End Image Handling ---

    # Format appointment time with ordinal but leave birthday in YYYY-MM-DD format
    appointment_time = format_date_with_ordinal(call_data_store["global"].get("scheduled_time", ""), include_time=True)
    birth_date = call_data_store["global"].get("birthday", "") 

    html_body = f"""
    <html>
    <head>
        <style>
            body {{ font-family: sans-serif; line-height: 1.6; }}
            table {{ border-collapse: collapse; width: 100%; max-width: 600px; margin-top: 20px; }}
            th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #ddd; }}
            th {{ background-color: #f2f2f2; }}
            .logo {{ max-width: 200px; height: auto; margin-bottom: 20px; }} /* Style for the logo */
        </style>
    </head>
    <body>
        {f'<img src="cid:{image_cid}" alt="Kong Health Clinic Logo" class="logo"><br>' if img_data else ''}
        <h2>Patient Intake Summary</h2>
        <p>Dear {call_data_store['global']['name']},</p>
        <p>Thank you for completing the intake process with Kong Health Clinic. Here is a summary of the information provided:</p>
        <table>
            <tr><th>Field</th><th>Information</th></tr>
    """
    
    # Special handling for formatted dates - only appointment time gets special formatting
    special_formats = {
        "scheduled_time": appointment_time
    }
    
    for key, value in call_data_store["global"].items():
        # Skip email in the table body as it's the recipient
        if key == 'email':
            continue
        formatted_key = key.replace('_', ' ').title()
        
        # Use specially formatted values for dates (except birthday)
        if key in special_formats:
            display_value = special_formats[key]
        else:
            display_value = value if value is not None else 'N/A'
            
        html_body += f"<tr><td>{formatted_key}</td><td>{display_value}</td></tr>\n"

    html_body += """
        </table>
        <p>If any of this information looks incorrect, please let us know as soon as possible so we can update your records.</p>

        <p>We’re looking forward to seeing you and making sure you receive the care you need.</p>

        <p>Sincerely,<br>Kong Health Clinic</p>
    </body>
    <footer style="font-size: 0.9em; color: #666; margin-top: 30px; border-top: 1px solid #ccc; padding-top: 10px;">
        <p>Kong Health Clinic</p>
        <p>123 Wellness Way, Suite 400<br>Boston, MA 02118</p>
        <p>Phone: (617) 555-0199<br>Email: info@konghealthclinic.com</p>
        <p>This message was sent by an automated system. Please do not reply directly to this email.</p>
    </footer>
    </html>
    """

    # Completely rebuild the email structure using explicit MIME parts
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.image import MIMEImage
    
    # Create the root message - use 'alternative' as the main content type
    # This prioritizes HTML content over plain text in email clients
    root_message = MIMEMultipart('alternative')
    root_message["Subject"] = "Your Appointment Confirmation"
    root_message["From"] = SENDER_EMAIL
    root_message["To"] = call_data_store["global"]["email"]
    
    # Create the plain text version with formatted dates
    text_content = f"""
    Kong Health Clinic
    Appointment Confirmation
    
    Thank you for scheduling your appointment with us!
    
    Details:
    Name: {call_data_store["global"]["name"]}
    Provider: {call_data_store["global"]["scheduled_provider"]}
    Date/Time: {appointment_time}
    Visit Reason: {call_data_store["global"].get("visit_reason", "N/A")}
    
    Please arrive 15 minutes before your appointment time.
    """
    
    # Add plain text to the root message
    plain_part = MIMEText(text_content, 'plain')
    root_message.attach(plain_part)
    
    # Create the related part for HTML with inline images
    related_part = MIMEMultipart('related')
    
    # Read the logo image
    logo_path = Path(__file__).parent / "assets" / "Kong_Health_Clinic_Banner.png"
    img_cid = "logo@konghealth.clinic"  # Content ID for the image
    
    # Create the HTML content with proper reference to the embedded image
    html_content = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ text-align: center; margin-bottom: 20px; }}
            .details {{ background-color: #f9f9f9; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
            .patient-info {{ background-color: #f0f7ff; padding: 15px; border-radius: 5px; }}
            h1 {{ color: #2a5885; }}
            h2 {{ color: #336699; margin-top: 20px; }}
            table {{ width: 100%; border-collapse: collapse; }}
            table td {{ padding: 8px; border-bottom: 1px solid #ddd; }}
            td:first-child {{ font-weight: bold; width: 40%; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <img src="cid:{img_cid}" alt="Kong Health Clinic Logo" style="max-width:100%;">
                <h1>Appointment Confirmation</h1>
            </div>
            <p>Dear {call_data_store["global"]["name"]},</p>
            <p>Thank you for scheduling your appointment with us!</p>
            
            <div class="details">
                <h2>Appointment Details</h2>
                <ul>
                    <li><strong>Provider:</strong> {call_data_store["global"]["scheduled_provider"]}</li>
                    <li><strong>When:</strong> {appointment_time}</li>
                    <li><strong>Visit Reason:</strong> {call_data_store["global"].get("visit_reason", "N/A")}</li>
                </ul>
            </div>
            
            <div class="patient-info">
                <h2>Patient Information Summary</h2>
                <table>
                    <tr>
                        <td>Name</td>
                        <td>{call_data_store["global"].get("name", "N/A")}</td>
                    </tr>
                    <tr>
                        <td>Date of Birth</td>
                        <td>{birth_date}</td>
                    </tr>
                    <tr>
                        <td>Phone Number</td>
                        <td>{call_data_store["global"].get("phone", "N/A")}</td>
                    </tr>
                    <tr>
                        <td>Email</td>
                        <td>{call_data_store["global"].get("email", "N/A")}</td>
                    </tr>
                    <tr>
                        <td>Address</td>
                        <td>{call_data_store["global"].get("address", "N/A")}</td>
                    </tr>
                    <tr>
                        <td>Insurance Provider</td>
                        <td>{call_data_store["global"].get("insurance_provider", "N/A")}</td>
                    </tr>
                    <tr>
                        <td>Insurance Payer ID</td>
                        <td>{call_data_store["global"].get("insurance_payer_id", "N/A")}</td>
                    </tr>
                    <tr>
                        <td>Referral</td>
                        <td>{"Yes" if call_data_store["global"].get("has_referral") else "No"}</td>
                    </tr>
                    <tr>
                        <td>Referring Physician</td>
                        <td>{call_data_store["global"].get("referring_physician", "N/A")}</td>
                    </tr>
                </table>
            </div>
            
            <p>Please arrive 15 minutes before your appointment time.</p>
            <p>If you need to reschedule, please call us at (555) 123-4567.</p>
            <p>We look forward to seeing you!</p>
            <p><em>- The Kong Health Clinic Team</em></p>
        </div>
    </body>
    </html>
    """
    
    # Create the HTML part
    html_part = MIMEText(html_content, 'html')
    
    # Add the HTML part to the related part
    related_part.attach(html_part)
    
    # Add the image to the related part if it exists
    if logo_path.exists():
        logger.info(f"Successfully read logo image from {logo_path}")
        with open(logo_path, "rb") as img_file:
            img_data = img_file.read()
            img = MIMEImage(img_data)
            img.add_header("Content-ID", f"<{img_cid}>")
            # Setting Content-Disposition as inline is critical for embedded images
            img.add_header("Content-Disposition", "inline", filename="logo.png")
            related_part.attach(img)
    else:
        logger.warning(f"Logo image not found at {logo_path}")
    
    # Add the related part to the root message
    root_message.attach(related_part)
    
    # --- ICS Calendar Attachment ---
    try:
        appointment_datetime = datetime.strptime(call_data_store["global"]["scheduled_time"], "%Y-%m-%d %H:%M")
        
        # Use Eastern timezone for the appointment (consistent with EST in email display)
        eastern = pytz.timezone('US/Eastern')
        appointment_datetime_eastern = eastern.localize(appointment_datetime)
        
        cal = Calendar()
        cal.add('prodid', '-//Kong Health Clinic//Appointment System//EN')
        cal.add('version', '2.0')
        cal.add('method', 'REQUEST')  
        
        event = Event()
        event.add('summary', f"Appointment with {call_data_store['global']['scheduled_provider']} at Kong Health Clinic")
        event.add('dtstart', appointment_datetime_eastern)
        event.add('dtend', appointment_datetime_eastern + timedelta(hours=1))
        event.add('dtstamp', datetime.now(pytz.UTC))
        event.add('location', 'Kong Health Clinic, 123 Wellness Way, Suite 400, Boston, MA 02118')
        
        description = (f"Appointment for: {call_data_store['global']['name']}\n"
                      f"Visit Reason: {call_data_store['global'].get('visit_reason', 'N/A')}\n\n"
                      "Please arrive 15 minutes before your scheduled appointment time.\n"
                      "If you need to reschedule, please call us at (555) 123-4567.")
        event.add('description', description)
        
        organizer = f"mailto:{SENDER_EMAIL}"
        event.add('organizer', organizer)
        
        # Add attendee (patient)
        event.add('attendee', f"mailto:{call_data_store['global']['email']}")
        
        # Add unique identifier for the event
        event.add('uid', f"{call_data_store['global']['name'].replace(' ', '_')}-{appointment_datetime.strftime('%Y%m%d%H%M')}@konghealthclinic.com")
        
        cal.add_component(event)
        
        ics_content = cal.to_ical()
        ics_part = MIMEBase('application', 'text/calendar', method='REQUEST')
        ics_part.set_payload(ics_content)
        encoders.encode_base64(ics_part)
        ics_part.add_header('Content-Disposition', 'attachment', filename='Kong_Health_Clinic_Appointment.ics')
        ics_part.add_header('Content-Type', 'text/calendar; charset=UTF-8; method=REQUEST')
        root_message.attach(ics_part)
        
        logger.info("Calendar attachment created successfully")
    except Exception as e:
        logger.error(f"Failed to create calendar attachment: {e}")
        # Continue without calendar attachment if there's an error
    # --- End ICS Calendar Attachment ---
    
    # Send the email
    try:
        server = smtplib.SMTP(SMTP_SERVER, 587)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(root_message)
        server.quit()
        logger.info(f"Email sent successfully to {call_data_store['global']['email']}")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return SimpleResult(success=False, message=f"Email failed to send: {e}")
    
    return SimpleResult(success=True, message="Email sent successfully")

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
      <p>If you see this page, the web server is running.</p>
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
    global http_session 

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

        # --- Select and Initialize TTS ---
        if USE_LOCAL_TTS:
            logger.info(f"Using local Piper TTS service at {PIPER_TTS_URL}")
            if not http_session:
                 # This should ideally not happen if lifespan manager works correctly
                 logger.error("aiohttp session not initialized!")
                 await websocket.close(code=1011, reason="Internal server error: TTS session not ready.")
                 return
            tts = PiperTTSService(
                base_url=PIPER_TTS_URL,
                aiohttp_session=http_session,
                sample_rate=22050
                # Piper default sample rate might be higher, 
                # but Twilio expects 8000. Pipecat handles resampling.
                # Set sample_rate here if your Piper model differs significantly 
                # or if you encounter issues. Default is often 22050.
                # sample_rate=8000 
            )
        else:
            logger.info("Using Cartesia TTS service")
            if not CARTESIA_API_KEY or not CARTESIA_VOICE_ID:
                logger.error("Cartesia API Key or Voice ID not configured.")
                await websocket.close(code=1011, reason="Internal server error: TTS not configured.")
                return
            tts = CartesiaTTSService(
                api_key=CARTESIA_API_KEY,
                voice_id=CARTESIA_VOICE_ID,
                model_id="sonic-english",
                output_format="pcm_s16le", 
                sample_rate=8000 
            )
        # --- End TTS Selection ---


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
