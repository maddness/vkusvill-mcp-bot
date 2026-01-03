FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY bot/ ./bot/
COPY assets/ ./assets/
COPY prompts/ ./prompts/
COPY config.yaml .

# Run bot
CMD ["python", "app.py"]


