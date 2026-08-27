FROM python:3.11

RUN apt update && apt install -y ffmpeg libopus0 libopus-dev curl unzip \
    && curl -fsSL https://deno.land/install.sh | sh \
    && ln -s /root/.deno/bin/deno /usr/local/bin/deno

WORKDIR /app

COPY . .

RUN pip install --upgrade pip
RUN pip install --upgrade "yt-dlp[default]" \
    && pip install -r requirements.txt

CMD ["python", "bott.py"]
