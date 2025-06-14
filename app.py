from flask import Flask, request, jsonify
from crewai import Agent, Task, Crew, Tool
from google.oauth2 import service_account
from googleapiclient.discovery import build
from twilio.rest import Client
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
import json

# Load env vars
load_dotenv()

# Custom Google Calendar Toolkit Implementation
class CalendarToolkit:
    def __init__(self, calendar_id):
        credential_content = os.getenv('GOOGLE_CREDENTIALS')
        if not credential_content:
            raise ValueError("Google credentials not found")

        self.creds = service_account.Credentials.from_service_account_info(
            json.loads(credential_content)
        self.service = build('calendar', 'v3', credentials=self.creds)
        self.calendar_id = calendar_id

    def create_booking(self, event_summary, start_time, end_time):
        """Actual method to create calendar events"""
        event = {
            'summary': event_summary,
            'start': {'dateTime': start_time},
            'end': {'dateTime': end_time},
        }
        created_event = self.service.events().insert(
            calendarId=self.calendar_id,
            body=event
        ).execute()
        return f"Created event: {created_event.get('htmlLink')}"

    def get_tools(self):
        """Return properly formatted CrewAI tools"""
        return [
            Tool(
                name="google_calendar_creator",
                func=self.create_booking,
                description="Creates events in Google Calendar. Input should be event_summary, start_time (ISO format), end_time (ISO format)"
            )
        ]

# Initialize clients
twilio_client = Client(os.getenv('TWILIO_ACCOUNT_SID'), os.getenv('TWILIO_AUTH_TOKEN'))
toolkit = CalendarToolkit(
    calendar_id=os.getenv("GOOGLE_CALENDAR_ID")
)

# CrewAI Agent Setup
booking_agent = Agent(
    role="WhatsApp Booking Assistant",
    goal="Handle appointment bookings via WhatsApp",
    backstory="Specialized in calendar management",
    tools=toolkit.get_tools(),  # Now returns proper Tool objects
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
        incoming_msg = request.values.get('Body', '').strip()
        sender = format_whatsapp_number(request.values.get('From', ''))

        if not incoming_msg:
            return jsonify({"error": "Empty message"}), 400

        # Create CrewAI task
        task = Task(
            description=f"Process test booking request: '{incoming_msg}'",
            expected_output="Confirmation of test booking creation",
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