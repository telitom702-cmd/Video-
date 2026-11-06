FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /thumbnail

# FFmpeg + archive extraction tools
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        p7zip-full \
        fontconfig \
        fonts-noto-core \
        unrar-free \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
