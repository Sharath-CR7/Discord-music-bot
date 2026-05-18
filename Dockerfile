FROM python:3.11

RUN apt update && apt install -y ffmpeg libopus0 libopus-dev

WORKDIR /app

COPY . .

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

CMD ["python", "bott.py"]
