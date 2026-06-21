# Bot路由器 v2.0

## 核心逻辑
1. 主Bot接收所有消息并处理，副Bot不接收消息仅作为发送通道
2. 受管插件(在副Bot配置中注册的插件)由余量最多的副Bot发送
3. 非受管插件: 主Bot发送，受主Bot限额约束
4. 主Bot不参与受管插件的发送（除非fallback）
5. 副Bot满载时发送一次"消息已满"通知，随后该Bot今日不再发送
6. 0点自动重置（通过数据库stat_date实现）
7. 白名单插件和超级用户(可配置)绕过限额
8. 消息去重: 防止多Bot发送相同消息

## Fallback机制
- 当目标Bot发送失败(ActionFailed)时，自动fallback到主Bot
- 主Bot需在目标群内，否则放弃发送并抛出原异常
- 发送失败后清除该Bot的群成员缓存，下次select_bot会重新检查
- 只fallback 1次，主Bot也失败则放弃

## 配置文件
插件目录下 `config.yaml`
