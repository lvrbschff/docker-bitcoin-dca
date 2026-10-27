FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir requests==2.32.5
COPY activity.py bot.py bitvavo.py exchange.py kraken.py reporting.py web_ui.py .
CMD ["python", "-u", "bot.py"]
