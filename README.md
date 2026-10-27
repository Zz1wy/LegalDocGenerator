# ⚖️ 智能法律文书辅助生成系统

基于 LLM（大语言模型）的智能法律文书**辅助**生成工具，支持 9 种常见法律文书的自动生成，并使用 **RAG（检索增强生成）** 辅助检索法律条文。生成结果仍需人工核对，不构成法律意见。

> ⚠️ **免责声明**：本项目仅用于技术演示/学习，不提供法律服务。AI 输出可能错误、遗漏或过时，使用前必须由执业律师或专业人士复核。详见 [DISCLAIMER.md](DISCLAIMER.md)。

## ✨ 功能特性

- 📝 **9 种法律文书**：劳动合同、民事起诉状、借条、租赁合同、离婚协议书、股权转让协议、保密协议、房屋买卖合同、委托代理书
- 🤖 **AI 智能生成**：基于 Ollama + Qwen2.5 本地模型，也可切换到任意 OpenAI 兼容云 API
- 🔍 **RAG 增强**：法条级切块 + 向量/BM25 混合检索 + 可选 LLM 重排，检索《民法典》《劳动合同法》等条文作为上下文
- 📚 **可追溯引用**：返回法名、条号、编/章、公布/施行日期和官方来源链接，并在生成后校验文内引用是否真实存在
- 📊 **可复现评测**：77 条金标查询，报告 Recall@5、MRR、nDCG@5 与 P50/P95 延迟，支持 dense / hybrid / hybrid+rerank 消融
- 👤 **用户系统**：注册/登录/JWT 认证，数据隔离
- 📜 **历史记录**：保存、搜索、筛选、导出生成记录
- ✏️ **在线编辑**：生成结果与历史记录均可直接修改并保存回数据库
- 📥 **多格式导出**：Word（保留版式）、打印/另存为 PDF、一键复制全文
- 🐳 **Docker 部署**：一键容器化部署，健康检查与反向代理配置齐全
- ✅ **CI**：GitHub Actions 自动跑后端测试、前端构建和生产依赖审计

## 🛠️ 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Vue 3 + Vite + Element Plus |
| 后端 | FastAPI + Python 3.11 |
| 数据库 | MySQL 8.0 |
| LLM | Ollama + Qwen2.5:7B，或 OpenAI 兼容云 API（DeepSeek 等） |
| RAG | ChromaDB + nomic-embed-text + rank-bm25/jieba + weighted RRF |
| 评测 | 自建 77 条金标集，Recall@K / MRR / nDCG@K / 延迟 |
| 部署 | Docker + Docker Compose + Nginx |

## ☁️ LLM Provider：本地与云 API

生成模型通过 `backend/llm/` 的 Provider 抽象选择，检索与引用逻辑不变：

| 变量 | 本地 Ollama（默认） | OpenAI 兼容云 API |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `openai` / `deepseek` |
| `LLM_MODEL` | `qwen2.5:7b` | 例如 `deepseek-chat` |
| `LLM_BASE_URL` | — | 例如 `https://api.deepseek.com/v1` |
| `LLM_API_KEY` | — | 你的密钥 |

- 云端模式只替换**生成模型**；embedding 仍由本地 Ollama 的 `EMBEDDING_MODEL` 提供。
- 更换 embedding 模型后必须重建 Chroma 索引，并重跑评测；不同模型的向量不可混用。
- 重排默认关闭（`RERANK_ENABLED=false`）。开启后每次检索会多一次 LLM 调用，延迟增加。

## 🧠 RAG 检索链路

```
用户查询
  └─ 口语化术语扩展（query.py，可解释的规则映射）
       ├─ 向量召回 Top-20（Chroma + nomic-embed-text）
       └─ BM25 召回 Top-20（jieba 分词 + rank-bm25）
            └─ BM25 Top-4 保底 + weighted RRF 补齐 → Top-5
                 └─ 可选 LLM 列表重排（rerank.py）
                      └─ 带法名/条号/版本/来源的 Prompt 上下文
                           └─ 生成 + 引用落地校验（citations.py）
```

设计取舍：

- **法条级切块**：按“第 X 条”切分，长条文再按长度二次切分；每个块保留法名、编/章、条号、公布/施行日期和官方链接。
- **BM25 保底**：法律检索对法名、条号、专业术语的精确命中非常敏感。纯向量在中文法律语料上召回偏弱，等权 RRF 会把 BM25 的第一名挤下去，因此先保底 BM25 Top-4，剩余名额再用 weighted RRF 补齐。
- **查询扩展**：只追加规范术语，不替换用户原话；例如“社保 → 社会保险费”“房东 → 出租人”。它是检索归一化，不是“理解法律”。
- **引用校验**：只机械验证《法名》第 X 条是否存在于本地语料；条号存在不等于引用恰当，仍需人工复核。
- **可复现评测**：金标集、评测脚本和报告都在仓库里，指标可重跑，不把“感觉更准”当结论。

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
# 编辑 .env 填入你的数据库密码和密钥；
# DB_USER 保持 legal_app，不要用 root 运行后端。
# 默认 LLM_PROVIDER=ollama；如用云 API：
#   LLM_PROVIDER=openai
#   LLM_BASE_URL=https://api.deepseek.com/v1
#   LLM_API_KEY=你的密钥
#   LLM_MODEL=deepseek-chat

# 4. 初始化数据库并创建最小权限应用账号
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS legal_doc_db CHARACTER SET utf8mb4;"
mysql -u root -p legal_doc_db < init.sql
mysql -u root -p -e "CREATE USER IF NOT EXISTS 'legal_app'@'localhost' IDENTIFIED BY '替换成你的应用密码'; GRANT ALL PRIVILEGES ON legal_doc_db.* TO 'legal_app'@'localhost'; FLUSH PRIVILEGES;"
# 把同一个应用密码写入 backend/.env 的 DB_PASSWORD

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
# 生成 JWT 密钥（分别填入 SECRET_KEY）
python -c "import secrets; print(secrets.token_hex(32))"
# 生成两个不同的数据库密码：DB_ROOT_PASSWORD（仅初始化/运维）
# 和 DB_PASSWORD（应用账号 legal_app 使用）
python -c "import secrets; print(secrets.token_hex(24))"
# 保持 DB_USER=legal_app，不要把后端配置成 root
# 配置 ADMIN_USERNAMES=你的用户名（不要留空，否则 compose 拒绝启动）
# ⚠️ 先注册自己的账号，再把用户名填进去；不要在账号尚未注册时使用 admin
#    这类容易被猜到的名字——本项目注册接口开放，存在被抢注的风险

# 2. 一键启动全部服务（需先解决 Docker Hub 网络问题）
docker-compose up -d

# 3. 确认服务状态
docker-compose ps

# 4. 访问 http://localhost
```

> SECRET_KEY、DB_ROOT_PASSWORD、DB_PASSWORD 和 ADMIN_USERNAMES 都故意**没有默认值**：不配置的话 `docker-compose` 会直接报错退出。
> 这是有意的 fail-closed 设计——宁可起不来，也不要带着一个人尽皆知的密钥或弱密码上线。
>
> 首次初始化时，MySQL 官方镜像会根据 `MYSQL_USER` / `MYSQL_PASSWORD` 创建应用账号并只授权 `DB_NAME` 数据库。**如果已有旧的 `mysql_data` 数据卷，入口脚本不会再次创建用户**；需要手动创建 `legal_app` 账号，或重建数据卷（会丢失已有数据）。

## 📁 项目结构

```
LegalDocGenerator/
├── backend/
│   ├── main.py              # FastAPI 后端入口
│   ├── database.py          # 数据库操作
│   ├── requirements.txt     # Python 运行依赖
│   ├── requirements-dev.txt # 测试依赖（pytest 等）
│   ├── init.sql             # 数据库建表脚本
│   ├── Dockerfile           # 后端容器化
│   ├── llm/                 # LLM Provider 抽象
│   │   ├── factory.py         # ollama / OpenAI 兼容客户端选择
│   │   ├── ollama.py          # 本地 Ollama（生成 + 流式）
│   │   └── openai_compat.py   # 任意 OpenAI 兼容云 API
│   ├── rag/                 # RAG 检索增强模块
│   │   ├── knowledge_base.py  # 法条解析、切块、Chroma 建库
│   │   ├── indexer.py         # 向量 + BM25 混合索引单例
│   │   ├── hybrid.py          # weighted RRF 融合 / BM25 保底
│   │   ├── query.py           # 口语化查询 → 法律术语扩展
│   │   ├── rerank.py          # 可选 LLM 列表重排
│   │   ├── citations.py       # 引用抽取、落地校验、来源区块
│   │   ├── retriever.py       # 同步/异步检索入口
│   │   └── legal_data/        # 10 个法律文件 + 模板 + manifest
│   ├── evaluation/          # RAG 离线评测
│   │   ├── golden_set.jsonl   # 77 条带法条标注的查询
│   │   ├── metrics.py         # Recall / MRR / nDCG / 分位数
│   │   ├── run_eval.py        # 5 组检索配置消融
│   │   └── reports/           # 生成的 JSON + Markdown 报告
│   └── tests/               # 测试套件（245 项）
│       ├── test_api_auth.py    # 认证测试
│       ├── test_api_docs.py    # 文书生成/SSE 测试
│       ├── test_api_history.py # 历史记录/在线编辑测试
│       ├── test_database.py    # 数据库测试
│       ├── test_rag.py         # RAG 集成测试
│       ├── test_hybrid.py      # RRF / BM25 / 降级路径
│       ├── test_query.py       # 查询扩展
│       ├── test_citations.py   # 引用抽取与校验
│       ├── test_chunking.py    # 法条切块与元数据
│       ├── test_eval_metrics.py # 评测指标
│       ├── test_llm_provider.py # LLM Provider
│       ├── test_rate_limit.py  # 限流/代理 IP 测试
│       ├── test_edge_cases.py  # 边界值/安全测试
│       └── test_integration.py # 集成测试
├── frontend/
│   ├── src/
│   │   ├── App.vue           # 主组件（生成/编辑/导出/历史）
│   │   └── components/
│   │       └── Login.vue     # 登录组件
│   ├── Dockerfile            # 前端容器化（多阶段构建）
│   ├── nginx.conf            # 容器实际使用的 Nginx server 配置
│   └── package.json
├── .github/
│   ├── workflows/ci.yml      # CI：后端测试 + 前端构建 + 依赖审计
│   └── dependabot.yml        # 依赖更新
├── docker-compose.yml        # Docker 编排
├── nginx.conf                # 独立部署时的完整 Nginx 参考配置
├── LICENSE                   # 代码许可证（MIT）
├── SECURITY.md               # 安全问题报告方式
├── DISCLAIMER.md             # 法律与使用免责声明
├── SOURCES.md                # 法律数据来源与核对清单
└── README.md
```

## 🧪 运行测试

```bash
cd backend

# 安装测试依赖
pip install -r requirements-dev.txt

# 全部测试（含 RAG；需 Ollama 与已构建的 Chroma 索引）
pytest -q

# 不含 RAG 的快速测试
pytest tests/ --ignore=tests/test_rag.py -q

# 覆盖率报告
pytest tests/ --cov=. --cov-report=html
```

CI 会运行不含 `test_rag.py` 的测试、构建前端，并运行生产依赖审计，见 `.github/workflows/ci.yml`。

### RAG 检索评测

```bash
cd backend
# 全部 77 条查询，默认跑 5 组对照（含纯 BM25 与等权 RRF）
python -m evaluation.run_eval

# 不跑 LLM 重排的快速版本
python -m evaluation.run_eval --configs dense,bm25,hybrid-rrf,hybrid
```

报告写入 `backend/evaluation/reports/rag_eval_<timestamp>.{json,md}`。指标按法条去重：同一法条被切成多个块时只计一次；延迟只包含检索/重排，不含生成。

### 评测结果（本机实测）

> 数据：77 条查询（70 条规范表述 + 7 条口语化），Top-5，候选池 20；Ollama + nomic-embed-text。所有配置都使用同一套查询扩展；`bm25` 指只用关键词通道。

| 配置 | Recall@5 | MRR | nDCG@5 | P50 | P95 |
|---|---:|---:|---:|---:|---:|
| `dense`（纯向量） | 0.4156 | 0.3223 | 0.3457 | 24 ms | 29 ms |
| `bm25`（纯关键词） | **0.9740** | 0.8907 | 0.9119 | 11 ms | 17 ms |
| `hybrid-rrf`（等权 RRF） | 0.8312 | 0.6006 | 0.6585 | 35 ms | 43 ms |
| `hybrid`（BM25 保底 + weighted RRF） | **0.9740** | 0.8907 | 0.9119 | 35 ms | 43 ms |
| `hybrid+rerank`（+ LLM 重排） | **0.9740** | **0.9351** | **0.9453** | 2176 ms | 2386 ms |

读法：

- **纯向量只有 41.6%**：`nomic-embed-text` 对中文法律语义区分度不足，45/77 条查询在 Top-5 内完全未命中。这是当前最大瓶颈，而不是 RRF 参数问题。
- **等权 RRF 反而掉到 83.1%**：向量噪声会把 BM25 的第一名“投票”下去。改成 BM25 Top-4 保底后回到 97.4%，失败数从 13 条降到 2 条。
- **重排提升排序质量，但不提升召回**：MRR 0.8907 → 0.9351，nDCG 0.9119 → 0.9453；剩下 2 条失败不在 Top-10 候选池内，所以 LLM 没机会选中。代价是 P50 从 35 ms 增至约 2.2 s，因此默认关闭。
- 剩余失败：`lease_repair`（民法典第 712 条在 BM25 中仅排第 23）和 `labor_social_insurance_resign_colloquial`（劳动合同法第 38 条排第 10）。下一步应换中文 embedding、扩大重排候选池或做 doc_type 过滤，而不是继续调 RRF 权重。

## ⚠️ 已知限制与下一步

- **Embedding 是主要瓶颈**：当前 `nomic-embed-text` 不擅长中文法律语义，纯向量 Recall@5 仅 41.6%。可迁移到 `bge-m3` 等中文模型，但换模型后必须重建索引、重跑全部评测；不同模型的向量不能混用。
- **金标集规模有限**：77 条查询、78 个标注法条，且以“定位到某一条”为主，不等于真实用户分布。指标适合版本间对比，不宜对外宣称线上准确率。
- **查询扩展是规则表**：可解释、可测试，但覆盖有限；复杂口语、多轮追问、否定表达仍需要 LLM 查询改写。
- **引用校验只到“条号存在”**：不检查法规是否已修订/废止、是否与案件事实匹配，也不检查模型是否曲解条文；`valid` 不代表引用正确。
- **重排默认关闭**：本地 7B 模型单次重排约 2.2 s，且受候选池限制；更大候选池需要更长上下文或更强的重排模型。
- **法条正文尚未逐条对照官方数据库**：来源与版本已记录在 [SOURCES.md](SOURCES.md)，真实业务使用前仍需逐条核对。
- **工程侧**：同步 MySQL 调用只在文书保存处做了线程池卸载；登录/历史等链路仍可进一步 async 化；依赖版本可继续收紧。

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
| POST | `/api/generate` | 生成法律文书（一次性返回，含 `citations`） |
| POST | `/api/generate/stream` | SSE 流式生成，`done` 事件携带 `citations` 与 `citation_check` |
| GET | `/api/history` | 获取历史记录 |
| PUT | `/api/history/{id}` | 保存在线编辑后的内容 |
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

## ⚖️ 法律数据与免责声明

- 本项目仅用于技术演示/学习，不构成法律意见；生成内容必须由人工复核。
- `backend/rag/legal_data/` 收录 6 部法律（10 个法律文件，共 1,984 个法条块）和 1 个保密协议模板。文本来自 [LawRefBook/Laws](https://github.com/LawRefBook/Laws) 的固定 commit，来源、版本、施行日期和官方核对入口见 [SOURCES.md](SOURCES.md)；**正文尚未逐条对照官方数据库**。
- 文内引用校验只验证“条号是否存在于本地语料”，不验证条文是否仍有效、是否适用于具体案件，也不代表引用内容被正确解释。
- 不要向公开演示输入真实身份证号、住址、手机号、银行账号、商业秘密或未脱敏法律文书。

## 🔒 安全说明

**认证与授权**

- 密码使用 bcrypt 哈希存储（`passlib`），不落明文
- JWT（HS256，PyJWT）令牌认证，有效期 7 天
- **SECRET_KEY 快速失败校验**：密钥为空、少于 32 字节或仍为示例值时，应用直接拒绝启动，避免用可预测密钥静默上线
- **认证失败绝不静默降级**：令牌无效/过期一律返回 401，不会被当成匿名请求处理
- **数据隔离**：历史记录的读取、修改与删除都强制要求登录；读取按 `user_id` 过滤，修改与删除在 SQL 层用 `WHERE id = %s AND user_id = %s` 保证只能操作自己的记录
- 运维接口 `POST /api/knowledge/reload` 需登录；公开/生产部署必须配置 `ADMIN_USERNAMES`，否则任何已登录用户都能触发高开销的知识库重建（仍有限流）

**输入与滥用防护**

- SQL 注入防护（全程参数化查询，无字符串拼接）
- 注册接口在服务端校验：用户名 2–50 字符、密码至少 8 字符且不超过 72 字节（bcrypt 上限），不只依赖前端表单
- 请求体规模限制：单次最多 50 个字段、单字段最长 10000 字；在线编辑保存上限 200000 字，超出返回 422
- `generated_records.generated_content` 使用 `MEDIUMTEXT`（而非 64KB 的 `TEXT`），避免长文书保存时被数据库截断/拒绝；已有库需执行 `init.sql` 末尾的升级 SQL
- 接口级限流（固定窗口）：登录/注册/生成/知识库重建各有独立配额，超限返回 429 + `Retry-After`
- **反向代理下的真实 IP**：仅当直连对端位于 `TRUSTED_PROXY_NETWORKS` 内时才采信 `X-Forwarded-For`，且只取 Nginx 追加的最右一项；避免全站共享限流桶，也防止客户端伪造来源 IP
- 限流计数器会定期清理过期条目，长期运行不会无限增长
- MySQL、Ollama、后端端口均只绑定宿主机回环地址，不对局域网暴露；外部流量统一走前端 Nginx
- **数据库最小权限**：后端使用 `DB_USER=legal_app`，仅拥有 `DB_NAME` 数据库权限；MySQL root 只用于首次初始化/运维，不参与应用连接

**配置管理**

- `.env*`（包括 `.env.local`、`.env.prod`、`.env.development`、`.env.test`、`.env.production`）均已加入 `.gitignore`，不会提交到仓库
- 后端与 Docker 构建上下文也通过 `.dockerignore` 排除全部 `.env*` 变体
- `SECRET_KEY` 未配置、为空或少于 32 字节时，后端拒绝启动；`DB_ROOT_PASSWORD`、`DB_PASSWORD`、`ADMIN_USERNAMES` 未配置或为空时，Docker Compose 拒绝启动。它们都不会回退到弱默认值
- 提供 `backend/.env.example`（本地运行）和根目录 `.env.example`（Docker 部署）作为模板

**安全报告**

发现安全问题请按 [SECURITY.md](SECURITY.md) 私下报告，不要在公开 issue 中粘贴真实密钥、个人信息或真实文书。

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

### 已修复的已知问题（v3）

| 问题 | 影响 | 修复 |
|------|------|------|
| Nginx 未关闭代理缓冲 | SSE 被攒包，流式输出退化成一次性返回 | `proxy_buffering off` + HTTP/1.1 |
| Nginx 未透传 `X-Forwarded-For` | Docker 下全站共享 Nginx 容器 IP，一个用户打满全员 429 | 透传真实 IP + 后端可信代理解析 |
| 限流计数器只增不减 | 长期运行内存缓慢泄漏 | 过期桶定期清理 |
| Ollama / 后端端口暴露到所有网卡 | 无鉴权的模型接口可被局域网滥用 | 仅绑定 `127.0.0.1` |
| 导出只是改了后缀的纯文本 | Word 打开无标题无版式 | 带样式的 HTML Word + 浏览器打印 PDF |

### 安全加固（公开前）

| 问题 | 修复 |
|------|------|
| Docker 默认弱密码 | 移除 `123456`；`DB_ROOT_PASSWORD`、`DB_PASSWORD`、`ADMIN_USERNAMES` 改为 fail-closed |
| 后端使用 MySQL root 连库 | 改用专用 `legal_app` 账号，只授予 `DB_NAME` 数据库权限 |
| `python-jose` 有未修复 advisory | 迁移到仍在维护的 PyJWT 2.x |
| 前端依赖高危漏洞 | `npm audit fix` 后 0 个 high；CI 增加生产依赖审计 |
| 异常信息原样返回客户端 | 统一返回通用错误，详细异常只写服务端日志 |
| `.env` 变体漏忽略 | `.gitignore` / `.dockerignore` 覆盖全部 `.env*`，只保留 `.env.example` |

已知但受控的依赖风险（ChromaDB）见 [SECURITY.md](SECURITY.md)。

## 📄 License

代码采用 [MIT License](LICENSE)。`backend/rag/legal_data/` 中的法律文本、模板及其他第三方材料的来源与许可状态需单独核对，详见 [SOURCES.md](SOURCES.md)。

## 📝 后续规划

- [x] 法条级切块与带版本/来源的引用对象
- [x] 向量 + BM25 混合检索、weighted RRF 与 LLM 重排
- [x] 口语化查询扩展与引用落地校验
- [x] 77 条金标集的 RAG 消融评测
- [x] 本地 Ollama / OpenAI 兼容云 API 双 Provider
- [x] 支持文书在线编辑
- [x] 添加 PDF 导出功能（浏览器打印 / 另存为 PDF）
- [x] CI 自动化测试与构建
- [ ] 迁移到更强的中文 embedding（如 bge-m3）并重跑评测
- [ ] 扩大金标集与语料范围，补充语义/时效性引用校验
- [ ] CD 自动化部署
