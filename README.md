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
# 一键启动全部服务（需先解决 Docker Hub 网络问题）
docker-compose up -d

# 确认服务状态
docker-compose ps

# 访问 http://localhost
```

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

- 密码使用 bcrypt 哈希存储
- JWT Token 认证
- SQL 注入防护（参数化查询）
- .env 文件已加入 .gitignore，不会提交到仓库
- 使用 `.env.example` 作为配置模板

## 📝 后续规划

- [ ] 添加更多文书类型
- [ ] 补充更全面的法律条文知识库
- [ ] 支持文书在线编辑
- [ ] 添加 PDF 导出功能
- [ ] CI/CD 自动化部署
