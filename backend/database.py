import os
import mysql.connector
from mysql.connector import pooling
from dotenv import load_dotenv

load_dotenv()

# 数据库连接池配置
db_pool = pooling.MySQLConnectionPool(
    pool_name="legal_pool",
    pool_size=5,
    host=os.getenv("DB_HOST", "localhost"),
    port=int(os.getenv("DB_PORT", 3306)),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "legal_doc_db")
)

def get_db_connection():
    """获取数据库连接"""
    try:
        conn = db_pool.get_connection()
        return conn
    except mysql.connector.Error as e:
        print(f"数据库连接失败: {e}")
        return None

def get_all_templates():
    """获取所有文书模板"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, name, category, fields FROM document_templates")
        result = cursor.fetchall()
        cursor.close()
        conn.close()
        return result
    except mysql.connector.Error as e:
        print(f"查询模板失败: {e}")
        return []

def save_generated_record(doc_type: str, input_fields: dict, generated_content: str):
    """保存生成记录"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cursor = conn.cursor()
        sql = """
            INSERT INTO generated_records (doc_type, input_fields, generated_content) 
            VALUES (%s, %s, %s)
        """
        import json
        cursor.execute(sql, (doc_type, json.dumps(input_fields), generated_content))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except mysql.connector.Error as e:
        print(f"保存记录失败: {e}")
        return False

def delete_record(record_id: int):
    """删除指定记录"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM generated_records WHERE id = %s", (record_id,))
        conn.commit()
        affected = cursor.rowcount
        cursor.close()
        conn.close()
        return affected > 0
    except mysql.connector.Error as e:
        print(f"删除记录失败: {e}")
        return False

def test_connection():
    """测试数据库连接"""
    conn = get_db_connection()
    if conn:
        print("✅ 数据库连接成功！")
        conn.close()
        return True
    else:
        print("❌ 数据库连接失败！")
        return False

if __name__ == "__main__":
    test_connection()
    print("\n文书模板列表：")
    templates = get_all_templates()
    for t in templates:
        print(f"  - {t['name']} ({t['category']})")