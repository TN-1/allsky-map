FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

# Create a dedicated system user and group for running the application securely
RUN groupadd -g 1000 appgroup && \
    useradd -r -u 1000 -g appgroup -s /sbin/nologin appuser

WORKDIR /app

# Copy dependency definition file first to leverage Docker layer caching
COPY requirements.txt .
RUN apt-get update && \
    apt-get install -y --no-install-recommends libjpeg62-turbo zlib1g && \
    apt-get install -y --no-install-recommends gcc libc6-dev zlib1g-dev libjpeg62-turbo-dev && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get purge -y --auto-remove gcc libc6-dev zlib1g-dev libjpeg62-turbo-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy backend application package and static files
COPY ./app /app/app
COPY ./static /app/static

# Create persistence data directory and adjust permissions
RUN mkdir -p /app/data && chown -R appuser:appgroup /app

# Switch to the non-root user
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--forwarded-allow-ips", "*"]
