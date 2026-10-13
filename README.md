# ⚖️ 智能法律文书辅助生成系统

基于 LLM（大语言模型）的智能法律文书生成工具，支持 9 种常见法律文书的自动生成，集成 **RAG（检索增强生成）** 技术确保法律条文引用的准确性。

## ✨ 功能特性

- 📝 **9 种法律文书**：劳动合同、民事起诉状、借条、租赁合同、离婚协议书、股权转让协议、保密协议、房屋买卖合同、委托代理书
- 🤖 **AI 智能生成**：基于 Ollama + Qwen2.5 大模型，生成规范的法律文书
- 🔍 **RAG 增强**：自动检索《劳动法》《民法典》等真实法律条文，确保文书合法合规
- 👤 **用户系统**：注册/登录/JWT 认证，数据隔离
- 📜 **历史记录**：保存、搜索、筛选、导出生成记录
- 🐳 **Docker 部署**：一键容器化部署

## 🛠️ 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Vue 3 + Vite + Element Plus |
| 后端 | FastAPI + Python 3.11 |
| 数据库 | MySQL 8.0 |
| LLM | Ollama + Qwen2.5:7B |
| RAG | ChromaDB + nomic-embed-text |
| 部署 | Docker + Docker Compose + Nginx |

## 🚀 快速开始

### 方式一：本地运行

**前置条件：**
- Python 3.11+
- Node.js 20+
- MySQL 8.0
- Ollama

```bash
# 1. 下载模型
ollama pull qwen2.5:7b

# 2. 安装 RAG 依赖
cd backend
pip install -r requirements.txt
ollama pull nomic-embed-text

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env 填入你的 MySQL 密码和密钥

# 4. 初始化数据库（在 MySQL 中执行 backend/init.sql）

# 5. 启动后端
python main.py

# 6. 启动前端（新终端）
cd frontend
npm install
npm run dev

# 7. 访问 http://localhost:5173
```

### 方式二：Docker 部署

```bash
# 1. 配置根目录环境变量（docker-compose 读的是根目录的 .env）
cp .env.example .env
# 生成一个 JWT 密钥并填进 .env 的 SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"
# 同时在 .env 里填好 DB_PASSWORD

# 2. 一键启动全部服务（需先解决 Docker Hub 网络问题）
docker-compose up -d

# 3. 确认服务状态
docker-compose ps

# 4. 访问 http://localhost
```

> SECRET_KEY 故意**没有默认值**：不配置的话 `docker-compose` 会直接报错退出。
> 这是有意的 fail-closed 设计——宁可起不来，也不要带着一个人尽皆知的密钥上线。

## 📁 项目结构

```
LegalDocGenerator/
├── backend/
│   ├── main.py              # FastAPI 后端入口
│   ├── database.py          # 数据库操作
│   ├── requirements.txt     # Python 依赖
│   ├── init.sql             # 数据库建表脚本
│   ├── Dockerfile           # 后端容器化
│   ├── rag/                 # RAG 检索增强模块
│   │   ├── knowledge_base.py  # 知识库构建
│   │   ├── retriever.py       # 检索模块
│   │   └── legal_data/        # 法律条文数据
│   └── tests/               # 测试套件
│       ├── test_api_auth.py    # 认证测试
│       ├── test_api_docs.py    # 文书生成测试
│       ├── test_api_history.py # 历史记录测试
│       ├── test_database.py    # 数据库测试
│       ├── test_rag.py         # RAG 测试
│       ├── test_edge_cases.py  # 边界值/安全测试
│       └── test_integration.py # 集成测试
├── frontend/
│   ├── src/
│   │   ├── App.vue           # 主组件
│   │   └── components/
│   │       └── Login.vue     # 登录组件
│   ├── Dockerfile            # 前端容器化（多阶段构建）
│   └── package.json
├── docker-compose.yml        # Docker 编排
├── nginx.conf                # Nginx 配置
└── README.md
```

## 🧪 运行测试

```bash
cd backend

# 全部测试（不含 RAG）
pytest tests/ --ignore=tests/test_rag.py -v

# RAG 测试（需 Ollama 运行）
pytest tests/test_rag.py -v

# 覆盖率报告
pytest tests/ --cov=. --cov-report=html
```

## 📊 API 文档

启动后端后，访问 Swagger 文档：
- http://localhost:8000/docs

主要接口：
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/doc-types` | 获取文书类型列表 |
| POST | `/api/register` | 用户注册 |
| POST | `/api/login` | 用户登录 |
| GET | `/api/user` | 获取用户信息 |
| POST | `/api/generate` | 生成法律文书 |
| GET | `/api/history` | 获取历史记录 |
| DELETE | `/api/history/{id}` | 删除记录 |
| GET | `/api/knowledge/stats` | 知识库统计 |
| POST | `/api/knowledge/reload` | 重建知识库 |
| GET | `/health` | 健康检查 |

## 🗄️ 数据库表结构

```sql
users                -- 用户表
generated_records    -- 生成记录表
document_templates   -- 文书模板表
```

## 🔒 安全说明

**认证与授权**

- 密码使用 bcrypt 哈希存储（`passlib`），不落明文
- JWT（HS256）令牌认证，有效期 7 天
- **SECRET_KEY 快速失败校验**：密钥为空或仍为示例值时，应用直接拒绝启动，避免用可预测密钥静默上线
- **认证失败绝不静默降级**：令牌无效/过期一律返回 401，不会被当成匿名请求处理
- **数据隔离**：历史记录的读取与删除都强制要求登录，且 SQL 层用 `WHERE id = %s AND user_id = %s` 保证只能操作自己的记录
- 运维接口 `POST /api/knowledge/reload` 需登录；配置 `ADMIN_USERNAMES` 后可进一步限制为管理员

**输入与滥用防护**

- SQL 注入防护（全程参数化查询，无字符串拼接）
- 请求体规模限制：单次最多 50 个字段、单字段最长 10000 字，超出返回 422
- 接口级限流（固定窗口）：登录/注册/生成/知识库重建各有独立配额，超限返回 429 + `Retry-After`
- MySQL 端口只绑定宿主机回环地址，不对局域网暴露

**配置管理**

- `.env` 已加入 `.gitignore`，不会提交到仓库
- 提供 `backend/.env.example`（本地运行）和根目录 `.env.example`（Docker 部署）作为模板

### 已修复的已知问题（v2）

| 问题 | 影响 | 修复 |
|------|------|------|
| 未登录可读取全库历史记录 | 越权访问，所有用户的文书互相可见 | 读取接口强制登录 |
| 删除记录不校验归属 | 任何人可遍历 `record_id` 删光全站数据 | SQL 加 `user_id` 条件 |
| 令牌无效被静默当作游客 | 访问控制形同虚设 | 统一 401，移除所有 `except: pass` |
| `SECRET_KEY` 有可预测默认值 | 可离线伪造任意用户令牌 | 启动时快速失败校验 |
| `/api/knowledge/reload` 不鉴权 | 匿名可反复触发全库重向量化（DoS） | 要求登录 + 管理员白名单 |
| 请求体无大小限制 | 1MB 字段可撑爆 Prompt 上下文 | Pydantic 字段校验 |
| 前端写死 `http://localhost:8000` | Docker 部署下非本机访问全部失败 | 改用相对路径 `/api/...` |
| `onMounted` 注册了两次 | 挂载时接口被调用两遍 | 合并为单个 `onMounted` |

## 📝 后续规划

- [ ] 添加更多文书类型
- [ ] 补充更全面的法律条文知识库
- [ ] 支持文书在线编辑
- [ ] 添加 PDF 导出功能
- [ ] CI/CD 自动化部署
