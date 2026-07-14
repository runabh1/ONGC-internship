FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc g++ libpq-dev zlib1g-dev libjpeg-dev libfreetype6-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY backend /app/backend
COPY ml /app/ml

ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
