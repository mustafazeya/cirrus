FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user
RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Run the application with Gunicorn and dynamic worker count
# Formula: (2 * CPU cores) + 1, minimum 4 workers
CMD ["sh", "-c", "exec gunicorn -w $(( $(nproc) > 1 ? 2 * $(nproc) + 1 : 4 )) -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 300 --worker-tmp-dir /dev/shm --access-logfile - --error-logfile - --log-level info main:app"]