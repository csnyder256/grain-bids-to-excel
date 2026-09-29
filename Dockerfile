FROM python:3.12-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/csnyder256/grain-bids-to-excel"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /opt/bidboard
COPY app/requirements.txt app/requirements.txt
RUN pip install --no-cache-dir -r app/requirements.txt && python -m playwright install --with-deps chromium
COPY app/src app/src
COPY deploy/serve.py deploy/serve.py
RUN useradd --create-home --uid 10001 bidboard && mkdir -p app/data Spreadsheets && chown -R bidboard:bidboard app/data Spreadsheets
USER bidboard
EXPOSE 8383
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8383/', timeout=3)"
CMD ["python", "deploy/serve.py"]
