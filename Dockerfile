FROM python:3.11-slim@sha256:e031123e3d85762b141ad1cbc56452ba69c6e722ebf2f042cc0dc86c47c0d8b3

# Create a dedicated system user and group for running the application securely
RUN groupadd -g 1000 appgroup && \
    useradd -r -u 1000 -g appgroup -s /sbin/nologin appuser

WORKDIR /app

# Copy dependency definition file first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application package and static files
COPY ./app /app/app
COPY ./static /app/static

# Create persistence data directory and adjust permissions
RUN mkdir -p /app/data && chown -R appuser:appgroup /app

# Switch to the non-root user
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--forwarded-allow-ips", "*"]
