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
            {"id": 4, "name": "租赁合同", "category": "租赁"},
            {"id": 5, "name": "离婚协议书", "category": "婚姻"},
            {"id": 6, "name": "股权转让协议", "category": "商业"},
            {"id": 7, "name": "保密协议", "category": "商业"},
            {"id": 8, "name": "房屋买卖合同", "category": "房地产"},
            {"id": 9, "name": "委托代理书", "category": "委托"}
        ]
    return templates

@app.post("/api/generate")
async def generate_document(request: GenerateRequest):
    """生成文书（使用大语言模型）"""
    try:
        has_values = any(v for v in request.fields.values() if v)
        
        if not has_values:
            return {"success": False, "content": "", "message": "请填写至少一个字段"}
        
        content = await generate_with_ai(request.doc_type, request.fields)
        
        save_generated_record(request.doc_type, request.fields, content)
        
        return {"success": True, "content": content}
    except Exception as e:
        return {"success": False, "content": "", "message": str(e)}

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