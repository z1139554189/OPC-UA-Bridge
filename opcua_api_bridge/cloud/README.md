# OPC UA 云端部署指南

## 架构概览

```
本地工控网                              腾讯云
┌──────────────────┐                 ┌─────────────────────────┐
│  OPC UA 服务器    │                 │                         │
│  172.30.0.254    │                 │  TDSQL-C MySQL           │
│       ↓          │    定时推送      │  172.17.0.2:3306        │
│  本地桥接器       │ ────HTTP────→  │       ↕                   │
│  localhost:8000  │                 │  SCF 云函数              │
│       ↓          │                 │  (只读查询API)            │
│  本地SQLite      │                 │       ↕                   │
│  (完整历史库)     │                 │  API 网关               │
│                  │                 │  (HTTPS公网入口)          │
└──────────────────┘                 └─────────────────────────┘
                                              ↓
                                     浏览器/手机直接访问
```

## 第一步：初始化 MySQL 数据库

在 TDSQL-C 控制台的「数据库管理」页面，找到 `opcua_db`，点击「登录」进入数据库管理工具（或使用 Navicat/DBeaver 等客户端连接），执行以下 SQL：

```sql
-- 方式一：直接复制 init_db.sql 的内容执行
-- 方式二：在控制台的 SQL 窗口中粘贴以下内容

-- 节点历史数据表
CREATE TABLE IF NOT EXISTS node_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    node_id VARCHAR(255) NOT NULL,
    timestamp DATETIME(3) NOT NULL,
    value DOUBLE,
    quality VARCHAR(32) NOT NULL DEFAULT 'Good',
    source_timestamp DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_node_ts (node_id, timestamp),
    INDEX idx_timestamp (timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 节点实时快照表
CREATE TABLE IF NOT EXISTS node_latest (
    node_id VARCHAR(255) PRIMARY KEY,
    value DOUBLE,
    quality VARCHAR(32) NOT NULL DEFAULT 'Good',
    timestamp DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    INDEX idx_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据推送日志表
CREATE TABLE IF NOT EXISTS push_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    push_time DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    node_count INT NOT NULL DEFAULT 0,
    record_count INT NOT NULL DEFAULT 0,
    status VARCHAR(16) NOT NULL DEFAULT 'success',
    error_msg TEXT NULL,
    duration_ms INT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

## 第二步：配置本地推送

编辑 `cloud/cloud_config.py`，填入你的 MySQL 密码：

```python
CLOUD_MYSQL = {
    "host": "172.17.0.2",
    "port": 3306,
    "database": "opcua_db",
    "user": "opcua_user",
    "password": "你的密码",  # ← 填这里
}
```

安装依赖：

```bash
pip install aiomysql pymysql
```

### 方式 A：独立推送脚本（推荐先用这个测试）

```bash
# 单次推送测试
python cloud/run_push.py

# 持续循环推送（每 60 秒一次）
python cloud/run_push.py --loop

# 自定义间隔
python cloud/run_push.py --loop --interval 30
```

### 方式 B：集成到桥接器

在 `client.py` 的 `_poll_cache_to_history` 方法中加入云端推送调用。

## 第三步：创建 SCF 云函数

1. 打开腾讯云 SCF 控制台：
   **https://console.cloud.tencent.com/scf**

2. 点击「新建」，配置如下：
   - **函数名称**：`opcua-data-api`
   - **地域**：上海（与 MySQL 同地域）
   - **运行环境**：Python 3.10
   - **提交方法**：本地上传文件夹（选 `cloud/scf/` 目录）
   - **执行方法**：`index.main_handler`

3. **高级配置 → VPC**：
   - 启用 VPC 访问
   - VPC：选择 `Default-VPC`（vpc-dh9uf32a）
   - 子网：选择 `Default-Subnet`（subnet-ob3fhqkf）

4. **环境变量**：
   ```
   DB_HOST=172.17.0.2
   DB_PORT=3306
   DB_NAME=opcua_db
   DB_USER=opcua_user
   DB_PASSWORD=你的密码
   ```

5. 点击「完成」创建

## 第四步：配置 API 网关

1. 打开 API 网关控制台：
   **https://console.cloud.tencent.com/apigateway**

2. 创建服务：
   - 服务名称：`opcua-api-service`
   - 协议：HTTPS
   - 前端类型：HTTP & HTTPS

3. 在服务中创建 API：
   - **API 名称**：`opcua-data`
   - **请求方法**：ANY
   - **请求路径**：`/`
   - **后端类型**：云函数 SCF
   - **后端函数**：选择刚创建的 `opcua-data-api`

4. 发布服务：
   - 创建发布环境：`release`
   - 发布后获得访问地址：`https://service-xxxxx-xxx.gz.apigw.tencentcs.com/release/`

5. （可选）绑定自定义域名：
   - 在「自定义域名」tab 添加域名
   - 配置 DNS 解析 CNAME 到 API 网关地址

## 第五步：验证

访问以下地址测试：

```
# 健康检查
GET https://你的API网关地址/health

# 查询所有实时值
GET https://你的API网关地址/api/latest

# 查询指定节点实时值
GET https://你的API网关地址/api/latest?node_id=ns=1;s=FIT_05R201F01.PV

# 查询历史数据（最近1小时）
GET https://你的API网关地址/api/history?node_id=ns=1;s=FIT_05R201F01.PV&hours=1

# 查询历史数据（指定时间范围）
GET https://你的API网关地址/api/history?node_id=ns=1;s=FIT_05R201F01.PV&start_time=2026-04-01 14:00:00&end_time=2026-04-01 15:00:00

# 查看统计
GET https://你的API网关地址/api/stats

# 批量查询实时值
POST https://你的API网关地址/api/batch-latest
Content-Type: application/json
{"node_ids": ["ns=1;s=FIT_05R201F01.PV", "ns=1;s=FIT_05R210F01.PV"]}
```

## 费用估算

| 资源 | 免费额度 | 本项目用量 | 预估费用 |
|------|---------|-----------|---------|
| TDSQL-C Serverless | 每月 100万读+50万写 | 推送每分钟 1 次 ≈ 4万/月 | ~0 元 |
| SCF 云函数 | 每月 100万次调用+40万GBs | 查询几百次/月 | ~0 元 |
| API 网关 | 每月 100万次调用 | 几百次/月 | ~0 元 |
| **总计** | | | **~0 元** |

## 常见问题

### Q: 本地连不上云 MySQL？
A: TDSQL-C 只能在 VPC 内网访问。本地推送需要以下方案之一：
1. 本地安装 VPN 或专线打通到腾讯云 VPC
2. 使用腾讯云「数据库连接管理」开通公网地址（不推荐，安全风险）
3. **推荐方案**：在腾讯云轻量服务器上部署推送服务，轻量服务器可访问 VPC 内网

### Q: SCF 函数报连不上数据库？
A: 确认 SCF 函数的 VPC 配置与 MySQL 在同一个 VPC（Default-VPC）

### Q: API 网关返回 504？
A: SCF 冷启动需要几秒，重试即可。如果持续超时，检查 SCF 日志
