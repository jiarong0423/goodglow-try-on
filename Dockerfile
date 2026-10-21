# 好氣色試妝：評審用線上版（展示模式、虛構型錄；不帶任何 YouCam 金鑰、不扣點、不含品牌資料）
# 建置：docker build -t goodglow .      執行：docker run --rm -p 8787:8787 goodglow
# Coolify 等平台會設 PORT；同時處理的重運算請求上限用 GOODGLOW_MAX_HEAVY（預設 2）。
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8787 \
    GOODGLOW_MAX_HEAVY=2

WORKDIR /srv/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web
COPY demo ./demo
COPY tools/noise_test.py ./tools/noise_test.py

# 展示模式不寫入使用者照片；runs/ 只放點數帳本（展示模式不會用到），仍給非 root 使用者可寫
RUN useradd --system --uid 10001 goodglow && mkdir -p runs && chown -R goodglow:goodglow runs
USER goodglow

EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/status' % os.environ.get('PORT','8787'),timeout=4)" || exit 1

CMD ["sh", "-c", "exec python -B app/server.py --mock --catalog demo --host 0.0.0.0 --port ${PORT}"]
