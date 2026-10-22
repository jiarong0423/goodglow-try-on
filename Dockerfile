# Good Glow Try-On: online version for judges (fictional catalog, no brand data; the image carries no YouCam key).
# Default is demo mode: no YouCam calls, no credits, no sign-in. To let signed-in judges use real calls, set all of
# GOODGLOW_LIVE=1, YOUCAM_API_KEY, GOODGLOW_JUDGE_USER and GOODGLOW_JUDGE_PASSWORD (at least 10 characters) on the platform.
# If any is missing or too short the container starts in demo mode and logs which one (app/serve.sh).
# Visitors who are not signed in always get demo mode; the sign-in page is /judge. Each start allows at most GOODGLOW_MAX_CALLS
# real calls, and GOODGLOW_BUDGET_CAP limits total credits across starts. The credit ledger lives in /srv/app/runs:
# mount a persistent volume there, or every redeploy resets the ledger.
# Build: docker build -t goodglow .      Run: docker run --rm -p 8787:8787 goodglow
# Coolify and similar platforms set PORT; GOODGLOW_MAX_HEAVY (default 2) caps concurrent heavy requests.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8787 \
    GOODGLOW_MAX_HEAVY=2 \
    GOODGLOW_MAX_CALLS=5 \
    GOODGLOW_BUDGET_CAP=30

WORKDIR /srv/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web
COPY demo ./demo
COPY tools/noise_test.py ./tools/noise_test.py

# Photos are never written to disk; runs/ holds only the credit ledger (unused in demo mode) and stays writable for the non-root user
RUN useradd --system --uid 10001 goodglow && mkdir -p runs && chown -R goodglow:goodglow runs
USER goodglow

EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/status' % os.environ.get('PORT','8787'),timeout=4)" || exit 1

CMD ["sh", "app/serve.sh"]
