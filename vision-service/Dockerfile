# Employee Activity Tracking — REST service image
# CPU-only build (Docker Desktop does not pass through Apple Silicon/CUDA GPUs),
# mirrors the install order in setup.py so the container matches local dev.

FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git curl libgl1 libglib2.0-0 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) PyTorch CPU wheels first — must land before anything that imports torch at build time.
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
 && pip install --no-cache-dir torch==2.11.0 torchvision==0.26.0 \
      --index-url https://download.pytorch.org/whl/cpu

# 2) Main pipeline + service dependencies.
RUN pip install --no-cache-dir \
      "numpy>=2.0" pandas==2.2.2 matplotlib==3.9.0 seaborn==0.13.2 \
      scikit-learn==1.5.0 shapely==2.0.4 tqdm==4.66.4 pyyaml==6.0.1 "networkx>=3.2" \
      "opencv-python-headless>=4.10" "ultralytics>=8.3.40,<8.4" insightface==0.7.3 \
      onnxruntime==1.25.1 lapx==0.5.11 \
      "fastapi>=0.115" "uvicorn[standard]>=0.30" "pydantic>=2.7" "python-multipart>=0.0.9"

# 3) Legacy packages that need --no-build-isolation (same as setup.py step 5).
RUN pip install --no-cache-dir --no-build-isolation filterpy==1.4.5 torchreid==0.2.5

# 4) torchreid runtime deps not declared in its own setup.py (setup.py step 6).
RUN pip install --no-cache-dir gdown yacs h5py tensorboard future imageio Cython

# App code — copied after deps so code edits don't invalidate the dependency layers.
COPY service ./service
COPY yolov8m.pt ./yolov8m.pt

ENV PROJECT_DIR=/app
RUN mkdir -p /app/data /app/outputs /app/gallery /app/models

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8000"]
