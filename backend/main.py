from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import os
import json
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

def generate_document_content(doc_type: str, fields: dict) -> str:
    templates = {
        "劳动合同": f"""
┌─────────────────────────────────────────────┐
              劳动合同书
├─────────────────────────────────────────────┤
甲方（用人单位）：{fields.get('party_a', '')}
乙方（劳动者）：{fields.get('party_b', '')}

根据《中华人民共和国劳动法》及相关法律法规，甲乙双方经平等协商一致，签订本合同。

一、合同期限
{fields.get('term', '')}

二、工资待遇
每月工资：{fields.get('salary', '')}元

三、双方权利义务
1. 甲方应按时支付劳动报酬
2. 乙方应遵守公司规章制度

甲方签字：__________    乙方签字：__________
日期：__________        日期：__________
└─────────────────────────────────────────────┘
        """,
        
        "民事起诉状": f"""
┌─────────────────────────────────────────────┐
              民事起诉状
├─────────────────────────────────────────────┤
原告：{fields.get('plaintiff', '')}
被告：{fields.get('defendant', '')}

诉讼请求：
{fields.get('claim', '')}

事实与理由：
{fields.get('reason', '')}

此致
XX人民法院

起诉人（签名）：__________
日期：__________
└─────────────────────────────────────────────┘
        """,
        
        "借条": f"""
┌─────────────────────────────────────────────┐
                    借条
├─────────────────────────────────────────────┤
今本人（借款人）{fields.get('borrower', '')}向（出借人）{fields.get('lender', '')}
借到人民币{fields.get('amount', '')}元（大写：__________）。

还款日期：{fields.get('repay_date', '')}

借款人签字（按手印）：__________
日期：__________
└─────────────────────────────────────────────┘
        """,
        
        "租赁合同": f"""
┌─────────────────────────────────────────────┐
              租赁合同
├─────────────────────────────────────────────┤
出租方（甲方）：{fields.get('landlord', '')}
承租方（乙方）：{fields.get('tenant', '')}

租赁物：{fields.get('property', '')}
租期：{fields.get('term', '')}
租金：{fields.get('rent', '')}元/月

甲方签字：__________    乙方签字：__________
日期：__________        日期：__________
└─────────────────────────────────────────────┘
        """
    }
    return templates.get(doc_type, f"暂不支持生成{doc_type}")

@app.get("/api/doc-types")
async def get_doc_types():
    """获取文书类型列表（从数据库读取）"""
    templates = get_all_templates()
    if not templates:
        # 如果数据库没有，返回默认列表
        return [
            {"id": 1, "name": "劳动合同", "category": "劳动"},
            {"id": 2, "name": "民事起诉状", "category": "诉讼"},
            {"id": 3, "name": "借条", "category": "借贷"},
            {"id": 4, "name": "租赁合同", "category": "租赁"}
        ]
    return templates

@app.post("/api/generate")
async def generate_document(request: GenerateRequest):
    """生成文书"""
    try:
        content = generate_document_content(request.doc_type, request.fields)
        
        # 保存到数据库
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
        
        # 转换日期为字符串
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