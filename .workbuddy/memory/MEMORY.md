# 工作区完整记忆

> 更新时间：2026-06-02 16:00
> 工作区：`C:\Users\Administrator\WorkBuddy\2026-05-21-10-09-10\`
> 记忆仓库：WorkBuddy workbuddy/memory/ + GitHub 仓库

---

## 第一部分：用户画像

| 维度 | 描述 |
|------|------|
| 领域背景 | 工业自动化 / 工控系统，熟悉 OPC UA 协议 |
| 技术偏好 | 倾向于可落地的架构方案，不只是理论 |
| 工作风格 | 问题导向，直接切入技术细节 |

---

## 第二部分：用户习惯偏好（AI 必须遵守）

| 规则 | 说明 |
|------|------|
| 报表时间戳 | 精确到秒（`YYYY-MM-DD HH:MM:SS`），不需要毫秒/微秒 |
| 报表数值 | 浮点数保留 2 位小数（`round(value, 2)`） |
| 报表节点显示 | 只显示位号部分（去掉 `ns=1;s=` 前缀），如 `FIT_05R201F01.PV` |
| 适用范围 | 所有 Excel 报表、数据表格输出 |
| 交付前验证 | 任务完成后先自行检查/验证一遍再交付，必须验证核心功能正常 |
| 先想后做 | 执行大操作前先评估影响范围，列出可能受影响的依赖和服务 |
| 遇错先查 | 库兼容性报错第一时间查 GitHub Issues / 官方文档 |
| 软件安装路径 | 默认装到 D 盘（`D:\`），除非软件本身有特殊要求 |
| 专家协作 | 架构选型、代码质量、部署运维等任务主动召唤专家 Skill |
| 新增节点预热 | 新增到 config.py 的节点必须通过 API 预热订阅，否则 SQLite 无数据 |
| git push | 由用户手动触发，不配置自动 push；用户说"推送"时默认推送所有有用内容（记忆、日志、代码等） |
| 记忆管理 | 长期记忆统一在 WorkBuddy memory/ 同步到 Git 仓库 |
| Dashboard 测试 | 页面改完后用 `?test` URL 参数模拟边界场景（如 ERR=1），验证通过后删除测试代码 |

---

## 第三部分：系统环境

| 项目 | 值 |
|------|-----|
| OS | Windows 10 22H2 (Build 19045)，用户 Administrator |
| GitHub | 用户名 `z1139554189`，仓库 `OPC-UA-Bridge` |
| Python | D:\opcua_venv（Python 3.13.12，managed 版创建） |
| Git | D:\Program Files\Git，需绝对路径调用 |
| Chrome | `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| NSSM | `D:\Tools\nssm.exe`（v2.24 64-bit），Windows 服务管理 |
| 网络路由 | 永久路由 `172.30.0.0/24 → 192.168.10.1`（接口 192.168.10.10） |
| Tailscale VPN | 设备 `chinami-2rjmba3`，IP `100.96.61.25`，账号 GitHub `z1139554189` |
| Tailscale 设备2 | `v2405a`，IP `100.94.65.1` |
| Dashboard 远程地址 | `http://100.96.61.25:8000/dashboard`（需 Tailscale 组网） |
| 防火墙规则 | `OPCUABridge-8000`，TCP 8000 入站已开放 |

---

## 第四部分：OPC-UA-Bridge 项目（当前状态 2026-05-25）

### 项目概况

| 项目 | 值 |
|------|-----|
| 项目路径 | `C:\Users\Administrator\WorkBuddy\2026-05-21-10-09-10\OPC-UA-Bridge\opcua_api_bridge` |
| GitHub | `https://github.com/z1139554189/OPC-UA-Bridge` |
| 技术栈 | FastAPI + asyncua + SQLite + openpyxl + Chart.js |
| 版本 | v3.0.0 |

### OPC UA 服务器

| 项目 | 值 |
|------|-----|
| 服务器地址 | `opc.tcp://172.30.0.254:18950` |
| 本机 IP | `192.168.10.10` |
| 安全策略 | 无安全连接 |
| Session 限制 | 最大 **1 个** |
| Read 支持 | ❌ 必须用 Subscription 订阅 |
| 历史数据 | ❌ 不支持，桥接器自建 SQLite |

### 当前订阅节点（与报表一致）

| 类型 | 节点 | 说明 |
|------|------|------|
| FIT.PV | R301~R310 各 1 个（共 10） | 瞬时流量 kg/h |
| FIQ.OUT | R301~R310 各 1 个（共 10） | 累计流量 |
| FIT.ERR | R301~R310 各 1 个（共 10） | 传感器错误状态（0=正常，1=故障） |
| 总计 | **30 节点** | 来源：`reporter/config.py` REPORT_NODES + `src/api/main.py` `_DEFAULT_NODE_IDS` |

### Windows 服务（当前）

| 服务名 | 命令 | 端口 | 启动类型 |
|--------|------|------|----------|
| OPCUABridge | `D:\opcua_venv\Scripts\uvicorn.exe src.api.main:app --host 0.0.0.0 --port 8000` | 8000 | AUTO_START |
| OPCUAScheduler | `D:\opcua_venv\Scripts\python.exe reporter/scheduler.py` | — | AUTO_START |

### NSSM Recovery 策略

| 退出码 | OPCUABridge | OPCUAScheduler |
|--------|-------------|----------------|
| 0（正常退出） | 不重启 | 不重启 |
| 1（运行异常） | 重启 | 重启 |
| 2（参数错误） | 不重启 | — |

### Dashboard（2026-06-01 V1.0 正式发布）

| 功能 | 说明 |
|------|------|
| 实时看板 | 0.5秒防重叠轮询，30 卡片（FIT 绿色 kg/h + ERR 状态，FIQ 橙色 kg） |
| FIT/FIQ ERR 状态 | Quality=Good 且 ERR=0 → 绿色 Good，否则红色 Bad |
| 历史查询 | WPS 风格下拉选择器（分组 FIT/FIQ/ERR、搜索过滤、全选/清空/选中搜索结果） |
| Excel 导出 | 表头备注单位，数据格纯数值。取离桶起始时间最近的值 |
| 远程访问 | `window.location.origin` 适配，Tailscale 可远程访问 |
| 采样间隔 | 1s/5s/10s/30s/1m/5m/10m/30m/1h |
| 实时卡片筛选 | WPS 下拉筛选（FIT+FIQ 分组）+ Good/Bad 状态切换 |
| 卡片弹窗趋势图 | 单击卡片 → 实时图(0.5s更新, 200点滑动窗口) + 历史图(时间范围筛选, 全量原始数据) |
| 弹窗 NaN 缺口 | value=null 时 push NaN，Chart.js 自动断线可视化 |
| 弹窗 Excel 导出 | 导出弹窗中筛选时间范围的历史数据（非实时 buffer），CSV BOM UTF-8，文件名含时间范围 |
| 版本 | **`dashboard.html` = V1.1 正式版**（2026-06-02 10:18 发布） |
| 归档 | `dashboard_test.html`（v1）、`dashboard_test2.html`（v2）、`dashboard_test3.html`（v3） |
| 同步路径 | Bridge 修改后需同步到 `~/.node-red/dashboard.html`（Node-RED 独立副本） |

### SQLite 历史库

- 路径：`opcua_api_bridge/data/history.db`
- WAL 模式 + 365 天自动清理（2026-05-25 由 7 天改为 365 天）

### 项目模块清单（v7 遗留 + v3 新增）

| 模块 | 文件 | 说明 |
|------|------|------|
| API 主应用 | `src/api/main.py` | v3.0.0，FastAPI + Dashboard + 历史查询/导出 |
| OPC UA 客户端 | `src/opcua_client/client.py` | v7.1.0，v7.0 极简退避版 + TCP 端口预检：网络未就绪 → 30s 短退避，协议失败 → 1800s 长退避 |
| 配置管理 | `src/config/settings.py` | pydantic-settings |
| 健康检查 | `src/monitoring/health.py` | v3.0.0，检查采集状态 + 缓存新鲜度 + 推送年龄 |
| 历史存储 | `src/storage/__init__.py` | HistoryDB 异步 SQLite（WAL、buffer 批量写入、7 天清理、分表） |
| 调度器 | `reporter/scheduler.py` | 直读 SQLite + warmup 预热 + 文件锁检测 |
| Excel 报表 | `reporter/excel_report.py` | 追加式报表 |
| 报表配置 | `reporter/config.py` | 30 个 R3xx 节点（10 FIT PV + 10 FIQ OUT + 10 FIT ERR），间隔 1 分钟 |
| Dashboard | `dashboard.html` | 纯静态看板，Chart.js 趋势图 + Excel 导出。**2026-05-25 新增**：FIT 卡片 ERR 状态（Quality=Good 且 ERR=0 → 绿色 Good，否则红色 Bad）；历史查询复选框含 ERR 节点；批量查询扩展至 30 节点。**同步路径**：Bridge 修改后需同步到 `~/.node-red/dashboard.html`（Node-RED 独立副本） |

---

## 第五部分：云端部署（历史参考）

| 项目 | 值 |
|------|-----|
| TDSQL-C MySQL | 上海，`sh-cynosdbmysql-grp-4f512ckw.sql.tencentcdb.com:21397` |
| VPC | `vpc-dh9ul32a`，子网 `subnet-ob3fhqkf` |
| 数据库 | `opcua_db`，用户 `opcua_user` |
| 代码位置 | `opcua_bridge_cloud/` + `scf_browser_scripts/` |

---

## 第六部分：经验教训

### 历史教训（v7 时期）

| 问题 | 根因 | 解决方案 |
|------|------|----------|
| 读值返回 Bad | 服务器不支持 Read | 必须用 Subscription 订阅 |
| 历史数据稀疏 | 值不变不推送 | 60 秒定期轮询强制写缓存 |
| 调度器成功率 48% | append 清空合并单元格报错 | 首次新建 + 后续只追加新行 |
| 报表数据不更新 | 新节点未预热订阅 | warmup_subscriptions() 启动预热 |
| Session 断了健康检查仍报 healthy | is_connected() 只看布尔标志 | 改为检查 _last_data_time 新鲜度（120s 超时） |
| 死值和断连无法区分 | v6 只检查 session 对象是否在内存里 | v7 推送超时检测：300 秒无推送 = 断连 + 心跳写入 |
| 推送超时 30s 误判断连 | 只订阅 5 个同类型节点，值全不变 | PUSH_TIMEOUT 30s→300s + 新增节点分散相关性 |

### 2026-05-21 项目重建教训（6 大坑）

| # | 问题 | 根因 | 教训 |
|---|------|------|------|
| 1 | 订阅节点列表错误 | 凭空捏造 R301~R305 F01+F02，未参照 `reporter/config.py` | **永远找权威来源**：节点列表以 `config.py` 的 `REPORT_NODES` 为准 |
| 2 | Bridge 心跳崩溃 | `self._yielded` 不存在（AttributeError） | **API 先读后写**：调用属性前确认方法名，`is_yielded()` vs `_yielded` |
| 3 | 启动 0 节点订阅 | `_collect_nodes` 为空，`add_nodes()` 必须在 `start()` 之前 | **理解启动顺序**：订阅发生在 `_connect_opc()` 时，节点必须提前注册 |
| 4 | Canvas 上下文冲突 | Chart.js `destroy()` 不清理 2D context | **重建优于销毁**：Chart.js 复用时销毁后重建 canvas DOM |
| 5 | 日期适配器缺失 | Chart.js v4 时间轴需外部适配器 | **换方案优于补依赖**：`type: 'linear'` + 毫秒时间戳 + 手动格式化 tick |
| 6 | Excel 时间不对齐 + 空白 | 精确字符串匹配时间戳，毫秒不同致匹配失败 | **时间桶对齐**：不同源数据按固定间隔分桶，不依赖精确时间戳 |
| — | 旧 MEMORY.md 被覆盖 | 整合记忆时误用 Write 覆盖而非 Edit 追加 | **整合记忆用 Edit 追加**，不得覆盖已有内容 |

### 2026-05-22 Tailscale 组网教训

| # | 问题 | 根因 | 教训 |
|---|------|------|------|
| 7 | Dashboard 远程访问无数据 | JS 硬编码 `http://localhost:8000`，远程浏览器把 API 请求发到自己机器 | **前端 BASE URL 用 `window.location.origin`**，自动适配访问地址 |

### 2026-05-22 开机自启失败教训

| # | 问题 | 根因 | 教训 |
|---|------|------|------|
| 8 | 关机重启后桥接器 30 分钟无数据 | 开机时网络未就绪 → `WinError 1232` → 被当作普通连接失败进入 1800s 退避 | **区分网络错误和协议错误**：TCP 端口预检 → 网络不通用短退避（30s），协议失败用长退避（1800s） | ✅ 2026-05-22 15:30 用户实测验证通过 |

### 2026-05-25 经验教训

| # | 问题 | 根因 | 教训 |
|---|------|------|------|
| 9 | git push 只推代码漏推记忆文件 | 记忆文件和代码分属不同目录，AI 未自动关联 | **git push 必须同时推送 `.workbuddy/memory/` 全部文件**，已写入 `~/.workbuddy/MEMORY.md` 跨项目规则 |
| 10 | Excel 导出历史数据取值策略不明确 | 时间桶内有多条记录时，"取最新"与"取最接近桶起始"结果不同 | **Excel 导出取离桶起始时间最近的值**（`min(abs(record_ts - bucket_start))`），而非桶内最新记录。适用于 `src/api/main.py` 历史导出接口 |
| 11 | 测试版改动重构了正常功能 | 把"加卡片搜索"顺手扩展成"重构整个位号选择器"，导致历史数据下拉框异常 | **测试版必须增量叠加，不动已有正常功能**。只加新功能，不改旧功能 |
| 12 | "回退到某版本"理解反了 | 用户说"回退实时数据没有搜索框的版本"，理解成"回退整个 WPS 下拉框" | **"回退到某版本"必须精确确认范围**，不能凭猜测扩大回退范围 |

### 2026-06-02 看板轮询优化教训

| # | 问题 | 根因 | 教训 |
|---|------|------|------|
| 24 | 用户问"看板刷新多久"，AI 回答趋势图而非卡片 | 同一 poll 函数同时驱动卡片和趋势图，AI 理解偏差 | **用户问卡片就答卡片，不要混淆**。虽然共享同一 poll 函数，但用户感知不同 |
| 25 | setInterval 异步函数可能请求重叠 | setInterval 不管上次是否完成，到点就触发 | **异步轮询用 `setTimeout` 递归替代 `setInterval`**：`await poll()` → `setTimeout(loop, interval)` 防重叠雪崩 |
| 26 | 改轮询间隔需同步更新弹窗标签 | 标签硬编码"每3秒"，改间隔后忘记更新 | **轮询间隔改完后检查所有含"秒"的 UI 文字**（实时趋势标签、注释等） |
| 27 | OPC UA 推送到达后缓存更新延迟 2s | _SubHandler 只写 buffer，_value_cache 在 _flush_buffer 才更新 | **缓存和 SQLite 落库解耦**：推送回调里立即更新 _value_cache，SQLite 仍批量写。asyncio 单线程安全 | ✅ 2026-06-02 15:59 用户实测验证通过，"确实灵敏多了" |

### 2026-06-01 Dashboard 测试版教训
| # | 问题 | 根因 | 教训 |
|---|------|------|------|
| 13 | 直接改生产版 `dashboard.html` | 未先建测试版，用户纠正后才回滚 | **改界面前必须先建 `xxx_test.html` 测试版**，确认好用再覆盖生产版 |
| 14 | `querySelector('#lbl_FIT_05R301F01.PV')` 匹配失败 | `.` 在 CSS 选择器中被解析为类选择器 | **位号含 `.` 时不能用 id 选择器**，统一用 `data-*` 属性（`[data-node="..."]`) |
| 15 | 搜索框为空时点"选中搜索结果"选中全部 | `filterNodes('')` 把所有 label 的 `node-group-hidden` 移除，全员"可见" | **`selectAllVisible()` 必须加空搜索 guard**：`if (!search.value.trim()) return` |
| 16 | 测试版功能范围理解错误（两次） | 第一次把 WPS 下拉框整个回退；第二次把实时卡片搜索留在了测试版 | **用户说"回退到某版本"时，必须先确认精确范围再动手** |
| 17 | 误读 GitHub"仓库迁移"提示，代码推到错误仓库 | push 输出含"迁移"提示，AI 擅自改 remote URL | **GitHub 仓库地址是用户指定的，绝不自行修改**。迁移提示 ≠ 改名指令 |
| 18 | 第三个下拉框（卡片筛选）函数名冲突 | 已有 `nodeEl`/`toggleDropdown` 等函数名被历史下拉框占用 | **用 `card` 前缀隔离命名空间**：`cardNodeEl`、`toggleCardDropdown`、`cardSelectAll` 等 |
| 19 | dashboard_test2.html 功能验证通过 | 三版本并行（生产/v1/v2），纯增量叠加成功 | **命名空间隔离是叠加功能的正确方式**，`card` 前缀零冲突 |
| 20 | dashboard_test3.html 弹窗双图表 | 实时图+历史图需要两套 Chart.js 实例分别管理 | **弹窗图表生命周期**：open 时创建 + 实时推送；close 时全部 destroy；query 时重建 canvas 防上下文污染 |
| 21 | 弹窗 Excel 导出按钮无响应 | `a.download` 设了但 `a.href` 忘记赋值 | **浏览器下载三要素缺一不可**：`a.href = blobUrl` + `a.download = fileName` + `document.body.appendChild(a)` |
| 22 | 弹窗导出数据源搞错 | 初版导出的是 `popupRtBuffer`（实时数据），用户要的是筛选时间范围的历史数据 | **弹窗导出 = 历史查询结果**，缓存最近一次 `queryPopupHistory()` 的响应用于导出 |
| 23 | CSV 时间列被 Excel 截断到分钟 | Excel 自动把 `2026-05-30 20:47:15` 解析为日期格式，丢弃秒位 | **CSV 时间用 `="YYYY-MM-DD HH:MM:SS"` 包裹**，强制 Excel 文本模式，秒位不丢 |

---

## 第七部分：工作流程规则

### 文件清理流程
1. 全面扫描目录树，不遗漏任何角落
2. 分析分类：区分核心文件 vs 无用文件
3. 列表确认：列出所有待删除项 + 原因，请用户逐项确认
4. 执行删除
5. 生成记忆记录结果

### Git 推送流程
1. `git status` + `git ls-files` 确保无遗漏
2. `git add` 所有有价值文件（代码 + 记忆 + 日志）
3. `git commit` 提交
4. `git push origin main`

### 记忆整合流程（重要！）
- 整合已有记忆时使用 **Edit 追加**，不得用 Write 覆盖
- MEMORY.md 是累积记录，不是重建文档

**核心原则**：一个不落推送 git

---

## 第八部分：已安装 Skills

find-skills、python-backend、fastapi、docker-compose、node-red-manager

---

_后续增量记录在 `memory/YYYY-MM-DD.md`，长期经验沉淀在本文件。_
