FROM python:3.11-slim

WORKDIR /app

# Install dependencies (original + server extras)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    flask==3.1.* waitress==3.0.* apscheduler==3.10.* flask-compress==1.17

# Copy application code
COPY scripts/ scripts/
COPY assets/ assets/
COPY feeds/ feeds/
COPY index.html server.py entrypoint.sh ./

RUN chmod +x entrypoint.sh

# Seed data (copied into persistent volume on first run)
COPY data/archive.json data/title-zh-cache.json data-seed/

RUN mkdir -p /app/data

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "waitress", "--host=0.0.0.0", "--port=8080", "--threads=4", "server:app"]
