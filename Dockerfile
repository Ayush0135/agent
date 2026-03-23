# ===== Base Image: Python 3.10 slim for minimal size =====
FROM python:3.10-slim

# System-level dependencies for torch, transformers, and scraping
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first to exploit Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# Pre-download HuggingFace models at build time so cold starts are instant
# This bakes the models into the Docker image (~600MB total)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
RUN python -c "from transformers import pipeline; pipeline('text-classification', model='cross-encoder/nli-MiniLM2-L6-H768')"

# Copy source code
COPY . .

# Expose FastAPI port
EXPOSE 8000

# Start the FastAPI server with uvicorn
# We use a shell-form CMD to allow environment variable expansion (like $PORT)
# Reduced workers to 1 to stay within Render's memory limits
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
