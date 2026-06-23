"""
测试配置与共享夹具

运行方式:
    cd backend
    pytest tests/ -v                          # 全部测试
    pytest tests/ -v -k "test_register"       # 按关键字筛选
    pytest tests/ -v --tb=short               # 简化错误输出
    pytest tests/ -v --no-header -q           # 最简输出

前置条件:
    pip install pytest pytest-asyncio pytest-cov httpx
"""

import sys
import os
import json
import pytest
from unittest.mock import patch, MagicMock

# 确保 backend 目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ==================== 环境变量 ====================
os.environ["SECRET_KEY"] = "test-secret-key-for-pytest-only"
os.environ["OLLAMA_HOST"] = "http://localhost:11434"

# ==================== Mock 数据库（在 import main 之前） ====================
_mock_conn = MagicMock()
_mock_cursor = MagicMock()
_mock_cursor.fetchall.return_value = []
_mock_cursor.fetchone.return_value = None
_mock_cursor.lastrowid = 1
_mock_conn.cursor.return_value = _mock_cursor

# Mock database.py 模块级连接池
patch("database.db_pool", MagicMock()).start()
patch("database.get_db_connection", return_value=_mock_conn).start()
patch("database.get_all_templates", return_value=[]).start()
patch("database.save_generated_record", return_value=True).start()
patch("database.save_generated_record_with_user", return_value=True).start()
patch("database.create_user", return_value=1).start()
patch("database.get_user_by_username", return_value=None).start()
patch("database.get_user_by_id", return_value={
    "id": 1, "username": "testuser", "nickname": "测试用户"
}).start()
patch("database.get_history_by_user", return_value=[]).start()
patch("database.get_all_history", return_value=[]).start()
patch("database.delete_record_by_user", return_value=True).start()
patch("database.delete_record", return_value=True).start()

# Mock Ollama
_mock_ollama_response = MagicMock()
_mock_ollama_response.status_code = 200
_mock_ollama_response.json.return_value = {
    "response": "【Mock 生成的测试文书内容】\n\n根据相关法律法规，特此出具本法律文书..."
}
# 需要在 import main 之后才能 patch，但先准备好 callable
def _mock_call_ollama(prompt: str) -> str:
    return "【Mock 生成的测试文书】\n\n甲方：___\n乙方：___\n合同期限：___\n工资待遇：___\n\n本合同依据《中华人民共和国劳动法》订立。\n双方签字：___\n日期：___"


# ==================== 现在安全导入 main ====================
# 在 mock 激活之后导入，避免真实数据库连接
patch("main.call_ollama", side_effect=_mock_call_ollama).start()

# Mock RAG 模块以避免 Ollama 依赖
_mock_rag_available = patch("main.RAG_AVAILABLE", False).start()
_mock_build_kb = patch("main.build_knowledge_base", return_value=(True, 0)).start()
_mock_get_stats = patch("main.get_knowledge_base_stats", return_value={"status": "mock"}).start()

from main import app, create_access_token, SECRET_KEY, ALGORITHM
from fastapi.testclient import TestClient
import database as db_module


# ==================== 自动重置 Mock 状态 ====================

@pytest.fixture(autouse=True)
def _reset_mocks():
    """每个测试前重置所有 mock 的返回值，防止测试间状态污染"""
    db_module.get_db_connection.return_value = _mock_conn
    db_module.get_all_templates.return_value = []
    db_module.save_generated_record.return_value = True
    db_module.save_generated_record_with_user.return_value = True
    db_module.create_user.return_value = 1
    db_module.get_user_by_username.return_value = None
    db_module.get_user_by_id.return_value = {"id": 1, "username": "testuser", "nickname": "测试用户"}
    db_module.get_history_by_user.return_value = []
    db_module.get_all_history.return_value = []
    db_module.delete_record_by_user.return_value = True
    db_module.delete_record.return_value = True
    yield


# ==================== 夹具 ====================

@pytest.fixture
def mock_database():
    """提供对已 mock 的数据库函数的引用，测试中可修改返回值"""
    return {
        "templates": db_module.get_all_templates,
        "user_by_name": db_module.get_user_by_username,
        "create_user": db_module.create_user,
        "user_by_id": db_module.get_user_by_id,
        "save_record": db_module.save_generated_record_with_user,
        "history": db_module.get_history_by_user,
        "all_history": db_module.get_all_history,
        "delete_user": db_module.delete_record_by_user,
        "delete": db_module.delete_record,
    }

@pytest.fixture
def client():
    """FastAPI TestClient - 无需启动服务器即可测试"""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """JWT 认证头"""
    token = create_access_token(data={"user_id": 1})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sample_user():
    """标准测试用户"""
    return {
        "username": "testuser",
        "password": "TestPass123!",
        "nickname": "测试用户"
    }


@pytest.fixture
def sample_fields_all_types():
    """全部 9 种文书类型的测试字段"""
    return {
        "劳动合同": {
            "party_a": "字节跳动科技有限公司", "party_b": "张三",
            "term": "三年", "salary": "30000元/月"
        },
        "民事起诉状": {
            "plaintiff": "李四", "defendant": "王五",
            "claim": "请求判令被告归还借款本金10万元及利息",
            "reason": "被告于2024年1月借款10万元，约定一年后归还，现已逾期未还"
        },
        "借条": {
            "lender": "赵六", "borrower": "钱七",
            "amount": "人民币拾万元整（¥100,000）", "repay_date": "2025年12月31日"
        },
        "租赁合同": {
            "landlord": "孙八", "tenant": "周九",
            "property": "北京市朝阳区某某小区1号楼1单元101室",
            "rent": "8000元/月", "term": "一年"
        },
        "离婚协议书": {
            "husband": "吴十", "wife": "郑十一",
            "child_custody": "女儿由女方抚养，男方每月可探视两次",
            "property_split": "房产归男方，存款归女方，车辆一人一辆"
        },
        "股权转让协议": {
            "transferor": "冯十二", "transferee": "陈十三",
            "company_name": "某某科技有限公司",
            "share_ratio": "30%", "share_price": "人民币壹佰万元整"
        },
        "保密协议": {
            "employer": "某某科技有限公司", "employee": "褚十四",
            "confidential_scope": "技术方案、源代码、客户名单、财务数据",
            "confidential_period": "合同终止后三年"
        },
        "房屋买卖合同": {
            "seller": "卫十五", "buyer": "蒋十六",
            "house_address": "上海市浦东新区某某路100号",
            "house_area": "120平方米", "house_price": "人民币伍佰万元整"
        },
        "委托代理书": {
            "client": "沈十七", "agent": "韩十八",
            "matter": "代为办理房屋过户手续",
            "authority": "特别授权（含代为签署相关文件、代收款项等）",
            "period": "自签署之日起一年内有效"
        }
    }
