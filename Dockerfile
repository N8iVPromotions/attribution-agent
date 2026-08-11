FROM python:3.11-slim

WORKDIR /app

# Layer caching: install deps before copying source
COPY attribution_agent/attribution_agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the package flat into /app so both entry points resolve the same way
# as local dev (which runs from inside attribution_agent/attribution_agent/)
COPY attribution_agent/attribution_agent/ /app/

# Versioned agent prompt definitions (PromptLoader / n8iv_agents read these)
COPY .claude/agents/ /app/.claude/agents/

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    N8IV_AGENTS_DIR=/app/.claude/agents

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Default command = FastAPI control service.
# Cloud Run Jobs override this at deploy time with the specific flow module.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
