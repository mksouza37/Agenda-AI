from flask import Flask, request, jsonify
from crewai import Agent, Crew, Process, Task
from crewai.tools import BaseTool
from google.oauth2 import service_account
from googleapiclient.discovery import build
from twilio.rest import Client
from dotenv import load_dotenv
import os
import json
from typing import Dict, Any
from pydantic import Field

# Load environment variables
load_dotenv()


from pydantic import Field

class GoogleCalendarTool(BaseTool):
    """Custom tool for Google Calendar operations"""
    name: str = "Google Calendar Tool"  # Required by BaseTool
    description: str = "Creates events in Google Calendar"  # Required by BaseTool
    calendar_id: str = Field(default_factory=lambda: os.getenv("GOOGLE_CALENDAR_ID"))
    service: Any = Field(default=None, exclude=True)  # Mark as non-serializable

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._setup_service()

    def _setup_service(self):
        """Initialize Google Calendar service"""
        credential_content = os.getenv('GOOGLE_CREDENTIALS')
        if not credential_content:
            raise ValueError("Google credentials not found")

        creds = service_account.Credentials.from_service_account_info(
            json.loads(credential_content))
        self.service = build('calendar', 'v3', credentials=creds)

    def _run(self, event_details: Dict[str, Any]) -> str:
        """Create calendar event (Core tool functionality)"""
        event = {
            'summary': event_details.get('summary'),
            'start': {'dateTime': event_details.get('start_time')},
            'end': {'dateTime': event_details.get('end_time')}
        }
        result = self.service.events().insert(
            calendarId=self.calendar_id,
            body=event
        ).execute()
        return f"Event created: {result.get('htmlLink')}"


# Initialize services
twilio_client = Client(
    os.getenv('TWILIO_ACCOUNT_SID'),
    os.getenv('TWILIO_AUTH_TOKEN')
)

calendar_tool = GoogleCalendarTool()

booking_agent = Agent(
    role="WhatsApp Booking Assistant",
    goal="Handle appointment bookings via WhatsApp",
    backstory="Specialized in calendar management",
    tools=[calendar_tool],
    verbose=True
)

app = Flask(__name__)


def format_whatsapp_number(number: str) -> str:
    """Standardize WhatsApp number format"""
    return number if number.startswith('whatsapp:+') else f"whatsapp:+{number.lstrip('+')}"


@app.route('/whatsapp', methods=['POST'])
def whatsapp_webhook():
    try:
        incoming_msg = request.values.get('Body', '').strip()
        sender = format_whatsapp_number(request.values.get('From', ''))

        if not incoming_msg:
            return jsonify({"error": "Empty message"}), 400

        task = Task(
            description=f"Process booking: {incoming_msg}",
            expected_output="Event confirmation",
            agent=booking_agent
        )

        crew = Crew(
            agents=[booking_agent],
            tasks=[task],
            process=Process.sequential,
            verbose=True
        )

        result = crew.kickoff()

        twilio_client.messages.create(
            body=result,
            from_=os.getenv('TWILIO_WHATSAPP_NUMBER'),
            to=sender
        )
        return jsonify({"success": True})

    except Exception as e:
        twilio_client.messages.create(
            body="⚠️ Error: Please try again",
            from_=os.getenv('TWILIO_WHATSAPP_NUMBER'),
            to=sender
        )
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)