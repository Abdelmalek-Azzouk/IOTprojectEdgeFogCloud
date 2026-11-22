FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY cloud ./cloud
COPY fog ./fog
COPY edge ./edge
COPY dashboard ./dashboard
COPY tests ./tests
EXPOSE 8000
# command is set per-service in docker-compose.yml
CMD ["python", "-m", "uvicorn", "cloud.api:app", "--host", "0.0.0.0", "--port", "8000"]
