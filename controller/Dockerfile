FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY docksentinel ./docksentinel
RUN pip install --no-cache-dir . && useradd -u 10001 sentinel
USER sentinel
WORKDIR /data
ENTRYPOINT ["docksentinel", "--db", "/data/docksentinel.db"]
CMD ["watch", "--in-cluster"]
