# ⚖️ 智能法律文书辅助生成系统

基于 LLM（大语言模型）的智能法律文书**辅助**生成工具，支持 9 种常见法律文书的自动生成，并使用 **RAG（检索增强生成）** 辅助检索法律条文。生成结果仍需人工核对，不构成法律意见。

> ⚠️ **免责声明**：本项目仅用于技术演示/学习，不提供法律服务。AI 输出可能错误、遗漏或过时，使用前必须由执业律师或专业人士复核。详见 [DISCLAIMER.md](DISCLAIMER.md)。

## ✨ 功能特性

- 📝 **9 种法律文书**：劳动合同、民事起诉状、借条、租赁合同、离婚协议书、股权转让协议、保密协议、房屋买卖合同、委托代理书
- 🤖 **AI 智能生成**：基于 Ollama + Qwen2.5 本地模型，也可切换到任意 OpenAI 兼容云 API
- 🔍 **RAG 增强**：法条级切块 + 向量/BM25 混合检索 + 可选 LLM 重排，检索《民法典》《劳动合同法》等条文作为上下文
- 📚 **可追溯引用**：返回法名、条号、编/章、公布/施行日期和官方来源链接，并在生成后校验文内引用是否真实存在
- 📊 **可复现评测**：77 条金标查询的检索消融，外加 12 份文书的生成质量抽检（引用可追溯、引用落地、编造率、字段保留）
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
| RAG | ChromaDB + bge-m3 + rank-bm25/jieba + weighted RRF + doc_type 范围过滤 |
| 评测 | 77 条金标检索集（Recall@K / MRR / nDCG@K / 延迟）+ 12 份文书生成抽检（引用可追溯/落地/编造/字段保留） |
| 部署 | Docker + Docker Compose + Nginx |

## ☁️ LLM Provider：本地与云 API

生成模型通过 `backend/llm/` 的 Provider 抽象选择，检索与引用逻辑不变：

| 变量 | 本地 Ollama（默认） | OpenAI 兼容云 API |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `openai` / `deepseek` |
| `LLM_MODEL` | `qwen2.5:7b` | 例如 `deepseek-chat` |
| `LLM_BASE_URL` | — | 例如 `https://api.deepseek.com/v1` |
| `LLM_API_KEY` | — | 你的密钥 |

- 云端模式只替换**生成模型**；embedding 仍由本地 Ollama 的 `EMBEDDING_MODEL` 提供（默认 `bge-m3`）。
- 更换 embedding 模型后必须重建 Chroma 索引，并重跑评测；不同模型的向量不可混用。索引指纹包含模型名，下次启动会自动重建。
- 重排默认关闭（`RERANK_ENABLED=false`）。开启后每次检索会多一次 LLM 调用，延迟增加。

## 🧠 RAG 检索链路

```
用户查询
  └─ 口语化术语扩展（query.py，可解释的规则映射）
       └─ 文书类型范围过滤（scope.py：法名 / 分编 / 章节）
            ├─ 向量召回 Top-20（Chroma + bge-m3）
            └─ BM25 召回 Top-20（jieba 分词 + rank-bm25）
                 └─ BM25 Top-4 保底 + weighted RRF 补齐 → Top-5
                      └─ 可选 LLM 列表重排（rerank.py）
                           └─ 带法名/条号/版本/来源的 Prompt 上下文
                                └─ 生成 + 引用落地校验（citations.py）
```

设计取舍：

- **法条级切块**：按“第 X 条”切分，长条文再按长度二次切分；每个块保留法名、编/章、条号、公布/施行日期和官方链接。
- **融合参数随 embedding 重测**：`nomic-embed-text` 的中文纯向量 Recall 只有 41%，当时需要 BM25 Top-4 保底，防止向量噪声把关键词第一名挤下去；换成 bge-m3 后纯向量升到 96%，保底反而压低排序，默认改为等权 RRF。两组参数都保留环境变量，可复现消融结论。
- **按文书类型收窄**：9 类文书对应的法律依据差异很大（房屋买卖合同不该召回融资租赁、公司设立条文），因此用 chunk 上已有的法名/分编/章节元数据先圈定候选范围。映射表人工维护；民事起诉状可能基于合同、侵权或劳动争议，不设范围，避免硬过滤误杀。
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
ollama pull bge-m3        # 中文检索 embedding，约 1.2 GB

# 2. 安装 RAG 依赖
cd backend
pip install -r requirements.txt

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

### Docker 部署验证（2026-10-27，本机实测）

在**空数据卷**上冷启动整套服务，验证结果：

| 检查项 | 结果 |
|---|---|
| MySQL 初始化 | `init.sql` 建出 3 张表；`legal_app` 只能访问业务库 |
| Chroma 首次构建 | 空卷启动时重建 1,984 个法条块；重启后复用索引，约 9 s 恢复健康 |
| 前端 / 反向代理 | `/` 返回 200；`/health` 经 Nginx 转发返回 200 |
| 注册 → 登录 | 200，JWT 可正常使用 |
| 生成（劳动合同） | 200，45.6 s，1,514 字，返回 5 条参考法条 |
| 历史持久化 | 重启 backend 后仍有 2 条记录，用户数据未丢 |
| SSE 流式输出 | `text/event-stream`；首片段 0.66 s，344 个事件，17.5 s 完成；`done` 事件带引用与校验结果（4/4 可追溯） |
| 鉴权失败 | 无 token / 伪造 token 均返回 401，不会静默降级为游客 |
| fail-closed | 缺少 `SECRET_KEY` / `DB_*` 时 `docker compose config` 直接报错 |
| 端口暴露 | MySQL、后端只绑 `127.0.0.1`；前端 80 端口对外 |

验证中发现并修复了两个**只在 Docker 冷启动时才会暴露**的问题：

1. **索引元数据放在镜像层**：新 clone 的仓库没有 `rag/chroma_db`，但 `rag/index_meta.json` 会被打进镜像；空数据卷 + 旧元数据会让启动逻辑误判“知识库已是最新”而跳过重建。现在元数据放进 Chroma 数据卷，并校验实际 collection 数量；`.dockerignore` 也排除了宿主机索引。
2. **`init.sql` 未声明字符集**：MySQL 入口脚本按默认客户端字符集导入时，中文模板名会双重编码成 `åŠ³åŠ¨åˆåŒ`。已在脚本开头加 `SET NAMES utf8mb4;`，并重新验证 9 种文书类型名称正常。

> 本机 MySQL 已占用 3306，验证时用临时 override 把容器端口映射到 13306；仓库里的 `docker-compose.yml` 未改。
> 国内网络下 Docker Hub 直连可能超时，可先从 `docker.m.daocloud.io/library/<镜像>` 拉取并重新 tag 成标准名称；本次后端构建还临时指定了清华 PyPI 源，仓库默认仍使用官方 PyPI。
> 这次验证使用当时的默认 embedding `nomic-embed-text`。2026-10-28 起默认改为 `bge-m3`：`ollama-init` 会多拉取约 1.2 GB 模型，首次启动的知识库重建也会明显变慢；已验证的检索指标见下节。

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
│   │   ├── scope.py           # 按文书类型收窄法名/分编/章节
│   │   ├── query.py           # 口语化查询 → 法律术语扩展
│   │   ├── rerank.py          # 可选 LLM 列表重排
│   │   ├── citations.py       # 引用抽取、落地校验、来源区块
│   │   ├── retriever.py       # 同步/异步检索入口
│   │   └── legal_data/        # 10 个法律文件 + 模板 + manifest
│   ├── evaluation/          # RAG 检索与生成质量评测
│   │   ├── golden_set.jsonl   # 77 条带法条标注的检索查询
│   │   ├── metrics.py         # Recall / MRR / nDCG / 分位数
│   │   ├── run_eval.py        # 5 组检索配置消融
│   │   ├── generation_samples.jsonl  # 12 份生成抽检样本
│   │   ├── generation_review.json    # 人工复核结论（绑定生成文本指纹）
│   │   ├── run_generation_eval.py    # 生成链路抽检 + 重评分
│   │   └── reports/           # 生成的 JSON + Markdown 报告
│   └── tests/               # 测试套件（270 项）
│       ├── test_api_auth.py    # 认证测试
│       ├── test_api_docs.py    # 文书生成/SSE 测试
│       ├── test_api_history.py # 历史记录/在线编辑测试
│       ├── test_database.py    # 数据库测试
│       ├── test_rag.py         # RAG 集成测试
│       ├── test_hybrid.py      # RRF / BM25 / 降级路径
│       ├── test_scope.py       # 文书类型检索范围
│       ├── test_query.py       # 查询扩展
│       ├── test_citations.py   # 引用抽取与校验
│       ├── test_generation_eval.py # 生成抽检指标（引用/字段）
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
# 全部 77 条查询，默认跑 5 组对照（含纯向量、纯 BM25、等权 RRF、doc_type 过滤、LLM 重排）
python -m evaluation.run_eval

# 不跑 LLM 重排的快速版本
python -m evaluation.run_eval --configs dense,bm25,hybrid,hybrid+scope
```

报告写入 `backend/evaluation/reports/rag_eval_<timestamp>.{json,md}`。指标按法条去重：同一法条被切成多个块时只计一次；延迟只包含检索/重排，不含生成。

### 评测结果（2026-10-28 重跑）

> 数据：77 条查询（70 条规范表述 + 7 条口语化），Top-5，候选池 20；Ollama。所有配置使用同一套查询扩展；`bm25` 指只用关键词通道。`scope` 指按金标 `doc_type` 收窄法名/分编/章节。

| Embedding | 配置 | Recall@5 | MRR | nDCG@5 | P50 |
|---|---|---:|---:|---:|---:|
| nomic-embed-text | `dense`（纯向量） | 0.4026 | 0.3093 | 0.3327 | 37 ms |
| nomic-embed-text | `hybrid`（旧 BM25 保底） | 0.9740 | 0.8907 | 0.9119 | 45 ms |
| bge-m3 | `dense`（纯向量） | 0.9610 | 0.9026 | 0.9160 | 29 ms |
| bge-m3 | `bm25`（纯关键词） | 0.9740 | 0.8907 | 0.9119 | 9 ms |
| bge-m3 | `hybrid`（等权 RRF，默认） | 0.9805 | 0.9121 | 0.9291 | 40 ms |
| bge-m3 | `hybrid+scope`（默认 + 文书范围） | **0.9935** | **0.9301** | **0.9457** | 44 ms |

读法：

- **换中文 embedding 是这轮最大收益**：纯向量 Recall@5 从 40.3% 升到 96.1%，已经接近 BM25 的 97.4%。之前“向量拖后腿、必须靠 BM25 保底”的结论只对 `nomic-embed-text` 成立。
- **保底策略失效**：换 bge-m3 后，等权 RRF（98.05% / MRR 0.9121）高于旧的 BM25 Top-4 保底（97.40% / 0.8907）。默认已改为等权 RRF（`HYBRID_BM25_WEIGHT=1.0`、`HYBRID_BM25_GUARANTEE=0`），旧值仍可复现。
- **doc_type 范围过滤再补一刀**：在等权 RRF 上按文书类型收窄后，Recall 99.35%、MRR 0.9301；77 条查询全部至少命中一条标注法条。唯一缺口是口语化社保辞职查询漏了《劳动合同法》第四十六条（第三十八条仍在 Top-5）。
- **不再依赖 LLM 重排**：这组指标没有开重排，MRR 0.9301 已接近旧配置 nomic + 重排的 0.9351，而 P50 降到 44 ms。重排仍作为可选增强，默认关闭。
- 本轮完整报告：`backend/evaluation/reports/rag_eval_20261028_204059.{json,md}`；换 embedding 后必须重建索引，nomic 与 bge-m3 的向量不可混用。

### 生成质量抽检

检索评测只能回答“有没有找到法条”，不能回答“文书里有没有正确使用法条”。抽检用真实链路（RAG Prompt → LLM → 引用抽取）跑了 12 份文书，覆盖全部 9 种文书类型：

```bash
cd backend
# 重新生成并评测（本地 qwen2.5:7b 约 15 分钟；6 GB 显存下 embedding 与生成模型交替加载）
python -m evaluation.run_generation_eval

# 复用已有生成文本，只按当前引用抽取逻辑重算指标（不调用模型）
python -m evaluation.run_generation_eval --rescore evaluation/reports/generation_eval_<timestamp>.json
```

自动指标定义：

| 指标 | 含义 |
|---|---|
| 检索命中 | 样本标注的期望法条是否出现在本次 Top-5 |
| 引用可追溯 | 正文引用的《法名》第X条是否存在于本地语料 |
| 引用落地 | 该引用是否真的来自本次检索上下文（含 `[S1]` 标记） |
| 语料外引用 | 条号在本地语料中不存在，即模型编造 |
| 字段保留 | 姓名、金额、期限等短字段是否以原样出现（字面匹配，是下限） |

**结果（12 份文书，qwen2.5:7b，temperature=0.2，单次采样）：**

| 指标 | 2026-10-27（nomic） | 2026-10-28（bge-m3 + 范围过滤） |
|---|---:|---:|
| 检索命中率（标注法条在 Top-5） | 91.7%（11/12） | 83.3%（10/12） |
| 有引用的样本 | 10/12 | 10/12 |
| 引用总数（去重） | 31 | 32 |
| 引用可追溯 | 31/31（100%） | **32/32（100%）** |
| 引用落地 | 31/31（100%） | **32/32（100%）** |
| 语料外引用（编造条号） | 0 | **0** |
| 字段字面保留率 | 82.6% | 84.8% |
| 平均文书长度 | 845 字 | 809 字 |
| 检索延迟 P50 / P95 | 2092 / 4095 ms | 7514 / 31106 ms |
| 生成延迟 P50 / P95 | 23.0 / 28.0 s | 36.0 / 65.7 s |

延迟不能直接对比：10-28 这轮与另一批生成任务有部分重叠，更主要的是 6 GB 显存放不下 bge-m3 与 7B，两个模型在每份文书之间反复换入换出；独立检索评测（模型常驻）的 P50 仍是 40–44 ms。要消掉这笔开销，需要更大显存，或把 embedding 放到独立服务。

**人工复核（逐份检查引用与条款是否对应，仅针对 2026-10-27 那批文本）：**

| 判断 | 数量 | 含义 |
|---|---:|---|
| `supported` | 2 | 引用与文书内容直接对应 |
| `partial` | 7 | 部分引用相关，部分章错位或过度概括 |
| `unsupported` | 1 | 主要引用与文书类型不匹配 |
| `no_citation` | 2 | 正文没有引用具体条文 |

重跑后文本已变化，脚本现在用 `content_hash` 把复核结论绑定到具体生成文本；12 条旧复核记录在新报告中全部判定失效（`review_stale=12`），本次没有重新逐份复核。上表只作为“语义恰当性有风险”的证据，不能当作新文本的结论。

关键发现：

- **机械层面没有编造**：32 条引用全部能在本地语料中找到，且全部来自本次检索上下文；`[S#]` 约束和“只能引用下列条文”的 Prompt 要求是有效的。
- **“落地”不等于“恰当”**：上一轮人工复核里，有引用的 10 份只有 2 份完全站得住。例如商铺租赁合同把《民法典》第七百三十五、七百五十八条（融资租赁）和《公司法》第十二条（公司形式变更）写成依据；借贷起诉状用第六百七十条（利息不得预先扣除）支撑逾期还款；借条引用了《民事诉讼法》第一百一十八条（罚款、拘留）。这些条号都“真实存在且来自上下文”，但对案情不适用。
- **范围过滤修好了房屋买卖盲区**：上一轮该查询的 Top-5 没有买卖合同条文，模型只能写“未引用具体条文”；这一轮命中第五百九十五、五百九十六、六百三十八、六百四十一条和第二百一十一条，模型引用 5 条且全部落地。第二百零九条（不动产登记生效）仍未进 Top-5，说明范围只解决“去哪找”，不解决“排得准不准”。
- **小样本命中率回落，和 77 条金标不矛盾**：12 条里新漏了两条——民间借贷起诉状（不设范围，Top-5 全是借款合同章，但与标注的第六百六十七/六百七十五/一百二十二条错位）和委托代理书（Top-5 是第一百六十五、民诉第六十二、第九百三十六/九百二十五/一百六十三条，缺少第一百六十二/九百一十九条）。两处都是排序错位，不是范围过滤误杀；77 条金标的整体 Recall 仍从 97.4% 升到 99.35%。
- **字段保留率是下限**：金额“50000 元”写成“伍万元整”、日期“2026-11-01”写成中文日期都会计为未保留；人工看内容其实没有丢，但指标不做语义等价判断。

报告与复核记录：本轮指标见 `backend/evaluation/reports/generation_rescore_20261028_203955.{json,md}`，原始生成文本在同时间戳的 `generation_eval_*.json`；人工复核记录在 `backend/evaluation/generation_review.json`。最终指标以 rescore 报告为准。这是 12 份样本、单次采样的抽检，不是线上准确率承诺。

## ⚠️ 已知限制与下一步

- **bge-m3 的代价**：模型约 1.2 GB，首次构建要重新嵌入 1,984 个法条块；纯向量 Recall 已到 96.1%，但剩余缺口集中在口语化表达和“同一条文的多段拆分”上。换模型必须重建索引，不同模型的向量不能混用。
- **金标集规模有限**：77 条查询、78 个标注法条，且以“定位到某一条”为主，不等于真实用户分布。指标适合版本间对比，不宜对外宣称线上准确率。
- **查询扩展是规则表**：可解释、可测试，但覆盖有限；复杂口语、多轮追问、否定表达仍需要 LLM 查询改写。
- **引用校验只到“条号存在/来自上下文”**：不检查法规是否已修订/废止、是否与案件事实匹配，也不检查模型是否曲解条文；`valid` 不代表引用正确。两轮抽检中可追溯与落地都是 100%，但 2026-10-27 那批文本的人工复核只有 2/10 份的引用完全对应（新文本尚未重新复核）——语义恰当性仍是未解决的问题。
- **文书范围映射是人工维护的**：9 类文书按法名/分编/章节手工配置，漏配时上线会回退到全库检索，但静态映射无法表达“同一文书类型下不同案情适用不同法条”；民事起诉状因此不设范围。房屋买卖等具体类型的检索盲区已由金标验证改善，但仍不等于语义正确。
- **重排默认关闭**：本地 7B 模型单次重排约 2.2 s（nomic 时期）；使用 bge-m3 时，6 GB 显存下 embedding 与 7B 生成模型同时驻留会触发 CPU 卸载，重排延迟明显上升。重排的收益主要是排序质量，不提升召回；更大候选池需要更长上下文或更强的重排模型。
- **法条正文尚未逐条对照官方数据库**：来源与版本已记录在 [SOURCES.md](SOURCES.md)，真实业务使用前仍需逐条核对。
- **ChromaDB 上游告警依赖可达性判断**：1.5.9 是当前最新版，仍有 4 条针对服务端 API 的 RCE/跨租户越权告警（Dependabot 因两个依赖清单计为 8 条）。本项目只使用嵌入式 `PersistentClient`，不启动 Chroma HTTP 服务、不暴露端口，逐条可达性分析见 [SECURITY.md](SECURITY.md#known-dependency-advisories)；启用 Chroma 服务端模式前必须重新评估。
- **依赖只有下界约束**：`requirements.txt` 多处使用 `>=`，全新 Docker 构建会解析到比本地 venv 更新的版本（本次 fastapi 0.162.0 / langchain 1.4.7）；本地测试与容器验证不是同一组版本，需要严格复现时应生成锁文件。
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

### 已修复的已知问题（v4）

| 问题 | 影响 | 修复 |
|------|------|------|
| 章节边界元数据错位 | 每章最后一条被标成下一章，引用展示和按章节过滤都会误判 | 遇到标题先结算上一条再更新章节；`INDEX_VERSION` 升到 3，旧索引自动重建 |

已知但受控的依赖风险（ChromaDB）见 [SECURITY.md](SECURITY.md)。

## 📄 License

代码采用 [MIT License](LICENSE)。`backend/rag/legal_data/` 中的法律文本、模板及其他第三方材料的来源与许可状态需单独核对，详见 [SOURCES.md](SOURCES.md)。

## 📝 后续规划

- [x] 法条级切块与带版本/来源的引用对象
- [x] 向量 + BM25 混合检索、weighted RRF 与 LLM 重排
- [x] 口语化查询扩展与引用落地校验
- [x] 77 条金标集的 RAG 消融评测
- [x] 12 份文书的生成质量抽检（引用可追溯/落地/编造/字段保留 + 人工复核）
- [x] 本地 Ollama / OpenAI 兼容云 API 双 Provider
- [x] 支持文书在线编辑
- [x] 添加 PDF 导出功能（浏览器打印 / 另存为 PDF）
- [x] CI 自动化测试与构建
- [x] Docker 冷启动端到端验证（初始化、健康检查、SSE、持久化）
- [x] 迁移到 bge-m3 并重跑检索/生成评测（纯向量 Recall@5 41.6% → 96.1%）
- [x] 按文书类型收窄检索范围（法名/分编/章节），补齐房屋买卖等召回盲区
- [ ] 扩大金标集与语料范围，补充语义/时效性引用校验
- [ ] CD 自动化部署
