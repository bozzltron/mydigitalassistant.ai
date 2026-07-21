FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir \
    imaplib2 \
    aiosmtplib \
    email-validator \
    requests \
    python-dotenv

# Copy bot script
COPY email_bot.py .

# Run the bot
CMD ["python", "email_bot.py"]

