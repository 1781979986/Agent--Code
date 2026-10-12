FROM python:3.12-slim-bookworm
WORKDIR /opt/runner
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY entry.py query_worker.py common.py ./
USER 1000:1000
ENTRYPOINT ["python", "/opt/runner/entry.py"]
