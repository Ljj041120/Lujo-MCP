FROM python:3.11-slim

WORKDIR /app

# Git 是 blame/diff 采集器的运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY examples/ ./examples/
COPY sdk/js/ ./sdk/js/

# 默认数据库目录，运行时可挂载 volume 持久化
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
