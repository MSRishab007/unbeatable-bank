FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app

# Run as non-root
RUN useradd --system --uid 10001 app
USER 10001

EXPOSE 8080 50051
# The same image runs migrations: `alembic upgrade head` (Kubernetes Job / ArgoCD PreSync hook)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
