from flask import Flask, request, jsonify
from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import BaseTool
from crewai.agents.agent_builder.base_agent import BaseAgent
from google.oauth2 import service_account
from googleapiclient.discovery import build
from twilio.rest import Client
from dotenv import load_dotenv
import os
import json
from typing import Dict, Any, List

# Load environment variables
load_dotenv()


@CrewBase
class WhatsAppBookingCrew():
    """WhatsApp Booking Crew"""

    agents: List[BaseAgent]
    tasks: List[Task]

    def __init__(self):
        self.twilio_client = Client(
            os.getenv('TWILIO_ACCOUNT_SID'),
            os.getenv('TWILIO_AUTH_TOKEN')
        )

    @agent
    def booking_agent(self) -> Agent:
        return Agent(
            role='WhatsApp Booking Assistant',
            goal='Handle appointment bookings via WhatsApp messages',
            backstory='Specialized in parsing natural language requests and managing calendars',
            verbose=True,
            tools=[self.calendar_tool()]
        )

    def calendar_tool(self) -> BaseTool:
        class GoogleCalendarTool(BaseTool):
            def __init__(self):
                credential_content = os.getenv('GOOGLE_CREDENTIALS')
                if not credential_content:
                    raise ValueError("Google credentials not found")

                creds = service_account.Credentials.from_service_account_info(
                    json.loads(credential_content))
                self.service = build('calendar', 'v3', credentials=creds)
                self.calendar_id = os.getenv("GOOGLE_CALENDAR_ID")

            def _run(self, event_details: Dict[str, Any]) -> str:
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

        return GoogleCalendarTool()

    @task
    def booking_task(self) -> Task:
        return Task(
            description='Process WhatsApp booking request',
            expected_output='Confirmation message with event details',
            agent=self.booking_agent()
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True
        )


app = Flask(__name__)
booking_crew = WhatsAppBookingCrew().crew()


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

        result = booking_crew.kickoff(inputs={'message': incoming_msg})

        booking_crew.twilio_client.messages.create(
            body=result,
            from_=os.getenv('TWILIO_WHATSAPP_NUMBER'),
            to=sender
        )

        return jsonify({"success": True})

    except Exception as e:
        error_msg = "⚠️ Sorry, something went wrong. Please try again later."
        booking_crew.twilio_client.messages.create(
            body=error_msg,
            from_=os.getenv('TWILIO_WHATSAPP_NUMBER'),
            to=sender
        )
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)