FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir 'psycopg[binary]>=3.2,<4'
COPY --chown=0:0 server.py /app/server.py
RUN chmod 0555 /app && chmod 0444 /app/server.py
USER 65534:65534
EXPOSE 8788
ENTRYPOINT ["python3", "/app/server.py"]
