FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY bot ./bot
COPY dashboard ./dashboard

# State, ledger and the STOP file live in /data, a volume, so they survive rebuilds.
# The fixed uid lets the host prepare a bind-mounted ./data directory for it.
RUN useradd --create-home --uid 10001 bot && mkdir /data && chown bot:bot /data
USER bot
VOLUME /data

# "python" as the entrypoint lets the same image run the bot or the read-only check:
#   docker compose run --rm bot -m bot.check --config /app/config.json
ENTRYPOINT ["python"]
CMD ["-m", "bot", "--config", "/app/config.json", "--workdir", "/data"]
