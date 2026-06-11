FROM python:3.11-slim

LABEL maintainer="ATL Team <training@arista.com>"
LABEL description="Config Service - Feature flags and announcements management"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV PYTHONUNBUFFERED=1

EXPOSE 50011

CMD ["python", "src/configservice.py"]
