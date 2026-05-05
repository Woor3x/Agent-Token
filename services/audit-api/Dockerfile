FROM python:3.11-slim

WORKDIR /app

# gcc: optional native extensions; curl: healthcheck
RUN sed -i 's|http://deb.debian.org|http://mirrors.aliyun.com|g; s|http://security.debian.org|http://mirrors.aliyun.com|g' \
        /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
        /etc/apt/sources.list 2>/dev/null; true \
 && apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY . .

# Pre-create data and backup directories (docker-compose volume mounts over /data)
RUN mkdir -p /data /data/backup

EXPOSE 8090

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]
