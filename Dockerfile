FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml schema.sql ./
COPY src ./src
RUN pip install --no-cache-dir -e .

RUN mkdir -p /data
ENV SHARE_DB_PATH=/data/share.db
EXPOSE 8080
CMD ["cassandra-share"]
