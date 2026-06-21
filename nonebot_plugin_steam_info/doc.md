# Steam Info 插件

## 配置项
- `STEAM_API_KEY`: Steam API Key，支持单个字符串或列表（多 Key 容错）
- `STEAM_REQUEST_INTERVAL`: 定时轮询间隔（秒），默认 60
- `STEAM_BROADCAST_TYPE`: 播报类型 `all`/`part`/`none`
- `STEAM_DISABLE_BROADCAST_ON_STARTUP`: 是否禁用启动时播报
- `PROXY`: 代理地址（可选）
- `STEAM_FONT_*_PATH`: 字体路径

## 核心功能模块

### Steam API 调用（steam.py）
- `get_steam_users_info`: 批量获取玩家概要信息（最多100个/批），支持多 API Key 轮替
- `get_steam_id`: Steam好友码转 Steam ID
- `get_user_data`: 爬取 Steam 社区主页获取详细资料
- **重试机制**: 每个 API Key 最多重试 3 次，重试间隔为指数退避（2^attempt: 1s, 2s, 4s）

### 动态灵敏度播报（data_source.py）
播报逻辑根据状态变化类型采用不同灵敏度：
- **开始玩游戏** → 立即播报
- **切换游戏**（A→B）→ 立即播报，A 的游玩时间以 B 开始时刻为结束时间
- **停止玩游戏** → 进入5分钟宽限期，需连续5次检查均不玩游戏才确认播报
  - 宽限期内恢复同一游戏 → 视为网络故障，不播报，`game_start_time` 保持原值（游玩时间连续计算）
  - 宽限期内开始玩其他游戏 → 立即播报切换（同直接切换逻辑）
  - 确认停止后，播报的结束时间为首次检测到停止的时刻（即实际停止时间）
- **宽限期状态不持久化**，服务器重启后重新计时
- 游戏同一性判断：优先比较 `gameid`，回退比较 `gameextrainfo`

### 定时播报（__init__.py）
- 通过 APScheduler 定时执行 `fetch_and_broadcast_steam_info`
- `update_by_players` 返回事件列表，按 parent_id 过滤后播报
- 启动/重连时 `on_bot_connect` 调用 `_startup_sync`，通过 `init_mode=True` 仅同步状态，不推进 pending_stop 计数、不生成事件

### 手动查询（steamcheck 命令）
- 直接调用 `get_steam_users_info` 获取当前状态，不走缓存
- 如果 API 返回空玩家列表，提示"连接 Steam API 失败，请重试"

### 头像管理（utils.py）
- **缓存**：以 `steamid + avatarhash` 为文件名缓存到本地，头像变更时自动重新下载
- **重试**：`avatarfull` CDN 下载最多重试 3 次，指数退避（2^attempt: 1s, 2s, 4s）
- **容错**：下载失败（网络异常/HTTP 非200）时自动回落默认头像，不阻断主流程
- **后台预加载**：每次 `update_steam_info` 成功后异步批量下载未缓存头像
- **定时刷新**：每 24 小时全量检查并补下未缓存头像

### 游戏名汉化（utils.py）
- 通过 Steam Store API 获取中文名称，按 gameid 缓存到 `game_name_cache.json`
- 播报和状态展示时自动替换为中文名

### 数据存储（data_source.py）
- `BindData`: 用户 Steam 绑定数据（JSON）
- `SteamInfoData`: 玩家状态历史数据 + 动态灵敏度状态机（pending_stops 不持久化）
- `ParentData`: 群聊头像/名称
- `DisableParentData`: 禁用播报的群聊列表

## 注意事项
- 启动时 `on_bot_connect` 会执行一次 `update_steam_info`（只更新数据不播报）
- Steam API 单次最多查询 100 个玩家，超量自动分批
- 关键路径有 info 级别日志，前缀 `[Steam API]`/`[Steam 播报]`/`[Steam 查看]`/`[Steam 头像]`，方便排查连接失败问题
- API Key 会在日志中脱敏显示（仅前 4 位 + `****`）
