# SCF Web 函数创建 - 进展记录

## ✅ 已完成
- **函数已创建**：`opcua-cloud-api`，地域：上海（rid=4）
- 函数类型：Event 函数（需要改为 Web 函数才有 HTTP 触发）
- 运行时间：Python 3.9

## ⚠️ 待完成配置

### 1. 添加环境变量（重要！）
当前状态：描述字段里有环境变量文本（位置错误），环境变量 section 仍为空

**操作步骤：**
1. 打开 https://console.cloud.tencent.com/scf/list-detail?rid=4&ns=default&id=opcua-cloud-api
2. 点击「编辑」按钮（基础配置 section）
3. 找到「环境变量」区域
4. 点击「导入」按钮
5. 在文本框填入（每行 `KEY=VALUE`）：
   ```
   DB_HOST=sh-cynosdbmysql-grp-4f512ckw.sql.tencentcdb.com
   DB_PORT=21397
   DB_NAME=opcua_db
   DB_USER=opcua_user
   DB_PASSWORD=Admin_00
   ```
6. 点击「保存」

### 2. 上传 Flask 代码
当前状态：代码还是 Hello World 模板

**操作步骤：**
1. 在函数详情页，点击「切换到旧版编辑器」（或点击编辑区域右上角）
2. 选择「本地上传 ZIP 包」
3. 上传文件：`c:\Users\Administrator\WorkBuddy\20260326125244\opcua_api_bridge\cloud\scf\opcua_cloud_api.zip`
4. 保存

### 3. 配置 VPC（重要！）
数据库在内网，SCF 需要加入同一 VPC 才能访问

**操作步骤：**
1. 在函数详情页，点击「编辑」
2. 找到「网络配置」区域
3. 勾选「私有网络」
4. 选择：VPC = `vpc-dh9ul32a`，子网 = `subnet-ob3fhqkf`
5. 保存

### 4. 添加 HTTP 触发器（重要！）
当前函数是 Event 类型，没有 HTTP 入口

**操作步骤：**
1. 在函数详情页，点击左侧「触发管理」
2. 点击「创建触发器」
3. 触发方式选择「API网关」或「HTTP触发」
4. 配置：启用「集成响应」
5. 保存

### 5. 配置执行超时
当前只有 3 秒，需要调大：
1. 编辑函数配置
2. 找到「执行超时时间」
3. 改为 30 秒
4. 保存

## 代码文件
- ZIP 路径：`c:\Users\Administrator\WorkBuddy\20260326125244\opcua_api_bridge\cloud\scf\opcua_cloud_api.zip`
- 入口函数：Flask 默认 `app.run(host="0.0.0.0", port=9000)`
