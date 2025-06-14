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
import logging
from typing import Dict, Any, List
from pydantic import Field
from datetime import datetime, timedelta

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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

        try:
            creds = service_account.Credentials.from_service_account_info(
                json.loads(credential_content))
            self.service = build('calendar', 'v3', credentials=creds)
            logger.info("Google Calendar service initialized successfully")
        except Exception as e:
            logger.error(f"Error setting up Google Calendar service: {str(e)}")
            raise

    def _parse_time(self, time_str: str) -> datetime:
        """Converte texto para objeto datetime"""
        try:
            time_str = time_str.lower()
            if "amanhã" in time_str:
                date = datetime.now() + timedelta(days=1)
            else:
                date = datetime.now()

            if match := re.search(r'(\d{1,2})[h:]?(\d{0,2})', time_str):
                hour, minute = match.groups()
                date = date.replace(
                    hour=int(hour),
                    minute=int(minute) if minute else 0,
                    second=0
                )
            return date
        except Exception as e:
            logger.error(f"Error parsing time: {str(e)}")
            raise

    def _run(self, context: List[str]) -> str:
        """Processa comandos em português"""
        try:
            if not context or not isinstance(context, list):
                return "❌ Mensagem inválida recebida"

            mensagem = context[0].lower()

            if any(word in mensagem for word in ["cancelar", "remover"]):
                return self._cancelar_evento(mensagem)
            return self._agendar_evento(mensagem)
        except Exception as e:
            logger.error(f"Error in _run: {str(e)}")
            return f"❌ Erro ao processar comando: {str(e)}"

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

            html_link = evento_criado.get('htmlLink', 'link não disponível')

            return (f"✅ Reunião agendada!\n"
                    f"Assunto: {tema}\n"
                    f"Data: {inicio.strftime('%d/%m às %H:%M')}\n"
                    f"Link: {html_link}")

        except Exception as e:
            logger.error(f"Error in _agendar_evento: {str(e)}")
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

            eventos = eventos if isinstance(eventos, dict) else {}

            if not eventos.get('items'):
                return "⚠️ Nenhum evento encontrado para cancelar"

            evento_id = eventos['items'][0]['id']
            self.service.events().delete(
                calendarId=self.calendar_id,
                eventId=evento_id
            ).execute()

            return "🗑️ Evento cancelado com sucesso!"

        except Exception as e:
            logger.error(f"Error in _cancelar_evento: {str(e)}")
            return f"❌ Erro ao cancelar: {str(e)}"


# Initialize services
try:
    twilio_client = Client(
        os.getenv('TWILIO_ACCOUNT_SID'),
        os.getenv('TWILIO_AUTH_TOKEN')
    )
    logger.info("Twilio client initialized successfully")
except Exception as e:
    logger.error(f"Error initializing Twilio client: {str(e)}")
    raise

try:
    calendar_tool = GoogleCalendarTool()
    logger.info("Google Calendar tool initialized successfully")
except Exception as e:
    logger.error(f"Error initializing Google Calendar tool: {str(e)}")
    raise

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
    try:
        if not number:
            return ""
        return number if number.startswith('whatsapp:+') else f"whatsapp:+{number.lstrip('+')}"
    except Exception as e:
        logger.error(f"Error formatting WhatsApp number: {str(e)}")
        return ""


@app.route('/whatsapp-webhook', methods=['POST'])
def whatsapp_webhook():
    sender = ""
    try:
        logger.info(f"Incoming request: {request.method} {request.url}")

        # Get incoming data
        if request.is_json:
            data = request.get_json()
            incoming_msg = data.get('Body', '').strip()
            sender = data.get('From', '')
        else:
            incoming_msg = request.form.get('Body', '').strip()
            sender = request.form.get('From', '')

        logger.info(f"Processing message from {sender}: {incoming_msg}")

        if not incoming_msg:
            logger.warning("Empty message received")
            return jsonify({"error": "Mensagem vazia"}), 400

        sender = format_whatsapp_number(sender)
        if not sender:
            logger.error("Invalid sender format")
            return jsonify({"error": "Número do remetente inválido"}), 400

        # Process message
        task = Task(
            description=f"Processar mensagem: {incoming_msg}",
            expected_output="Confirmação de agendamento/cancelamento",
            agent=booking_agent,
            context=[incoming_msg]
        )

        crew = Crew(
            agents=[booking_agent],
            tasks=[task],
            process=Process.sequential,
            verbose=2  # Maximum verbosity
        )

        result = crew.kickoff()
        logger.info(f"Raw crew result (type: {type(result)}): {result}")

        # SAFE RESULT HANDLING - handles all possible return types
        if hasattr(result, 'output'):  # If it's an object with output attribute
            response_text = str(result.output)
        elif isinstance(result, dict):  # If it's a dictionary
            response_text = str(result.get('output', result))
        else:  # For any other type (string, etc)
            response_text = str(result)

        # Clean up response text
        response_text = response_text.strip() or "Operação concluída"
        logger.info(f"Final response text: {response_text}")

        # Send response
        twilio_client.messages.create(
            body=response_text,
            from_=os.getenv('TWILIO_WHATSAPP_NUMBER'),
            to=sender
        )

        return jsonify({"success": True})

    except Exception as e:
        error_msg = f"⚠️ Ocorreu um erro: {str(e)}"
        logger.error(f"Webhook error: {error_msg}", exc_info=True)

        if sender:
            try:
                twilio_client.messages.create(
                    body="Desculpe, ocorreu um erro. Por favor, tente novamente mais tarde.",
                    from_=os.getenv('TWILIO_WHATSAPP_NUMBER'),
                    to=sender
                )
            except Exception as twilio_error:
                logger.error(f"Failed to send error message: {str(twilio_error)}")

        return jsonify({"error": error_msg}), 500


if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    logger.info(f"Starting server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=os.getenv('DEBUG', 'False').lower() == 'true')