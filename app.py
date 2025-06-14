from flask import Flask, request, jsonify
from crewai import Agent, Task, Crew
from crewai_tools import BaseTool
from google.oauth2 import service_account
from googleapiclient.discovery import build
from twilio.rest import Client
from dotenv import load_dotenv
import os
import json
from typing import Dict, Any

# Load environment variables
load_dotenv()


class GoogleCalendarTool(BaseTool):
    """Custom tool for Google Calendar operations"""

    def __init__(self, calendar_id: str):
        super().__init__()
        self.calendar_id = calendar_id
        self._setup_calendar_service()

    def _setup_calendar_service(self):
        """Initialize Google Calendar service"""
        credential_content = os.getenv('GOOGLE_CREDENTIALS')
        if not credential_content:
            raise ValueError("Google credentials not found in environment variables")

        self.creds = service_account.Credentials.from_service_account_info(
            json.loads(credential_content))
        self.service = build('calendar', 'v3', credentials=self.creds)

    def _run(self, event_details: Dict[str, Any]) -> str:
        """Create calendar event (Core tool functionality)"""
        event = {
            'summary': event_details.get('summary'),
            'start': {'dateTime': event_details.get('start_time')},
            'end': {'dateTime': event_details.get('end_time')},
            'description': event_details.get('description', '')
        }

        created_event = self.service.events().insert(
            calendarId=self.calendar_id,
            body=event
        ).execute()

        return f"Event created: {created_event.get('htmlLink')}"


def initialize_services() -> tuple:
    """Initialize all required services"""
    twilio_client = Client(
        os.getenv('TWILIO_ACCOUNT_SID'),
        os.getenv('TWILIO_AUTH_TOKEN')
    )

    calendar_tool = GoogleCalendarTool(
        calendar_id=os.getenv("GOOGLE_CALENDAR_ID")
    )

    booking_agent = Agent(
        role="WhatsApp Booking Assistant",
        goal="Handle appointment bookings via WhatsApp messages",
        backstory=(
            "Specialized in parsing natural language requests "
            "and managing calendars with precision"
        ),
        tools=[calendar_tool],
        verbose=True
    )

    return twilio_client, booking_agent


def format_whatsapp_number(number: str) -> str:
    """Standardize WhatsApp number format"""
    if number.startswith('whatsapp:+'):
        return number
    return f"whatsapp:+{number.lstrip('whatsapp:').lstrip('+')}"


# Initialize Flask app and services
app = Flask(__name__)
twilio_client, booking_agent = initialize_services()


@app.route('/whatsapp', methods=['POST'])
def whatsapp_webhook():
    """Handle incoming WhatsApp messages"""
    try:
        incoming_msg = request.values.get('Body', '').strip()
        sender = format_whatsapp_number(request.values.get('From', ''))

        if not incoming_msg:
            return jsonify({"error": "Empty message"}), 400

        task = Task(
            description=(
                f"Process booking request: '{incoming_msg}'. "
                "Extract: (1) Event summary, (2) Start datetime, "
                "(3) End datetime, (4) Optional description"
            ),
            expected_output="Confirmation message with event details",
            agent=booking_agent
        )

        crew = Crew(agents=[booking_agent], tasks=[task])
        result = crew.kickoff()

        twilio_client.messages.create(
            body=result,
            from_=os.getenv('TWILIO_WHATSAPP_NUMBER'),
            to=sender
        )

        return jsonify({"success": True})

    except Exception as e:
        error_msg = "⚠️ Sorry, something went wrong. Please try again later."
        twilio_client.messages.create(
            body=error_msg,
            from_=os.getenv('TWILIO_WHATSAPP_NUMBER'),
            to=sender
        )
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)