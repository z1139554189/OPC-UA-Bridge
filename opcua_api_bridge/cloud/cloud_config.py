"""
云端推送配置 - 密码从 .env 文件读取，不硬编码
"""

import os
from pathlib import Path

# 读取 .env 文件
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

# 腾讯云 TDSQL-C MySQL 连接信息
CLOUD_MYSQL = {
    "host": "sh-cynosdbmysql-grp-4f512ckw.sql.tencentcdb.com",
    "port": 21397,
    "database": "opcua_db",
    "user": "opcua_user",
    "password": os.environ.get("CLOUD_MYSQL_PASSWORD", ""),
}

# 推送参数
CLOUD_PUSH_CONFIG = {
    # 推送间隔（秒），与本地轮询间隔对齐
    "push_interval": 60.0,
    
    # 批量大小（条数），buffer 满多少条触发推送
    "batch_size": 500,
    
    # 重试次数
    "retry_times": 3,
    
    # 重试间隔（秒）
    "retry_delay": 5.0,
}
