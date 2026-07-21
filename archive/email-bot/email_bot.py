#!/usr/bin/env python3
"""
Email Bot for AI Assistant
Receives emails via IMAP and responds using Ollama API
"""

import os
import imaplib
import email
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
import time
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
IMAP_SERVER = os.getenv('IMAP_SERVER', 'imap.gmail.com')
IMAP_PORT = int(os.getenv('IMAP_PORT', '993'))
IMAP_USER = os.getenv('IMAP_USER', '')
IMAP_PASSWORD = os.getenv('IMAP_PASSWORD', '')

SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER', '')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')

OLLAMA_URL = os.getenv('OLLAMA_URL', 'http://host.docker.internal:11434')
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'qwen2.5:7b')

EMAIL_ADDRESS = os.getenv('EMAIL_ADDRESS', IMAP_USER)
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', '60'))  # seconds

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_ollama_response(prompt: str) -> str:
    """Get response from Ollama API"""
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False
            },
            timeout=120
        )
        response.raise_for_status()
        return response.json().get('response', 'Sorry, I could not generate a response.')
    except Exception as e:
        logger.error(f"Error getting Ollama response: {e}")
        return "Sorry, I encountered an error processing your request."


def process_email(msg):
    """Extract email content and sender"""
    subject = msg['Subject']
    from_addr = email.utils.parseaddr(msg['From'])[1]
    
    # Get email body
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                break
    else:
        body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
    
    return subject, from_addr, body


def send_reply(to_addr: str, subject: str, original_body: str):
    """Send email reply using AI response"""
    # Create prompt for AI
    prompt = f"""You are a helpful AI assistant responding to an email. 
    
Original email subject: {subject}
Original email content: {original_body}

Please write a helpful, concise, and professional response to this email. Keep it brief and to the point."""
    
    # Get AI response
    logger.info(f"Generating response for email from {to_addr}")
    ai_response = get_ollama_response(prompt)
    
    # Create reply email
    reply = MIMEMultipart()
    reply['From'] = EMAIL_ADDRESS
    reply['To'] = to_addr
    reply['Subject'] = f"Re: {subject}"
    
    # Add AI response
    reply.attach(MIMEText(ai_response, 'plain'))
    
    # Send email
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(reply)
        logger.info(f"Reply sent to {to_addr}")
        return True
    except Exception as e:
        logger.error(f"Error sending reply: {e}")
        return False


def check_emails():
    """Check for new emails and process them"""
    try:
        # Connect to IMAP server
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(IMAP_USER, IMAP_PASSWORD)
        mail.select('INBOX')
        
        # Search for unread emails
        status, messages = mail.search(None, 'UNSEEN')
        
        if status != 'OK':
            logger.error("Failed to search emails")
            return
        
        email_ids = messages[0].split()
        
        if not email_ids:
            logger.debug("No new emails")
            return
        
        logger.info(f"Found {len(email_ids)} new email(s)")
        
        # Process each email
        for email_id in email_ids:
            try:
                # Fetch email
                status, msg_data = mail.fetch(email_id, '(RFC822)')
                if status != 'OK':
                    continue
                
                # Parse email
                msg = email.message_from_bytes(msg_data[0][1])
                subject, from_addr, body = process_email(msg)
                
                # Skip if from self
                if from_addr.lower() == EMAIL_ADDRESS.lower():
                    continue
                
                logger.info(f"Processing email from {from_addr}: {subject}")
                
                # Send reply
                send_reply(from_addr, subject, body)
                
                # Mark as read
                mail.store(email_id, '+FLAGS', '\\Seen')
                
            except Exception as e:
                logger.error(f"Error processing email {email_id}: {e}")
        
        mail.close()
        mail.logout()
        
    except Exception as e:
        logger.error(f"Error checking emails: {e}")


def main():
    """Main loop"""
    logger.info("Email bot starting...")
    logger.info(f"Ollama URL: {OLLAMA_URL}")
    logger.info(f"Model: {OLLAMA_MODEL}")
    logger.info(f"Email: {EMAIL_ADDRESS}")
    logger.info(f"Poll interval: {POLL_INTERVAL} seconds")
    
    # Validate configuration
    if not all([IMAP_USER, IMAP_PASSWORD, SMTP_USER, SMTP_PASSWORD]):
        logger.error("Missing required email configuration. Please set environment variables.")
        return
    
    # Main loop
    while True:
        try:
            check_emails()
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()

