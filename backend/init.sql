-- ===== 法律文书系统 数据库初始化脚本 =====
-- MySQL 容器首次启动时会自动执行此文件。
--
-- 应用账号不在本脚本中创建（避免把密码写进 SQL）：
--   Docker: 由 MySQL 官方镜像根据 MYSQL_USER / MYSQL_PASSWORD 创建并授权。
--   本地:   请手动创建 legal_app 账号，仅授予本数据库权限，不要用 root 运行后端。
--
-- 注意：已有 mysql_data 数据卷时，入口脚本不会再次创建用户；需要手动迁移或重建卷。

-- 显式声明脚本编码。MySQL 官方镜像导入 init.sql 时，客户端默认字符集
-- 不一定是 utf8mb4；不写这一行，下面的中文模板名会被按 latin1 解释，
-- 再以 utf8mb4 存储，形成「劳动合同 → åŠ³åŠ¨åˆåŒ」的乱码。
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    nickname VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS generated_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    doc_type VARCHAR(50) NOT NULL,
    input_fields JSON,
    -- API 允许 200k 字符；TEXT 只有 64KB，长文书会保存失败
    generated_content MEDIUMTEXT,
    user_id INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    -- 历史记录固定按 user_id 过滤 + created_at 倒序
    INDEX idx_user_created (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 已有数据库升级（新建库无需执行）：
-- ALTER TABLE generated_records
--   MODIFY generated_content MEDIUMTEXT,
--   ADD INDEX idx_user_created (user_id, created_at);

-- 文书模板表（用于 /api/doc-types 动态读取）
CREATE TABLE IF NOT EXISTS document_templates (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    category VARCHAR(50),
    fields JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 插入默认模板数据
INSERT INTO document_templates (name, category) VALUES
    ('劳动合同', '劳动'),
    ('民事起诉状', '诉讼'),
    ('借条', '借贷'),
    ('租赁合同', '租赁'),
    ('离婚协议书', '婚姻'),
    ('股权转让协议', '商业'),
    ('保密协议', '商业'),
    ('房屋买卖合同', '房地产'),
    ('委托代理书', '委托')
ON DUPLICATE KEY UPDATE name=VALUES(name);
