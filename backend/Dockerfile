FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

COPY requirements.txt .

# PyTorch is installed from the CPU-only index first: the default Linux wheels
# bundle CUDA libraries and make the image several gigabytes larger.
RUN grep -E '^(torch|torchvision)==' requirements.txt > torch-requirements.txt \
    && pip install --index-url https://download.pytorch.org/whl/cpu -r torch-requirements.txt \
    && pip install -r requirements.txt \
    && rm torch-requirements.txt

COPY app ./app

RUN useradd --create-home --uid 10001 appuser
USER appuser

# The weights are not baked into the image: mount them at MODEL_PATH.
ENV MODEL_PATH=/srv/models/resnet18_v2_best.pt \
    DEVICE=cpu

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
