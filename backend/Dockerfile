FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY paperdesk ./paperdesk

ENV PAPERDESK_ALLOW_REMOTE=1
ENV PORT=8080
VOLUME ["/data"]
EXPOSE 8080

CMD ["sh", "-c", "python -m paperdesk --host 0.0.0.0 --port ${PORT} --data-dir /data"]
