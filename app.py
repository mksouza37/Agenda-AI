from flask import Flask, request, jsonify
from crewai import Agent, Task, Crew
from langchain_google_community import GoogleCalendarToolkit  # Official package
from twilio.rest import Client
from dotenv import load_dotenv
import os

# Load env vars
load_dotenv()

# Initialize clients
twilio_client = Client(os.getenv('TWILIO_ACCOUNT_SID'), os.getenv('TWILIO_AUTH_TOKEN'))
toolkit = GoogleCalendarToolkit(
    credentials_path="credentials.json",
    calendar_id=os.getenv("GOOGLE_CALENDAR_ID")
)

# CrewAI Agent Setup
booking_agent = Agent(
    role="WhatsApp Booking Assistant",
    goal="Handle appointment bookings via WhatsApp messages",
    backstory="Specialized in parsing natural language requests and managing calendars",
    tools=toolkit.get_tools(),  # Auto-includes Google Calendar tools
    verbose=True
)

app = Flask(__name__)


def format_whatsapp_number(number):
    """Convert WhatsApp numbers to E.164 format"""
    return number if number.startswith('whatsapp:+') else f"whatsapp:+{number.lstrip('whatsapp:')}"


@app.route('/whatsapp', methods=['POST'])
def whatsapp_webhook():
    try:
        # Get incoming message
        incoming_msg = request.values.get('Body', '')
        sender = format_whatsapp_number(request.values.get('From', ''))

        if not incoming_msg:
            return jsonify({"error": "Empty message"}), 400

        # Create CrewAI task
        task = Task(
            description=f"Process WhatsApp booking request: '{incoming_msg}'. Extract: (1) intent (book/cancel), (2) service, (3) datetime, (4) name.",
            expected_output="Confirmation message to send back to user",
            agent=booking_agent
        )
        crew = Crew(agents=[booking_agent], tasks=[task])
        result = crew.kickoff()

        # Send response via Twilio
        twilio_client.messages.create(
            body=result,
            from_=os.getenv('TWILIO_WHATSAPP_NUMBER'),
            to=sender
        )
        return jsonify({"success": True})

    except Exception as e:
        # Error fallback
        twilio_client.messages.create(
            body="⚠️ Sorry, something went wrong. Please try again later.",
            from_=os.getenv('TWILIO_WHATSAPP_NUMBER'),
            to=sender
        )
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)