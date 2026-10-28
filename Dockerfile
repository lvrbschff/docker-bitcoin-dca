FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir requests==2.34.2
COPY activity.py analytics.py bot.py bitvavo.py exchange.py exchange_errors.py read_cache.py kraken.py reporting.py web_ui.py withdrawals.py worker.py healthcheck.py price_cache.py .
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 CMD ["python", "healthcheck.py"]
CMD ["python", "-u", "bot.py"]

