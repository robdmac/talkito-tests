# Dockerfile for running talkito e2e tests
FROM python:3.12-slim

# Install system dependencies including Node.js for Claude CLI
RUN apt-get update && apt-get install -y \
    git \
    ffmpeg \
    portaudio19-dev \
    build-essential \
    curl \
    wget \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20 (required for Claude CLI)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install Claude CLI via npm
RUN npm install -g @anthropic-ai/claude-code

# Set environment variables early so they're available during build
ENV PATH="/root/.local/bin:${PATH}"

# Set working directory
WORKDIR /app

# Copy the entire talkito project
COPY .. /app/

# Upgrade pip to latest version to avoid potential issues
RUN pip install --no-cache-dir --upgrade pip

# Install talkito in development mode (all features are now included)
RUN pip install --no-cache-dir -e .

# Install test dependencies
RUN pip install --no-cache-dir pytest pytest-asyncio

# Install faster-whisper for local ASR testing
RUN pip install --no-cache-dir faster-whisper

RUN pip install --no-cache-dir pexpect

# Set environment variables for testing
ENV PYTHONUNBUFFERED=1
ENV TALKITO_AUTO_APPROVE_DOWNLOADS=1
ENV WHISPER_COMPUTE_TYPE=int8

# Default command runs the e2e test
CMD ["python", "tests-repo/test_asr_e2e.py"]
