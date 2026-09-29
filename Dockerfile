# Il webhook delle approvazioni (promo/approval_service.py) su Cloud Run.
# Solo il pacchetto promo e le sue dipendenze minime: niente repository del gioco, niente
# rendering. Deploy: vedi docs/promo-studio.md, "Approvazione immediata".
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements-approvals.txt .
RUN pip install --no-cache-dir -r requirements-approvals.txt

COPY promo/ promo/
RUN useradd --system --no-create-home app
USER app

# Un solo processo basta: pochi pulsanti al giorno, e una decisione alla volta.
CMD exec gunicorn --bind ":${PORT:-8080}" --workers 1 --threads 4 --timeout 60 "promo.approval_service:create_app()"
