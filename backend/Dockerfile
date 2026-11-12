FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
# Dev requirements (pytest) included so reviewers can run `docker compose run --rm backend pytest`.
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY pytest.ini .
COPY app ./app
COPY tests ./tests
# config.yml and configs/ are bind-mounted by docker-compose, so switching condition needs no rebuild.
# Single worker on purpose: live session state is held in memory in one event loop.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
