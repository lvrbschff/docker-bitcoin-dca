FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir requests==2.34.2
COPY app/ ./
HEALTHCHECK --interval=300s --timeout=5s --start-period=90s --retries=3 CMD ["python", "healthcheck.py"]
CMD ["python", "-u", "bot.py"]

