# Back end for Hugging Face Spaces (Docker SDK). Built in the cloud; no local Docker needed.
FROM python:3.12-slim

# Spaces run the container as user 1000
RUN useradd -m -u 1000 user
WORKDIR /app

COPY --chown=user pyproject.toml ./
COPY --chown=user src ./src
COPY --chown=user scripts ./scripts
COPY --chown=user data/raw ./data/raw
RUN pip install --no-cache-dir -e ".[api,llm]" \
    && mkdir -p data/processed \
    && chown -R user:user /app

USER user
# Build the org tree and the DuckDB file into the image
RUN python scripts/build_org.py && python scripts/load_duckdb.py

ENV PORT=7860 \
    PROMPT_VERSION=v4 \
    LLM_PROVIDER=openai \
    OPENAI_BASE_URL=https://api.groq.com/openai/v1 \
    LLM_MODEL=openai/gpt-oss-120b
EXPOSE 7860
CMD ["sh", "-c", "uvicorn workforce.api:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
