# One Dockerfile for every Python service. Build from the repository root:
#
#   docker build -f services/Dockerfile --build-arg SERVICE=ledger -t ledger .
#
# The same image runs the API (default command), the database migrations
# (`alembic upgrade head`) and the outbox relay (`python -m ubcommon.outbox --schema <name>`).

FROM python:3.12-slim

ARG SERVICE
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SERVICE_NAME=${SERVICE}

WORKDIR /srv

# Shared library first: this layer is identical for every service, so it is built once and cached.
COPY libs/common /libs/common
RUN pip install --no-cache-dir "/libs/common[kafka,gcp]"

COPY services/${SERVICE}/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/${SERVICE}/ .

# Run as non-root. /data holds DynaStore's files and locally written reports (a volume when it must persist).
RUN useradd --system --uid 10001 app && mkdir -p /data/objects && chown -R app /data
USER 10001

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
