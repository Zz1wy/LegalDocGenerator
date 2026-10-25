import os
import threading
import mysql.connector
from mysql.connector import pooling
from dotenv import load_dotenv

load_dotenv()

# 连接池延迟初始化：模块导入时不应要求数据库已经就绪。
# 测试会在导入后把 db_pool 替换为 MagicMock；生产环境在第一次取连接时创建。
db_pool = None
_pool_lock = threading.Lock()


def _init_db_pool():
    """线程安全地创建连接池（仅首次调用真正连接数据库）"""
    global db_pool
    if db_pool is not None:
        return db_pool
    with _pool_lock:
        if db_pool is None:
            db_pool = pooling.MySQLConnectionPool(
                pool_name="legal_pool",
                pool_size=5,
                host=os.getenv("DB_HOST", "localhost"),
                port=int(os.getenv("DB_PORT", 3306)),
                user=os.getenv("DB_USER", "legal_app"),
                password=os.getenv("DB_PASSWORD", ""),
                database=os.getenv("DB_NAME", "legal_doc_db"),
            )
    return db_pool


def get_db_connection():
    """获取数据库连接"""
    try:
        conn = _init_db_pool().get_connection()
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
    """
    保存生成记录（带用户ID）

    返回新记录的 id（成功）或 None（失败）。前端需要这个 id 才能对刚生成的
    文书做「在线编辑 → 保存」，所以这里不再只返回 True/False。
    """
    conn = get_db_connection()
    if not conn:
        return None

    try:
        cursor = conn.cursor()
        sql = """
            INSERT INTO generated_records (doc_type, input_fields, generated_content, user_id)
            VALUES (%s, %s, %s, %s)
        """
        import json
        cursor.execute(sql, (doc_type, json.dumps(input_fields), generated_content, user_id))
        conn.commit()
        record_id = cursor.lastrowid
        cursor.close()
        conn.close()
        return record_id
    except mysql.connector.Error as e:
        print(f"保存记录失败: {e}")
        return None


def update_record_content_by_user(record_id: int, user_id: int, generated_content: str) -> bool:
    """
    更新指定用户的文书内容（在线编辑后保存）。

    和删除一样，WHERE 同时带 id 和 user_id：改别人的记录会命中 0 行，
    返回值和「记录不存在」一致，不泄露记录是否存在。
    """
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cursor = conn.cursor()
        sql = """
            UPDATE generated_records
            SET generated_content = %s
            WHERE id = %s AND user_id = %s
        """
        cursor.execute(sql, (generated_content, record_id, user_id))
        conn.commit()
        affected = cursor.rowcount
        cursor.close()
        conn.close()
        return affected > 0
    except mysql.connector.Error as e:
        print(f"更新记录失败: {e}")
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

# 说明：原本这里还有一个 get_all_history()，直接「SELECT ... FROM generated_records
# ORDER BY created_at DESC」不带任何用户条件，供未登录用户调用。它会返回全库所有
# 用户的法律文书，属于越权访问，因此连同 delete_record() 一并删除。
# 历史记录一律通过 get_history_by_user(user_id, ...) 按归属读取。