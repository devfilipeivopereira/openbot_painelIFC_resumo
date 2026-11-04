FROM python:3.12-alpine
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 IFC_PANEL_HOST=0.0.0.0 IFC_PANEL_PORT=8787 IFC_PANEL_JSON=/data/dados.json IFC_PANEL_AUTH_FILE=/run/secrets/ifc-panel-auth
WORKDIR /app
COPY --chown=0:0 index.html painel.css painel.js server.py ./
RUN chmod 0555 /app && chmod 0444 /app/index.html /app/painel.css /app/painel.js /app/server.py
USER 65534:65534
EXPOSE 8787
ENTRYPOINT ["python3", "/app/server.py"]
