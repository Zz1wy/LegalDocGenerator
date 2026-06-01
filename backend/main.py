from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import os
import json
import httpx
from dotenv import load_dotenv
from database import get_all_templates, save_generated_record

load_dotenv()

app = FastAPI(title="智能法律文书辅助生成系统")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class GenerateRequest(BaseModel):
    doc_type: str
    fields: dict

class GenerateResponse(BaseModel):
    success: bool
    content: str
    message: Optional[str] = None

# Ollama 配置
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
请直接输出文书内容，不要解释。"""
    }
    
    return templates.get(doc_type, f"请生成一份{doc_type}，包含以下信息：{field_str}")

async def generate_with_ai(doc_type: str, fields: dict) -> str:
    """使用大语言模型生成文书"""
    prompt = build_prompt(doc_type, fields)
    return await call_ollama(prompt)

@app.get("/api/doc-types")
async def get_doc_types():
    templates = get_all_templates()
    if not templates:
        return [
            {"id": 1, "name": "劳动合同", "category": "劳动"},
            {"id": 2, "name": "民事起诉状", "category": "诉讼"},
            {"id": 3, "name": "借条", "category": "借贷"},
            {"id": 4, "name": "租赁合同", "category": "租赁"}
        ]
    return templates

@app.post("/api/generate")
async def generate_document(request: GenerateRequest):
    """生成文书（使用大语言模型）"""
    try:
        # 检查字段是否有值
        has_values = any(v for v in request.fields.values() if v)
        
        if not has_values:
            return {"success": False, "content": "", "message": "请填写至少一个字段"}
        
        # 调用大语言模型生成
        content = await generate_with_ai(request.doc_type, request.fields)
        
        # 保存到数据库
        save_generated_record(request.doc_type, request.fields, content)
        
        return {"success": True, "content": content}
    except Exception as e:
        return {"success": False, "content": "", "message": str(e)}

@app.get("/api/models")
async def get_available_models():
    """获取可用的模型列表"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{OLLAMA_HOST}/api/tags")
            if response.status_code == 200:
                return {"success": True, "models": response.json().get("models", [])}
            return {"success": False, "models": [], "message": "无法获取模型列表"}
    except Exception as e:
        return {"success": False, "models": [], "message": str(e)}

@app.get("/api/history")
async def get_history(limit: int = 10):
    """获取生成历史记录"""
    from database import get_db_connection
    conn = get_db_connection()
    if not conn:
        return {"success": False, "records": [], "message": "数据库连接失败"}
    
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, doc_type, input_fields, generated_content, created_at 
            FROM generated_records 
            ORDER BY created_at DESC 
            LIMIT %s
        """, (limit,))
        records = cursor.fetchall()
        cursor.close()
        conn.close()
        
        for record in records:
            if record['created_at']:
                record['created_at'] = record['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        
        return {"success": True, "records": records}
    except Exception as e:
        return {"success": False, "records": [], "message": str(e)}

@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "服务运行正常"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)