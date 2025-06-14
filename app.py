from flask import Flask, request, jsonify
from crewai import Agent, Task, Crew
from google.oauth2 import service_account
from googleapiclient.discovery import build
from twilio.rest import Client
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta

# Load env vars
load_dotenv()


# Custom Google Calendar Toolkit Implementation
class CalendarToolkit:
    def __init__(self, credentials_path, calendar_id):
        self.creds = service_account.Credentials.from_service_account_file(credentials_path)
        self.service = build('calendar', 'v3', credentials=self.creds)
        self.calendar_id = calendar_id

    def get_tools(self):
        """Return a test booking tool"""
        return [self.create_test_booking]

    def create_test_booking(self, event_summary="Test Booking", minutes_from_now=30, duration_minutes=60):
        """Test tool to create a calendar event (simplified for testing)"""
        start_time = datetime.utcnow() + timedelta(minutes=minutes_from_now)
        end_time = start_time + timedelta(minutes=duration_minutes)

        event = {
            'summary': event_summary,
            'start': {'dateTime': start_time.isoformat() + 'Z'},
            'end': {'dateTime': end_time.isoformat() + 'Z'},
        }

        try:
            created_event = self.service.events().insert(
                calendarId=self.calendar_id,
                body=event
            ).execute()
            return f"Booking created: {created_event.get('htmlLink')}"
        except Exception as e:
            return f"Error creating booking: {str(e)}"


# Initialize clients
twilio_client = Client(os.getenv('TWILIO_ACCOUNT_SID'), os.getenv('TWILIO_AUTH_TOKEN'))
toolkit = CalendarToolkit(
    credentials_path="credentials.json",
    calendar_id=os.getenv("GOOGLE_CALENDAR_ID")
)

# CrewAI Agent Setup
booking_agent = Agent(
    role="WhatsApp Booking Assistant",
    goal="Handle appointment bookings via WhatsApp messages",
    backstory="Specialized in parsing natural language requests and managing calendars",
    tools=toolkit.get_tools(),  # Includes our test booking tool
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