# ===== Phase A: 后端 Dockerfile =====
# 每个指令的解释见下方注释

# ① FROM: 选择基础镜像（Python 3.11 精简版，体积小）
FROM python:3.11-slim

# ② WORKDIR: 设置容器内的工作目录（后面的命令都在这个目录下执行）
WORKDIR /app

# ③ 先复制依赖文件（利用 Docker 缓存层：依赖不常变，pip install 只在依赖变时才重跑）
COPY requirements.txt .

# ④ 安装依赖（--no-cache-dir 不保存 pip 缓存，减小镜像体积）
RUN pip install --no-cache-dir -r requirements.txt

# ⑤ 复制项目代码（放最后因为代码经常变，复用前面的缓存层）
COPY . .

# ⑥ EXPOSE: 声明容器运行时监听的端口（仅文档作用，实际映射在 docker-compose 中配置）
EXPOSE 8000

# ⑦ CMD: 容器启动时执行的命令
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
