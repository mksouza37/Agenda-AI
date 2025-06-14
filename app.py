from flask import Flask, request, jsonify
from crewai import Agent, Crew, Process, Task
from crewai.tools import BaseTool
from google.oauth2 import service_account
from googleapiclient.discovery import build
from twilio.rest import Client
from dotenv import load_dotenv
import os
import json
import re
from typing import Dict, Any, List
from pydantic import Field
from datetime import datetime, timedelta

# Load environment variables
load_dotenv()


class GoogleCalendarTool(BaseTool):
    """Ferramenta para agendamento no Google Calendar"""
    name: str = "Agendador de Reuniões"
    description: str = "Agenda e cancela eventos no Google Calendar"
    calendar_id: str = Field(default_factory=lambda: os.getenv("GOOGLE_CALENDAR_ID"))
    service: Any = Field(default=None, exclude=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._setup_service()

    def _setup_service(self):
        """Configura o serviço do Google Calendar"""
        credential_content = os.getenv('GOOGLE_CREDENTIALS')
        if not credential_content:
            raise ValueError("Credenciais do Google não encontradas")

        creds = service_account.Credentials.from_service_account_info(
            json.loads(credential_content))
        self.service = build('calendar', 'v3', credentials=creds)

    def _parse_time(self, time_str: str) -> datetime:
        """Converte texto para objeto datetime"""
        time_str = time_str.lower()
        if "amanhã" in time_str:
            date = datetime.now() + timedelta(days=1)
        else:
            date = datetime.now()

        if match := re.search(r'(\d{1,2})[h:]?(\d{0,2})', time_str):
            hour, minute = match.groups()
            date = date.replace(hour=int(hour),
                                minute=int(minute) if minute else 0,
                                second=0)
        return date

    def _run(self, context: List[str]) -> str:
        """Processa comandos em português"""
        if not context or not isinstance(context, list):
            return "❌ Mensagem inválida recebida"

        mensagem = context[0].lower()

        if any(word in mensagem for word in ["cancelar", "remover"]):
            return self._cancelar_evento(mensagem)
        return self._agendar_evento(mensagem)

    def _agendar_evento(self, mensagem: str) -> str:
        """Agenda um novo evento"""
        try:
            # Extrai detalhes da mensagem
            tema = re.search(r'tema[: ]?(.+)', mensagem, re.IGNORECASE)
            tema = tema.group(1).strip() if tema else "Reunião"

            hora_match = re.search(r'(\d{1,2})[h:]?(\d{0,2})', mensagem)
            duracao_match = re.search(r'(\d+)\s*hora', mensagem)

            inicio = self._parse_time(mensagem)
            duracao = int(duracao_match.group(1)) if duracao_match else 1
            fim = inicio + timedelta(hours=duracao)

            evento = {
                'summary': f"📅 {tema}",
                'start': {
                    'dateTime': inicio.isoformat(),
                    'timeZone': 'America/Sao_Paulo'
                },
                'end': {
                    'dateTime': fim.isoformat(),
                    'timeZone': 'America/Sao_Paulo'
                }
            }

            evento_criado = self.service.events().insert(
                calendarId=self.calendar_id,
                body=evento
            ).execute()

            return (f"✅ Reunião agendada!\n"
                    f"Assunto: {tema}\n"
                    f"Data: {inicio.strftime('%d/%m às %H:%M')}\n"
                    f"Link: {evento_criado.get('htmlLink')}")

        except Exception as e:
            return f"❌ Erro ao agendar: {str(e)}"

    def _cancelar_evento(self, mensagem: str) -> str:
        """Cancela um evento existente"""
        try:
            inicio = self._parse_time(mensagem)
            fim = inicio + timedelta(hours=1)

            eventos = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=inicio.isoformat(),
                timeMax=fim.isoformat(),
                singleEvents=True
            ).execute()

            if not eventos.get('items'):
                return "⚠️ Nenhum evento encontrado para cancelar"

            evento_id = eventos['items'][0]['id']
            self.service.events().delete(
                calendarId=self.calendar_id,
                eventId=evento_id
            ).execute()

            return "🗑️ Evento cancelado com sucesso!"

        except Exception as e:
            return f"❌ Erro ao cancelar: {str(e)}"


# Initialize services
twilio_client = Client(
    os.getenv('TWILIO_ACCOUNT_SID'),
    os.getenv('TWILIO_AUTH_TOKEN')
)

calendar_tool = GoogleCalendarTool()

booking_agent = Agent(
    role="Assistente de Agendamento",
    goal="Agendar e cancelar reuniões via WhatsApp",
    backstory="Especialista em gerenciamento de calendários",
    tools=[calendar_tool],
    verbose=True
)

app = Flask(__name__)


def format_whatsapp_number(number: str) -> str:
    """Formata número para padrão WhatsApp"""
    return number if number.startswith('whatsapp:+') else f"whatsapp:+{number.lstrip('+')}"


@app.route('/whatsapp-webhook', methods=['POST'])
def whatsapp_webhook():
    try:
        incoming_msg = request.values.get('Body', '').strip()
        sender = format_whatsapp_number(request.values.get('From', ''))

        if not incoming_msg:
            return jsonify({"error": "Mensagem vazia"}), 400

        task = Task(
            description=f"Processar mensagem: {incoming_msg}",
            expected_output="Confirmação de agendamento/cancelamento",
            agent=booking_agent,
            context=[incoming_msg]  # Passa a mensagem como lista
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
            body="⚠️ Ocorreu um erro. Por favor, tente novamente.",
            from_=os.getenv('TWILIO_WHATSAPP_NUMBER'),
            to=sender
        )
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)