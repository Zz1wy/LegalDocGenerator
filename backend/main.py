from fastapi import Depends, FastAPI, HTTPException, Request
from starlette.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
import os
import json
import time
import asyncio
import ipaddress
import httpx
from dotenv import load_dotenv
from database import get_all_templates
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
import hashlib

# LLM Provider：Ollama / OpenAI 兼容 API 可切换
from llm import get_llm_client, get_model_name

# RAG 模块导入（带优雅降级：如果没有安装相关依赖，RAG 功能自动禁用）
try:
    from rag.knowledge_base import build_knowledge_base, get_knowledge_base_stats
    from rag.retriever import (
        aretrieve as rag_aretrieve,
        retrieve as rag_retrieve,
    )
    from rag.citations import verify_citations
    RAG_AVAILABLE = True
except ImportError as e:
    RAG_AVAILABLE = False
    print(f"⚠️ RAG 模块不可用: {e}")

# ⚠️ 必须先加载 .env，再读取环境变量
load_dotenv()

# ==================== 安全配置 ====================
# SECRET_KEY 用来签发和校验 JWT。JWT 的签名算法（HS256）是公开的，安全性完全
# 依赖这个密钥的保密性 —— 一旦密钥泄露或可被猜到，任何人都能离线伪造出
# 「user_id=1」的合法令牌，整个用户体系形同虚设。
# 所以这里采取「快速失败」策略：配置不合格就直接拒绝启动，而不是带着漏洞上线。
_PLACEHOLDER_SECRETS = {
    "change-me-to-a-random-secret-key",
    "default-secret-key-for-development-only",
    "change-me-in-production",
    "secret",
}

SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
if not SECRET_KEY or SECRET_KEY in _PLACEHOLDER_SECRETS:
    raise RuntimeError(
        "SECRET_KEY 未配置或仍为示例值，拒绝启动。\n"
        "请在 backend/.env 中设置一个随机密钥：\n"
        '  python -c "import secrets; print(secrets.token_hex(32))"'
    )
if len(SECRET_KEY.encode("utf-8")) < 32:
    raise RuntimeError(
        "SECRET_KEY 长度不足 32 字节，拒绝启动。\n"
        '请生成一个足够长的随机密钥：python -c "import secrets; print(secrets.token_hex(32))"'
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7

# 知识库重建等运维接口的管理员白名单（逗号分隔的用户名）。
# 留空时不启用该限制，只要求登录 —— 本地开发够用；线上部署应当显式配置。
ADMIN_USERNAMES = {u.strip() for u in os.getenv("ADMIN_USERNAMES", "").split(",") if u.strip()}

# 认证方案
# auto_error=False：没有 Authorization 头时返回 None 而不是直接 401，这样
# /api/generate 这类接口可以做成「可选登录」。注意「没带令牌」和「令牌无效」
# 是两回事：前者可以当游客，后者必须 401 —— 见 get_optional_user。
security = HTTPBearer(auto_error=False)

# 密码哈希配置
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="智能法律文书辅助生成系统")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== 限流 ====================
# 为什么需要：/api/generate 每次都会真调一次 LLM（又贵又慢），
# /api/knowledge/reload 会把整个知识库重新向量化。这两个接口如果没有任何频率
# 限制，一个几十行的脚本就能把服务打满 —— 未登录也能触发，成本极低。
#
# 这里实现的是最朴素的「固定窗口计数器」：同一 IP 在 window 秒内最多放行 limit 次。
# 已知局限（面试时主动说出来，比被问出来加分）：
#   1. 计数存在进程内存里，多 worker / 多副本部署时各进程各算各的。
#      生产环境应换成 Redis 共享计数，或直接在 Nginx 层用 limit_req 统一拦截。
#   2. 固定窗口在窗口交界处可能瞬时放行 2 倍流量（经典缺陷）。
#      要更平滑可以换成滑动窗口或令牌桶。
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() not in ("0", "false", "no")

# 反向代理（Nginx）后面的真实客户端 IP 解析。
#
# 为什么需要：Docker 部署时，所有请求都从 Nginx 容器转发进来，后端看到的
# request.client.host 永远是 Nginx 的内网地址。如果直接拿它做限流 key，全站
# 所有用户会共享同一个桶 —— 一个人打满 /api/generate，所有人一起 429。
#
# 安全边界：X-Forwarded-For 是客户端可以伪造的请求头，所以只有在「直连对端
# 本身就是可信代理」时才读取它，并且只取 Nginx 追加的最右一项
# （$proxy_add_x_forwarded_for 会把真实地址追加在列表末尾）。
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "false").lower() in ("1", "true", "yes", "on")


def _parse_trusted_networks(raw: str) -> tuple:
    """解析 CIDR 白名单。非法项跳过而不是让服务启动失败。"""
    networks = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            print(f"⚠️ 忽略非法的 TRUSTED_PROXY_NETWORKS 项: {item}")
    return tuple(networks)


# 默认覆盖本机回环和 Docker 常用私网段。注意：只有 TRUST_PROXY_HEADERS=true
# 时这份白名单才会生效，直连后端的外部请求无法伪造来源 IP。
TRUSTED_PROXY_NETWORKS = _parse_trusted_networks(os.getenv(
    "TRUSTED_PROXY_NETWORKS",
    "127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
))

RATE_LIMIT_RULES = {
    "/api/generate":         (20, 60),    # 每分钟 20 次
    "/api/generate/stream":  (20, 60),
    "/api/login":            (10, 60),
    "/api/register":         (5, 300),
    "/api/knowledge/reload": (2, 300),    # 重建知识库开销大，5 分钟最多 2 次
}

# (client_ip, path) -> [窗口起始时间, 本窗口内已放行次数]
_rate_buckets: dict = {}


def check_rate_limit(client_ip: str, path: str, now: float) -> Optional[int]:
    """
    检查是否超限。返回 None 表示放行；返回整数表示拒绝，
    值是建议客户端等待的秒数（对应 HTTP 头的 Retry-After）。

    now 由调用方传入（而不是函数内部读时钟），这样单元测试可以构造任意时间序列。
    """
    rule = RATE_LIMIT_RULES.get(path)
    if rule is None:
        return None

    limit, window = rule
    key = (client_ip, path)
    bucket = _rate_buckets.get(key)

    if bucket is None or now - bucket[0] >= window:
        _rate_buckets[key] = [now, 1]
        return None

    bucket[1] += 1
    if bucket[1] > limit:
        return max(1, int(window - (now - bucket[0])) + 1)
    return None


_RATE_BUCKET_PRUNE_THRESHOLD = 1024


def prune_rate_buckets(now: float) -> int:
    """
    清掉已经过期的计数器。

    _rate_buckets 的 key 是 (client_ip, path)。如果只增不减，每个访问过的 IP
    都会永久留下一个列表 —— 长期运行时就是一条缓慢的内存泄漏。这里在字典
    规模超过阈值时顺手清理，返回删除的条目数（便于测试）。
    """
    max_window = max(window for _, window in RATE_LIMIT_RULES.values())
    stale_keys = [
        key for key, (started_at, _) in _rate_buckets.items()
        if now - started_at >= max_window
    ]
    for key in stale_keys:
        del _rate_buckets[key]
    return len(stale_keys)


def _parse_ip(value: str):
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _is_trusted_proxy(ip) -> bool:
    return any(ip in network for network in TRUSTED_PROXY_NETWORKS if network.version == ip.version)


def get_client_ip(request: Request) -> str:
    """
    解析真实客户端 IP（用于限流 key）。

    - 默认（未开启 TRUST_PROXY_HEADERS）：直接用 TCP 对端地址。
    - 开启后：仅当直连对端位于 TRUSTED_PROXY_NETWORKS 内时才采信
      X-Forwarded-For，并且**只取最右一项**。

    为什么只取最右一项：Nginx 的 `$proxy_add_x_forwarded_for` 会把真实客户端
    地址追加到列表末尾，而左边的部分是客户端可以任意伪造的。若改为「从右往左
    跳过可信代理」，当真实客户端本身是私网地址（企业内网很常见）时会被误认为
    代理而跳过，反而让伪造的左侧条目生效。当前是单层 Nginx，取最右一项最简单
    也最安全。多级代理场景应改为显式配置每一跳的信任关系。
    """
    peer = request.client.host if request.client else "unknown"
    if not TRUST_PROXY_HEADERS:
        return peer

    peer_ip = _parse_ip(peer)
    if peer_ip is None or not _is_trusted_proxy(peer_ip):
        return peer

    forwarded_for = request.headers.get("x-forwarded-for", "")
    if not forwarded_for:
        return peer

    entries = [part.strip() for part in forwarded_for.split(",") if part.strip()]
    if not entries:
        return peer

    # 最后一项由我们信任的 Nginx 追加，前面的内容不可信，直接忽略
    candidate = _parse_ip(entries[-1])
    return str(candidate) if candidate is not None else peer


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if RATE_LIMIT_ENABLED:
        now = time.monotonic()
        client_ip = get_client_ip(request)
        retry_after = check_rate_limit(client_ip, request.url.path, now)
        if retry_after is not None:
            return JSONResponse(
                status_code=429,
                content={"success": False, "message": "请求过于频繁，请稍后再试"},
                headers={"Retry-After": str(retry_after)},
            )
        if len(_rate_buckets) > _RATE_BUCKET_PRUNE_THRESHOLD:
            prune_rate_buckets(now)
    return await call_next(request)


# ==================== Pydantic 模型 ====================

# 请求体规模上限：既是防 DoS，也是在收窄提示注入可以藏身的空间
MAX_FIELDS = 50           # 单次请求最多提交多少个字段
MAX_FIELD_LEN = 10_000    # 单个字段值的最大字符数


class GenerateRequest(BaseModel):
    doc_type: str = Field(..., min_length=1, max_length=50)
    fields: dict = Field(default_factory=dict)

    @field_validator("fields")
    @classmethod
    def _limit_fields(cls, v: dict) -> dict:
        """
        限制 fields 的规模。

        不限制时，客户端可以塞进一个 1MB 的字段值 —— 它会原样拼进 Prompt 送给
        LLM，既白白烧算力（DoS），又给了攻击者一大块可以藏「忽略以上指令」的地盘。
        """
        if len(v) > MAX_FIELDS:
            raise ValueError(f"字段数量不能超过 {MAX_FIELDS} 个")
        for key, value in v.items():
            if len(str(key)) > 100:
                raise ValueError("存在过长的字段名")
            if len(str(value)) > MAX_FIELD_LEN:
                raise ValueError(f"字段「{key}」的内容超过 {MAX_FIELD_LEN} 字上限")
        return v

class UserRegister(BaseModel):
    """
    注册请求。

    服务端必须自己校验用户名/密码，不能只依赖前端表单：API 可以被直接调用。
    尤其要拒绝空密码和超长密码——bcrypt 只处理前 72 字节，超出部分被静默忽略，
    会让两个不同的长密码得到相同的哈希。
    """
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=8)
    nickname: Optional[str] = Field(default=None, max_length=50)

    @field_validator("username", mode="before")
    @classmethod
    def _strip_username(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("password")
    @classmethod
    def _limit_password_bytes(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 72:
            raise ValueError("密码不能超过 72 字节（bcrypt 的限制）")
        return v

class UserLogin(BaseModel):
    # 登录侧只做基本上限，避免超长请求体；不强制新密码策略，
    # 否则会锁死历史上用短密码注册的账号。
    username: str = Field(..., max_length=50)
    password: str = Field(..., max_length=200)

class TokenResponse(BaseModel):
    success: bool
    token: Optional[str] = None
    user: Optional[dict] = None
    message: Optional[str] = None

class GenerateResponse(BaseModel):
    success: bool
    content: str
    message: Optional[str] = None

class UpdateRecordRequest(BaseModel):
    """在线编辑后保存的请求体。上限与生成字段的规模上限保持同一量级。"""
    content: str = Field(..., min_length=1, max_length=200_000)

# ==================== 密码 & JWT 工具函数 ====================

def get_password_hash(password: str) -> str:
    """生成密码哈希（使用 bcrypt）"""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码（支持 bcrypt 和旧的 SHA-256 + salt 格式）"""
    # 先尝试 bcrypt 验证
    try:
        if pwd_context.verify(plain_password, hashed_password):
            return True
    except Exception:
        # 哈希格式不是 bcrypt 时 passlib 会抛异常，这里只是「换一种方式再试」，
        # 不是吞掉错误。注意用 except Exception 而不是裸 except —— 裸 except 连
        # KeyboardInterrupt / SystemExit 都会一并吞掉，让 Ctrl+C 失效。
        pass

    # 如果 bcrypt 验证失败，尝试旧的 SHA-256 + salt 格式
    try:
        salt, stored_hash = hashed_password.split('$')
        return hashlib.sha256((plain_password + salt).encode()).hexdigest() == stored_hash
    except Exception:
        return False

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """创建 JWT 访问令牌"""
    to_encode = data.copy()
    # 用带时区的 UTC 时间。datetime.utcnow() 返回的是 naive datetime，
    # 在 Python 3.12+ 已废弃，而且很容易和本地时间混用出错。
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[dict]:
    """
    可选登录：没带令牌 → 返回 None（游客）；带了令牌但无效 → 直接 401。

    ⚠️ 这是本次安全修复的核心。旧代码在每个接口里重复这种写法：

        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("user_id")
        except:
            pass          # ← 伪造或过期的令牌被静默当成「游客」

    配合上「游客能看到全库记录」的分支，访问控制就完全失效了：攻击者不需要
    任何凭据，直接以匿名身份读全站数据。正确语义是——**认证失败就报错，
    绝不静默降级为匿名**。
    """
    if credentials is None:
        return None

    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="无效或已过期的认证凭据")

    user_id = payload.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=401, detail="无效或已过期的认证凭据")

    from database import get_user_by_id
    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


async def get_current_user(current_user: Optional[dict] = Depends(get_optional_user)) -> dict:
    """必须登录：未登录一律 401"""
    if current_user is None:
        raise HTTPException(status_code=401, detail="未提供认证凭据")
    return current_user

# ==================== LLM 配置 ====================

# 兼容旧配置：Provider 层会优先读 OLLAMA_HOST
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")


async def call_ollama(prompt: str) -> str:
    """调用当前配置的 LLM Provider（默认 Ollama）。

    函数名保留是为了兼容现有测试与调用点；实际由 llm.factory 决定
    走本地 Ollama 还是 OpenAI 兼容 API。
    """
    try:
        result = await get_llm_client().generate(prompt)
        return result.text
    except Exception as e:
        raise Exception(f"调用大模型失败: {str(e)}")


async def stream_ollama(prompt: str):
    """流式调用当前 LLM Provider，逐片段 yield 文本。"""
    try:
        async for chunk in get_llm_client().stream(prompt):
            if chunk:
                yield chunk
    except Exception as e:
        raise Exception(f"调用大模型失败: {str(e)}")

def build_prompt(doc_type: str, fields: dict) -> str:
    """构建提示词"""
    field_str = ", ".join([f"{k}={v}" for k, v in fields.items() if v])

    templates = {
        "劳动合同": f"""请生成一份规范的劳动合同书，包含以下信息：{field_str}
要求：
1. 格式规范，使用法律文书标准格式
2. 包含甲方乙方信息、合同期限、工作内容、工资待遇等必要条款
3. 包含双方权利义务、合同变更解除等条款
4. 包含签名日期栏
请直接输出文书内容，不要解释。""",

        "民事起诉状": f"""请生成一份规范的民事起诉状，包含以下信息：{field_str}
要求：
1. 格式规范，包含原告被告信息
2. 清晰列出诉讼请求
3. 详细陈述事实与理由
4. 包含证据和证据来源部分
5. 包含签名日期栏
请直接输出文书内容，不要解释。""",

        "借条": f"""请生成一份规范的借条，包含以下信息：{field_str}
要求：
1. 格式规范，包含出借人借款人信息
2. 明确借款金额（大写）
3. 包含借款日期和还款日期
4. 包含借款人签名按手印栏
请直接输出文书内容，不要解释。""",

        "租赁合同": f"""请生成一份规范的租赁合同，包含以下信息：{field_str}
要求：
1. 格式规范，包含出租方承租方信息
2. 明确租赁物、租期、租金
3. 包含双方权利义务条款
4. 包含违约金条款
5. 包含签名日期栏
请直接输出文书内容，不要解释。""",

        "离婚协议书": f"""请生成一份规范的离婚协议书，包含以下信息：{field_str}
要求：
1. 格式规范，包含男方女方基本信息
2. 明确子女抚养权归属及抚养费
3. 明确夫妻共同财产分割方案
4. 明确债务承担方式
5. 包含双方签名栏和日期
请直接输出文书内容，不要解释。""",

        "股权转让协议": f"""请生成一份规范的股权转让协议，包含以下信息：{field_str}
要求：
1. 格式规范，包含转让方和受让方基本信息
2. 明确目标公司信息和股权转让比例
3. 明确股权转让价格及付款方式
4. 包含陈述与保证条款
5. 包含违约责任和争议解决条款
6. 包含双方签名栏和日期
请直接输出文书内容，不要解释。""",

        "保密协议": f"""请生成一份规范的保密协议（NDA），包含以下信息：{field_str}
要求：
1. 格式规范，包含甲方乙方基本信息
2. 明确保密信息的定义和范围
3. 明确保密期限和保密义务
4. 明确违约责任和赔偿条款
5. 包含双方签名栏和日期
请直接输出文书内容，不要解释。""",

        "房屋买卖合同": f"""请生成一份规范的房屋买卖合同，包含以下信息：{field_str}
要求：
1. 格式规范，包含卖方买方基本信息
2. 明确房屋基本情况（地址、面积、产权证号）
3. 明确成交价格及付款方式
4. 明确交付时间和过户时间
5. 包含税费约定和违约责任
6. 包含双方签名栏和日期
请直接输出文书内容，不要解释。""",

        "委托代理书": f"""请生成一份规范的委托代理书，包含以下信息：{field_str}
要求：
1. 格式规范，包含委托人和受托人基本信息
2. 明确委托事项和权限范围
3. 明确委托期限
4. 明确双方权利义务
5. 包含双方签名栏和日期
请直接输出文书内容，不要解释。"""
    }

    return templates.get(doc_type, f"请生成一份{doc_type}，包含以下信息：{field_str}")

def _augment_with_rag(base_prompt: str, retrieval: dict) -> str:
    """把检索到的「可引用条文」拼进 Prompt。"""
    rag_context = retrieval.get("combined") or ""
    if not rag_context:
        return base_prompt

    return f"""{base_prompt}

{rag_context}

【重要要求】
请严格依据上述可引用条文生成文书：
1. 只能引用上面列出的条文，未列出的条号不要编造；
2. 引用时优先使用 [S1] 这样的来源编号，也可以写《法名》第X条；
3. 法条内容不确定时标注【待核实】，不要用肯定语气虚构法律依据；
4. 不要声称文书已经过法律审查。"""


def _empty_retrieval() -> dict:
    return {
        "legal_refs": [],
        "template_refs": [],
        "citations": [],
        "combined": "",
        "retrieval": {"config": "fallback", "count": 0},
    }


def build_prompt_with_rag_detailed(doc_type: str, fields: dict) -> tuple[str, dict]:
    """同步版 RAG Prompt 构建，返回 (prompt, 检索结果)。

    保留同步接口是为了兼容现有测试；应用主链路使用 async 版本，
    把 embedding/Chroma 的阻塞调用放到线程池里。
    """
    base_prompt = build_prompt(doc_type, fields)
    if RAG_AVAILABLE:
        try:
            retrieval = rag_retrieve(doc_type, fields)
            return _augment_with_rag(base_prompt, retrieval), retrieval
        except Exception as e:
            print(f"⚠️ RAG 检索失败，使用基础 Prompt: {e}")
    return base_prompt, _empty_retrieval()


def build_prompt_with_rag(doc_type: str, fields: dict) -> str:
    """兼容旧接口：只返回 Prompt 字符串。"""
    return build_prompt_with_rag_detailed(doc_type, fields)[0]


async def build_prompt_with_rag_async(doc_type: str, fields: dict) -> tuple[str, dict]:
    """异步版：检索放到线程池，可选 LLM 重排。"""
    base_prompt = build_prompt(doc_type, fields)
    if RAG_AVAILABLE:
        try:
            retrieval = await rag_aretrieve(doc_type, fields)
            return _augment_with_rag(base_prompt, retrieval), retrieval
        except Exception as e:
            print(f"⚠️ RAG 检索失败，使用基础 Prompt: {e}")
    return base_prompt, _empty_retrieval()


def build_citation_payload(retrieval: dict, content: str) -> dict:
    """构造响应里的引用列表与「引用落地」校验结果。"""
    payload = {"citations": retrieval.get("citations") or []}
    if not RAG_AVAILABLE:
        payload["citation_check"] = {"valid": [], "invalid": [], "checked": 0}
        return payload

    try:
        from rag.indexer import get_retrieval_index

        chunks = get_retrieval_index().chunks
        payload["citation_check"] = verify_citations(content, chunks)
    except Exception as e:
        print(f"⚠️ 引用校验失败: {e}")
        payload["citation_check"] = {
            "valid": [], "invalid": [], "checked": 0, "error": "引用校验不可用",
        }
    return payload


async def generate_with_ai_detailed(doc_type: str, fields: dict) -> tuple[str, dict]:
    """生成文书并返回 (内容, 检索结果)。"""
    prompt, retrieval = await build_prompt_with_rag_async(doc_type, fields)
    content = await call_ollama(prompt)
    return content, retrieval


async def generate_with_ai(doc_type: str, fields: dict) -> str:
    """兼容旧接口：只返回生成内容。"""
    content, _ = await generate_with_ai_detailed(doc_type, fields)
    return content


async def generate_with_ai_stream(doc_type: str, fields: dict):
    """
    流式生成文书，yield 每个文本片段。

    端点需要在流结束后拿到检索结果，因此主链路会直接调用
    build_prompt_with_rag_async + stream_ollama；这个函数保留给
    旧调用方/测试使用。
    """
    prompt, _ = await build_prompt_with_rag_async(doc_type, fields)
    async for chunk in stream_ollama(prompt):
        yield chunk

# ==================== API 路由 ====================

@app.get("/api/doc-types")
async def get_doc_types():
    """获取所有支持的文书类型"""
    templates = get_all_templates()
    if not templates:
        return [
            {"id": 1, "name": "劳动合同", "category": "劳动"},
            {"id": 2, "name": "民事起诉状", "category": "诉讼"},
            {"id": 3, "name": "借条", "category": "借贷"},
            {"id": 4, "name": "租赁合同", "category": "租赁"},
            {"id": 5, "name": "离婚协议书", "category": "婚姻"},
            {"id": 6, "name": "股权转让协议", "category": "商业"},
            {"id": 7, "name": "保密协议", "category": "商业"},
            {"id": 8, "name": "房屋买卖合同", "category": "房地产"},
            {"id": 9, "name": "委托代理书", "category": "委托"}
        ]
    return templates

@app.post("/api/register")
async def register(user_data: UserRegister):
    """用户注册"""
    from database import get_user_by_username, create_user

    # 检查用户名是否已存在
    existing_user = get_user_by_username(user_data.username)
    if existing_user:
        return {"success": False, "message": "用户名已存在"}

    # 创建用户
    hashed_password = get_password_hash(user_data.password)
    user_id = create_user(user_data.username, hashed_password, user_data.nickname)

    if not user_id:
        return {"success": False, "message": "注册失败"}

    # 生成 token
    access_token = create_access_token(data={"user_id": user_id})

    return {
        "success": True,
        "token": access_token,
        "user": {
            "id": user_id,
            "username": user_data.username,
            "nickname": user_data.nickname or user_data.username
        },
        "message": "注册成功"
    }

@app.post("/api/login")
async def login(login_data: UserLogin):
    """用户登录"""
    from database import get_user_by_username

    user = get_user_by_username(login_data.username)
    if not user:
        return {"success": False, "message": "用户名或密码错误"}

    if not verify_password(login_data.password, user["password"]):
        return {"success": False, "message": "用户名或密码错误"}

    # 生成 token
    access_token = create_access_token(data={"user_id": user["id"]})

    return {
        "success": True,
        "token": access_token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "nickname": user.get("nickname") or user["username"]
        },
        "message": "登录成功"
    }

@app.get("/api/user")
async def get_user_info(current_user: dict = Depends(get_current_user)):
    """获取当前用户信息"""
    return {
        "success": True,
        "user": {
            "id": current_user["id"],
            "username": current_user["username"],
            "nickname": current_user.get("nickname") or current_user["username"]
        }
    }

@app.post("/api/generate")
async def generate_document(
    request: GenerateRequest,
    current_user: Optional[dict] = Depends(get_optional_user),
):
    """生成文书（使用大语言模型）。游客可以试用，但只有登录用户的记录会被保存。"""
    try:
        has_values = any(v for v in request.fields.values() if v)

        if not has_values:
            return {"success": False, "content": "", "message": "请填写至少一个字段"}

        content, retrieval = await generate_with_ai_detailed(request.doc_type, request.fields)

        # 只有登录用户才落库。没有归属的记录一旦写进表里，要么对所有人可见
        # （这就是之前的越权漏洞），要么变成无人认领的垃圾数据，两种都不合适。
        record_id = None
        if current_user is not None:
            from database import save_generated_record_with_user
            # 同步 DB 调用放到线程池，避免阻塞事件循环
            record_id = await asyncio.to_thread(
                save_generated_record_with_user,
                request.doc_type, request.fields, content, current_user["id"],
            )

        return {
            "success": True,
            "content": content,
            "record_id": record_id,
            **build_citation_payload(retrieval, content),
        }
    except Exception as e:
        # 详细异常只写服务端日志，不把内部地址/堆栈返回给客户端。
        print(f"⚠️ 文书生成失败: {e}")
        return {"success": False, "content": "", "message": "生成失败，请稍后重试"}

@app.post("/api/generate/stream")
async def generate_document_stream(
    request: GenerateRequest,
    current_user: Optional[dict] = Depends(get_optional_user),
):
    """
    流式生成文书（SSE 协议）

    事件格式（每行以 data: 开头，事件之间用空行分隔）：
      data: {"type":"content","text":"甲"}    ← 生成的文本片段
      data: {"type":"done"}                       ← 生成完成
      data: {"type":"error","message":"..."}   ← 出错

    前端使用 fetch + ReadableStream 逐行解析，实现逐字显示效果。
    """
    # 参数校验
    has_values = any(v for v in request.fields.values() if v)
    if not has_values:
        async def error_event():
            yield f"data: {json.dumps({'type': 'error', 'message': '请填写至少一个字段'}, ensure_ascii=False)}\n\n"
        return StreamingResponse(error_event(), media_type="text/event-stream")

    async def event_generator():
        """SSE 事件生成器：流式推送文本片段，结束后保存到数据库"""
        full_content = ""
        try:
            # 先完成检索（此处可能触发 LLM 重排），再开始流式生成
            prompt, retrieval = await build_prompt_with_rag_async(
                request.doc_type, request.fields
            )

            # 流式接收每个 token，拼接完整内容并推送
            async for chunk in stream_ollama(prompt):
                full_content += chunk
                yield f"data: {json.dumps({'type': 'content', 'text': chunk}, ensure_ascii=False)}\n\n"

            # 生成完成后保存到数据库（仅登录用户，游客不落库）
            record_id = None
            if current_user is not None:
                from database import save_generated_record_with_user
                record_id = await asyncio.to_thread(
                    save_generated_record_with_user,
                    request.doc_type, request.fields, full_content, current_user["id"],
                )

            # 推送完成事件。带上 record_id 与引用，前端据此展示「参考法条」。
            done_event = {
                "type": "done",
                "record_id": record_id,
                **build_citation_payload(retrieval, full_content),
            }
            yield f"data: {json.dumps(done_event, ensure_ascii=False)}\n\n"
        except Exception as e:
            # 详细异常只写服务端日志，不把内部地址/堆栈返回给客户端。
            print(f"⚠️ 流式文书生成失败: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': '生成失败，请稍后重试'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # 禁用 nginx 缓冲，确保实时推送
        }
    )

@app.get("/api/history")
async def get_history(limit: int = 50, current_user: dict = Depends(get_current_user)):
    """
    获取**当前用户**的历史记录。必须登录。

    修复前：未登录时会调用 get_all_history()，把全库所有人的记录都返回出去。
    更麻烦的是这个行为还被写进了测试、标注成「✅ 未登录用户获取所有历史」，
    等于把漏洞固化成了「需求」。
    """
    # 把 limit 夹到合理区间，而不是报 422。这样客户端无论传 0、-1 还是 100000，
    # 都不会造成全表扫描或超大响应；对调用方也更友好。
    limit = max(1, min(limit, 200))

    from database import get_history_by_user
    records = get_history_by_user(current_user["id"], limit)

    return {"success": True, "records": records}

@app.delete("/api/history/{record_id}")
async def delete_history_record(record_id: int, current_user: dict = Depends(get_current_user)):
    """删除历史记录。必须登录，且只能删除属于自己的记录。"""
    from database import delete_record_by_user

    # 关键：这条 SQL 带 user_id 条件（WHERE id = %s AND user_id = %s）。
    # 去删别人的记录时命中行数为 0，返回值和「记录不存在」完全一样 ——
    # 顺带避免了通过响应差异探测「某条记录是否存在」。
    success = delete_record_by_user(record_id, current_user["id"])

    if success:
        return {"success": True, "message": "删除成功"}
    else:
        return {"success": False, "message": "记录不存在或无权删除"}

@app.put("/api/history/{record_id}")
async def update_history_record(
    record_id: int,
    payload: UpdateRecordRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    保存对某条历史文书的在线编辑。

    和删除一样，UPDATE 语句带 `AND user_id = %s`：改别人的记录命中 0 行，
    返回值与「记录不存在」一致，不泄露记录归属。
    """
    from database import update_record_content_by_user

    success = update_record_content_by_user(record_id, current_user["id"], payload.content)

    if success:
        return {"success": True, "message": "保存成功"}
    return {"success": False, "message": "记录不存在或无权修改"}

@app.get("/health")
async def health_check():
    rag_status = "disabled"
    if RAG_AVAILABLE:
        stats = get_knowledge_base_stats()
        rag_status = stats.get("status", "unknown")
    return {
        "status": "ok",
        "message": "服务运行正常",
        "rag": rag_status,
        "llm": {
            "provider": os.getenv("LLM_PROVIDER", "ollama"),
            "model": get_model_name(),
        },
    }

# ==================== 知识库管理 API ====================

@app.get("/api/knowledge/stats")
async def knowledge_stats():
    """查看知识库统计信息（需要多少块，存储在哪里）"""
    if not RAG_AVAILABLE:
        return {"success": False, "message": "RAG 模块未安装，请安装 chromadb 和 langchain-text-splitters"}
    stats = get_knowledge_base_stats()
    return {"success": True, **stats}

@app.post("/api/knowledge/reload")
async def reload_knowledge(current_user: dict = Depends(get_current_user)):
    """
    强制重建知识库（添加新法律条文后使用）。

    这个接口会把知识库里的每个文本块重新向量化一遍，开销很大。修复前它完全不
    鉴权，任何人（包括匿名请求）都能反复触发，是个现成的 DoS 入口。
    现在要求登录；如果配置了 ADMIN_USERNAMES，则进一步限制为管理员。
    """
    if ADMIN_USERNAMES and current_user["username"] not in ADMIN_USERNAMES:
        raise HTTPException(status_code=403, detail="仅管理员可以重建知识库")

    if not RAG_AVAILABLE:
        return {"success": False, "message": "RAG 模块未安装"}

    success, count = build_knowledge_base(force_rebuild=True)
    if success:
        # 清空进程内缓存的索引，下一次检索会重新加载
        from rag.indexer import reset_retrieval_index
        reset_retrieval_index()
    return {"success": success, "total_chunks": count, "message": f"知识库已重建，共 {count} 个文本块"}

# ==================== 启动事件 ====================

@app.on_event("startup")
async def startup_event():
    """服务启动时自动初始化知识库"""
    if RAG_AVAILABLE:
        print("\n🔧 正在初始化 RAG 知识库...")
        try:
            success, count = build_knowledge_base(force_rebuild=False)
            if success:
                print(f"✅ RAG 知识库就绪，共 {count} 个文本块\n")
            else:
                print("⚠️ RAG 知识库构建失败，将使用基础生成模式\n")
        except Exception as e:
            print(f"⚠️ RAG 初始化异常: {e}\n")
    else:
        print("\nℹ️ RAG 模块未启用（缺少依赖），使用基础生成模式\n")

if __name__ == "__main__":
    import uvicorn
    # 本地直接运行只监听回环地址，避免误把无 Nginx 的后端暴露到局域网/公网。
    # Docker 部署由 Dockerfile 的 uvicorn 命令显式监听 0.0.0.0（端口仅在容器内）。
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
