# ===== 阶段 1: 构建 =====
# 用 Node.js 编译 Vue 项目，产出 dist/ 静态文件
FROM node:20-alpine AS builder

WORKDIR /app

# 先复制依赖文件（利用 Docker 缓存）
COPY package.json package-lock.json* ./

# 按 package-lock.json 精确安装，保证构建可复现
# （npm install 可能悄悄升级依赖，导致「本地能跑、镜像里挂」）
RUN npm ci

# 复制源码并构建
COPY . .
RUN npm run build

# ===== 阶段 2: 运行 =====
# 用轻量级 nginx 托管构建好的静态文件
FROM nginx:alpine

# 把阶段 1 的 dist/ 复制到 nginx 的静态文件目录
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制自定义 nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
