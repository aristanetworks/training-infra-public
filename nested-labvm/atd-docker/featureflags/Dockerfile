FROM python:3.11-slim

LABEL maintainer="ATD Team <atd-help@arista.com>"
LABEL description="Feature Flags Service - Firestore-backed feature flag management"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV PYTHONUNBUFFERED=1

EXPOSE 50011

CMD ["python", "src/featureflags_service.py"]
