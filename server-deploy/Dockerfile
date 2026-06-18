FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements-server.txt ./requirements-server.txt
RUN pip install --no-cache-dir -r requirements-server.txt

COPY server ./server
COPY README-server.txt ./README-server.txt

EXPOSE 8000

CMD ["sh", "-c", "uvicorn server.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
