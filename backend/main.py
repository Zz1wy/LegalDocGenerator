from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import os
import json
import httpx
from dotenv import load_dotenv
from database import get_all_templates, save_generated_record
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from datetime import datetime, timedelta
from passlib.context import CryptContext
import hashlib

# RAG 模块导入（带优雅降级：如果没有安装相关依赖，RAG 功能自动禁用）
try:
    from rag.knowledge_base import build_knowledge_base, get_knowledge_base_stats
    from rag.retriever import retrieve_simple
    RAG_AVAILABLE = True
except ImportError as e:
    RAG_AVAILABLE = False
    print(f"⚠️ RAG 模块不可用: {e}")

# ⚠️ 必须先加载 .env，再读取环境变量
load_dotenv()

# JWT 配置
SECRET_KEY = os.getenv("SECRET_KEY", "default-secret-key-for-development-only")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7

# 认证方案
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

# ==================== Pydantic 模型 ====================

class GenerateRequest(BaseModel):
    doc_type: str
    fields: dict

class UserRegister(BaseModel):
    username: str
    password: str
    nickname: Optional[str] = None

class UserLogin(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    success: bool
    token: Optional[str] = None
    user: Optional[dict] = None
    message: Optional[str] = None

class GenerateResponse(BaseModel):
    success: bool
    content: str
    message: Optional[str] = None

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
    except:
        pass

    # 如果 bcrypt 验证失败，尝试旧的 SHA-256 + salt 格式
    try:
        salt, stored_hash = hashed_password.split('$')
        return hashlib.sha256((plain_password + salt).encode()).hexdigest() == stored_hash
    except:
        return False

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """创建 JWT 访问令牌"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """从 JWT 令牌获取当前用户"""
    if not credentials:
        raise HTTPException(status_code=401, detail="未提供认证凭据")
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="无效的认证凭据")
    except JWTError:
        raise HTTPException(status_code=401, detail="无效的认证凭据")

    from database import get_user_by_id
    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user

# ==================== Ollama 配置 ====================

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

async def call_ollama(prompt: str) -> str:
    """调用 Ollama 大语言模型"""
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{OLLAMA_HOST}/api/generate",
                json={
                    "model": "qwen2.5:7b",
                    "prompt": prompt,
                    "stream": False
                }
            )
            if response.status_code == 200:
                return response.json().get("response", "")
            else:
                raise Exception(f"Ollama 返回错误: {response.status_code}")
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

def build_prompt_with_rag(doc_type: str, fields: dict) -> str:
    """
    RAG 增强版 Prompt 构建

    与基础版 build_prompt 的区别：
    1. 先从法律知识库检索相关条文和模板
    2. 将检索结果拼接到 Prompt 中
    3. 指示 LLM 引用真实法律条文

    如果 RAG 不可用或检索失败，自动回退到基础版 build_prompt
    """
    # 先生成基础 Prompt
    base_prompt = build_prompt(doc_type, fields)

    # 尝试 RAG 增强
    if RAG_AVAILABLE:
        try:
            rag_context = retrieve_simple(doc_type, fields)
            if rag_context:
                # 在基础 Prompt 前面插入法律参考，在后面追加引用要求
                enhanced = f"""{base_prompt}

{rag_context}

【重要要求】
请参考上述法律条文内容生成文书，确保：
1. 引用的法律条款准确无误
2. 文书条款符合相关法律法规的规定
3. 在必要时可以在文书中注明所依据的法律条款编号"""
                return enhanced
        except Exception as e:
            print(f"⚠️ RAG 检索失败，使用基础 Prompt: {e}")

    return base_prompt

async def generate_with_ai(doc_type: str, fields: dict) -> str:
    """使用大语言模型生成文书（自动使用 RAG 增强）"""
    prompt = build_prompt_with_rag(doc_type, fields)
    return await call_ollama(prompt)

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
async def generate_document(request: GenerateRequest, credentials: HTTPAuthorizationCredentials = Depends(security)):
    """生成文书（使用大语言模型）"""
    try:
        has_values = any(v for v in request.fields.values() if v)

        if not has_values:
            return {"success": False, "content": "", "message": "请填写至少一个字段"}

        content = await generate_with_ai(request.doc_type, request.fields)

        # 获取当前用户ID（可选认证）
        user_id = None
        if credentials:
            try:
                token = credentials.credentials
                payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                user_id = payload.get("user_id")
            except:
                pass

        # 保存到数据库（带用户ID）
        from database import save_generated_record_with_user
        save_generated_record_with_user(request.doc_type, request.fields, content, user_id)

        return {"success": True, "content": content}
    except Exception as e:
        return {"success": False, "content": "", "message": str(e)}

@app.get("/api/history")
async def get_history(limit: int = 50, credentials: HTTPAuthorizationCredentials = Depends(security)):
    """获取生成历史记录（已登录显示用户记录，未登录显示所有记录）"""
    user_id = None
    if credentials:
        try:
            token = credentials.credentials
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("user_id")
        except:
            pass

    from database import get_history_by_user, get_all_history
    if user_id:
        records = get_history_by_user(user_id, limit)
    else:
        records = get_all_history(limit)

    return {"success": True, "records": records}

@app.delete("/api/history/{record_id}")
async def delete_history_record(record_id: int, credentials: HTTPAuthorizationCredentials = Depends(security)):
    """删除历史记录（只能删除自己的记录）"""
    user_id = None
    if credentials:
        try:
            token = credentials.credentials
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("user_id")
        except:
            pass

    from database import delete_record_by_user, delete_record
    if user_id:
        # 已登录用户，只能删除自己的记录
        success = delete_record_by_user(record_id, user_id)
    else:
        # 未登录用户，只能删除无用户关联的记录
        success = delete_record(record_id)

    if success:
        return {"success": True, "message": "删除成功"}
    else:
        return {"success": False, "message": "记录不存在或无权删除"}

@app.get("/health")
async def health_check():
    rag_status = "disabled"
    if RAG_AVAILABLE:
        stats = get_knowledge_base_stats()
        rag_status = stats.get("status", "unknown")
    return {
        "status": "ok",
        "message": "服务运行正常",
        "rag": rag_status
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
async def reload_knowledge():
    """强制重建知识库（添加新法律条文后使用）"""
    if not RAG_AVAILABLE:
        return {"success": False, "message": "RAG 模块未安装"}
    success, count = build_knowledge_base(force_rebuild=True)
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
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
