import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from loguru import logger

# Add the parent directory to sys.path to allow importing voice_ai_agent
sys.path.append(str(Path(__file__).parent))

# Load environment variables from .env file
load_dotenv(override=True)

# Import necessary components from the main script AFTER loading .env
# Ensure environment variables are available before these imports might use them
try:
    from voice_ai_agent import send_email, call_data_store, SimpleResult
    # Import necessary env vars directly for clarity in this test script
    SENDER_EMAIL = os.getenv("SENDER_EMAIL")
    TEST_RECIPIENT_EMAIL = os.getenv("TEST_RECIPIENT_EMAIL")
    SMTP_SERVER = os.getenv("SMTP_SERVER")
    SMTP_USERNAME = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
except ImportError as e:
    logger.error(f"Failed to import from voice_ai_agent: {e}")
    logger.error("Ensure voice_ai_agent.py is in the same directory or sys.path is correct.")
    sys.exit(1)
except KeyError as e:
     logger.error(f"Missing environment variable: {e}")
     sys.exit(1)


async def main():
    """Simulates call data and runs the send_email function."""
    logger.info("Starting email test script...")

    # --- Simulate Call Data ---
    # Replace with the email you want to send the test to
    logger.info(f"Simulating data for recipient: {TEST_RECIPIENT_EMAIL}")

    call_data_store["global"] = {
        'name': "Test Patient",
        'birthday': "1990-01-15",
        'insurance_provider': "Simulated Health",
        'insurance_payer_id': "SIMPAYER999",
        'has_referral': True,
        'referring_physician': "Dr. Sim Referral",
        'visit_reason': "Simulated Checkup",
        'address': "123 Simulation St, Faketown, FS 54321 (Validated)",
        'phone': "555-987-6543",
        'email': TEST_RECIPIENT_EMAIL,
        'scheduled_provider': "Dr. Test Provider",
        'scheduled_time': "2025-06-15 10:00" 
    }
    logger.debug(f"Simulated call_data_store: {call_data_store['global']}")
    # --- End Simulation ---

    # Check if email config is present before attempting send
    if not all([SENDER_EMAIL, SMTP_SERVER, SMTP_USERNAME, SMTP_PASSWORD]):
        logger.error("Email configuration missing in .env file. Cannot send test email.")
        return

    logger.info("Calling send_email function...")
    try:
        # send_email uses the global call_data_store, args are not strictly needed here
        result = await send_email(args=None)  # Pass None for args
        
        # Handle the result properly based on its type
        if hasattr(result, 'success') and hasattr(result, 'message'):
            # It's a SimpleResult object
            logger.info(f"send_email completed. Success: {result.success}, Message: {result.message}")
        elif isinstance(result, dict):
            # It's a dictionary
            success = result.get('success', False)
            message = result.get('message', 'No message provided')
            logger.info(f"send_email completed. Success: {success}, Message: {message}")
        else:
            # Handle any other return type
            logger.info(f"send_email completed with result: {result}")
    except Exception as e:
        logger.exception(f"An error occurred during send_email execution: {e}")
    finally:
        # Clean up the global store if it wasn't cleared by send_email
        if "global" in call_data_store:
            logger.info("Clearing simulated data from call_data_store.")
            call_data_store.clear()

    logger.info("Email test script finished.")

if __name__ == "__main__":
    logger.add("test_send_email.log", rotation="1 MB", level="DEBUG")

    asyncio.run(main())
