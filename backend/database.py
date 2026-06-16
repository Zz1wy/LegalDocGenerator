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

def delete_record_by_user(record_id: int, user_id: int):
    """删除指定用户的记录（仅允许删除自己的）"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cursor = conn.cursor()
        sql = "DELETE FROM generated_records WHERE id = %s AND user_id = %s"
        cursor.execute(sql, (record_id, user_id))
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

# 用户相关函数

def create_user(username: str, password_hash: str, nickname: str = None):
    """创建用户"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cursor = conn.cursor()
        sql = "INSERT INTO users (username, password, nickname) VALUES (%s, %s, %s)"
        cursor.execute(sql, (username, password_hash, nickname))
        conn.commit()
        user_id = cursor.lastrowid
        cursor.close()
        conn.close()
        return user_id
    except mysql.connector.Error as e:
        print(f"创建用户失败: {e}")
        return None

def get_user_by_username(username: str):
    """根据用户名获取用户"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        return user
    except mysql.connector.Error as e:
        print(f"查询用户失败: {e}")
        return None

def get_user_by_id(user_id: int):
    """根据ID获取用户"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, username, nickname, created_at FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        return user
    except mysql.connector.Error as e:
        print(f"查询用户失败: {e}")
        return None

def save_generated_record_with_user(doc_type: str, input_fields: dict, generated_content: str, user_id: int = None):
    """保存生成记录（带用户ID）"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cursor = conn.cursor()
        sql = """
            INSERT INTO generated_records (doc_type, input_fields, generated_content, user_id) 
            VALUES (%s, %s, %s, %s)
        """
        import json
        cursor.execute(sql, (doc_type, json.dumps(input_fields), generated_content, user_id))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except mysql.connector.Error as e:
        print(f"保存记录失败: {e}")
        return False

def get_history_by_user(user_id: int, limit: int = 50):
    """获取指定用户的历史记录"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, doc_type, input_fields, generated_content, created_at 
            FROM generated_records 
            WHERE user_id = %s
            ORDER BY created_at DESC 
            LIMIT %s
        """, (user_id, limit))
        records = cursor.fetchall()
        cursor.close()
        conn.close()
        
        for record in records:
            if record['created_at']:
                record['created_at'] = record['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        
        return records
    except mysql.connector.Error as e:
        print(f"查询历史记录失败: {e}")
        return []

def get_all_history(limit: int = 50):
    """获取所有历史记录（用于未登录用户）"""
    conn = get_db_connection()
    if not conn:
        return []
    
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
        
        return records
    except mysql.connector.Error as e:
        print(f"查询历史记录失败: {e}")
        return []