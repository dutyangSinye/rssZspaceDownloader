FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-docker.txt* ./

RUN pip install --upgrade pip \
    && if [ -f requirements-docker.txt ]; then pip install -r requirements-docker.txt; else pip install -r requirements.txt; fi

COPY . .

RUN mkdir -p /app/data /app/logs

EXPOSE 5000

CMD ["python", "main.py"]
