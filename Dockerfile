FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=data/app.db \
    PORT=8080

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY main.py ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh && mkdir -p data

EXPOSE 8080

# The entrypoint chowns the bind-mounted data directory and then drops to APP_UID:APP_GID.
# It must start as root to do that, so do NOT add a USER directive or a compose `user:`.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "main.py"]
