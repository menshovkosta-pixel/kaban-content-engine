FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 1000 kaban \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin kaban

COPY --chown=kaban:kaban kaban ./kaban
COPY --chown=kaban:kaban projects ./projects
COPY --chown=kaban:kaban *.py ./
COPY --chown=kaban:kaban VERSION ./VERSION

USER kaban

CMD ["python", "scheduler.py", "list"]
