FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies first (Docker layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py commands.py security.py ./

# HuggingFace Spaces runs on port 7860
ENV PORT=7860

# Create data directory for telemetry + opt-out files
RUN mkdir -p /app/data

EXPOSE 7860

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
