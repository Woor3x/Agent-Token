FROM python:3.11-slim

WORKDIR /app

# gcc: cryptography 编译依赖; curl: healthcheck
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

# audit SQLite 落盘目录（由 docker-compose volume gateway_data 挂载至 /data）
RUN mkdir -p /data

EXPOSE 9200

# 端口固定 9200：DPoP htu 由 SDK 按 GATEWAY_URL=http://gateway:9200 签名，
# 必须与 gateway 收到请求时的 request.url 完全一致，不可变更。
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9200", "--workers", "1"]
