FROM python:3.12-slim-bookworm
WORKDIR /app
COPY pyproject.toml constraints.txt ./
COPY src ./src
RUN pip install --no-cache-dir -c constraints.txt . && useradd --uid 10001 --create-home app && mkdir /data && chown app /data
USER app
ENV DATA_DIR=/data EVAL_DATA_DIR=/data/runs
EXPOSE 8101
HEALTHCHECK --interval=20s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8101/health')"
CMD ["uvicorn", "trade_desk.api:app", "--host", "0.0.0.0", "--port", "8101"]
