FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/q1_api
WORKDIR /srv

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir /data /backups \
    && chown app:app /data /backups

COPY q1_api/app ./q1_api/app
COPY data/account_applications.csv ./data/account_applications.csv
COPY q3_deploy/scripts ./scripts

USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--app-dir", "q1_api", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
