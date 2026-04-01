# ─── 构建阶段 ──────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# 系统依赖（opcua 需要 libxml2）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖到独立目录，方便复制
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ─── 运行阶段 ──────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL maintainer="WorkBuddy SRE <sre@example.com>"
LABEL version="1.0.0"
LABEL description="OPC UA REST API Bridge"

# 运行时系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户
RUN groupadd -r opcua && useradd -r -g opcua opcua

WORKDIR /app

# 从构建阶段复制已安装的包
COPY --from=builder /install /usr/local

# 复制项目代码
COPY --chown=opcua:opcua src/ ./src/

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 切换到非 root 用户
USER opcua

# 启动命令（生产模式用 gunicorn，4 个 worker）
CMD ["gunicorn", "src.api.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "4", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "60", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
