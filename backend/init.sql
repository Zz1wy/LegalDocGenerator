-- ===== 法律文书系统 数据库初始化脚本 =====
-- MySQL 容器首次启动时会自动执行此文件

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
    generated_content TEXT,
    user_id INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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
