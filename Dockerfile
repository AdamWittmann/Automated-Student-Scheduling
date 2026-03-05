FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all app source files
COPY app.py graph_auth.py graph_scheduler.py schedule_log.py scheduling_logic.py ./
COPY templates/ templates/
COPY static/ static/
COPY studentdata/ studentdata/

CMD ["python", "app.py"]