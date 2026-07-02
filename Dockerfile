FROM python:3.11-slim

WORKDIR /app

# Layer caching: install deps before copying source
COPY attribution_agent/attribution_agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the package flat into /app so both entry points resolve the same way
# as local dev (which runs from inside attribution_agent/attribution_agent/)
COPY attribution_agent/attribution_agent/ /app/

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Default command = UI service (Cloud Run Service).
# The pipeline Job overrides this at deploy time:
#   --command python --args flows/agency_flow.py[,--agency,<name>,...]
CMD ["streamlit", "run", "app.py", "--server.port", "8080", "--server.address", "0.0.0.0", "--server.headless", "true"]
