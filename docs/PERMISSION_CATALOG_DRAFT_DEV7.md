# V1.6 权限动作目录与旧授权迁移草案（dev.7）

状态：草案，不接入生产鉴权；dev.7 不改变现有权限判断。

## 1. 统一权限表达

权限由 `module.action` 表示；授权规则额外携带 `scope`、必要条件及字段限制。前端目录只用于呈现，服务端以当前会话、记录归属和绑定关系重新计算。未知动作、缺少策略的受保护路由默认拒绝。

建议范围：

- `self`：仅当前账号本人拥有/关联的记录。
- `assigned`：仅当前账号被明确分配或绑定的人员/资源。
- `store`：全店范围。

仅当数据存在可信归属关系时才允许 `self/assigned`。不得把客户端传入的 user_id、staff_id、scope 当作授权依据。

## 2. 动作目录草案

| 模块 | 动作键 | 说明 | 旧权限候选映射 |
|---|---|---|---|
| dashboard | `dashboard.read` | 首页看板 | `dashboard` |
| catalog | `products.read` | 商品列表、详情、选项 | `products_view`；部分旧日常登记读商品路径还允许 `operation` |
| catalog | `products.create` | 新增商品 | `products_manage` |
| catalog | `products.update` | 修改商品资料 | `products_manage` |
| catalog | `products.status.change` | 停用/启用商品；现有 DELETE 实为停用 | `products_manage` |
| catalog | `products.image.update` | 上传/替换商品图片 | `products_manage` |
| catalog | `products.export` | 商品数据导出 | 旧 `export`（需按导出 kind 精确确认） |
| inventory | `inventory.read` | 当前库存/预警读取 | `products_view`、`operation`、`products_manage` 按原路由差异映射 |
| inventory | `inventory.inbound.create` | 入库单提交 | `operation` |
| inventory | `inventory.outbound.create` | 出库单提交 | `operation` |
| inventory | `inventory.stocktake.adjust` | 盘点差异调整 | `operation`；需单独核对原接口业务类型和敏感性 |
| transactions | `transactions.read` | 流水与历史查询 | `transactions_view` |
| transactions | `transactions.export` | 流水/历史导出 | 旧 `export` |
| procurement | `purchase.read` | 当前采购页面/建议 | `purchase_view` |
| procurement | `purchase.export` | 采购导出 | 旧 `export` |
| scheduling | `schedule.read` | 排班与人员视图读取 | `schedule_view`；部分旧路径也允许 `schedule_manage`/`schedule_self_manage` |
| scheduling | `schedule.shift.update` | 班次/日期排班调整 | `schedule_manage`；`schedule_self_manage` 仅保留本人当天的受限能力 |
| scheduling | `schedule.rules.update` | 全局排班规则调整 | `schedule_manage` |
| scheduling | `schedule.staff.bind` | 账号与美容师绑定 | 旧接口为管理员专属，建议仅管理员 |
| appointments | `appointments.read` | 预约详情与列表 | `schedule_view`、`schedule_manage`、`schedule_self_manage` 按原范围映射 |
| appointments | `appointments.create` | 新增预约 | `schedule_manage`；本人权限须遵守本人当天约束 |
| appointments | `appointments.update` | 修改预约 | `schedule_manage`；本人权限须遵守本人当天约束 |
| appointments | `appointments.status.change` | 取消/完成等状态变化 | 待按现有状态规则拆分；不得从全量状态写入推断过宽权限 |
| appointments | `appointments.transfer` | 预约分段转交 | `schedule_manage` |
| schedule_requests | `requests.create` | 提交休息/换班申请 | `schedule_self_manage` 或管理员管理权限，按真实身份绑定判断 |
| schedule_requests | `requests.read` | 查看申请 | `schedule_view`、`schedule_manage`、`schedule_approve`、`schedule_self_manage`，按本人/待审范围过滤 |
| schedule_requests | `requests.approve` | 审批申请 | `schedule_approve` 或 `schedule_manage` |
| customers | `customers.read` | 客户档案查询 | `customers_view`、`customers_manage` |
| customers | `customers.create` | 新建客户 | `customers_manage` |
| customers | `customers.update` | 编辑客户资料 | `customers_manage` |
| customers | `customers.delete` | 删除客户 | `customers_manage`；删除行为需保留现有语义并审计 |
| customers | `customers.phone_tail.read` | 读取联系方式尾号 | `customers_view`/`customers_manage`，需评估字段授权 |
| customers | `customers.notes.read` | 读取客户备注 | `customers_view`/`customers_manage`，需评估字段授权 |
| waitlist | `waitlist.read` | 候补列表 | `waitlist_manage`、`schedule_manage` |
| waitlist | `waitlist.create` | 登记候补 | `waitlist_manage`、`schedule_manage` |
| waitlist | `waitlist.status.change` | 已安排/取消等状态变更 | `waitlist_manage`、`schedule_manage` |
| users | `users.read` | 员工列表 | 管理员专属（现有 `/api/users*`） |
| users | `users.create` | 创建员工 | 管理员专属 |
| users | `users.update` | 修改员工资料/启停 | 管理员专属；不同字段需再细分 |
| users | `users.activation_code.regenerate` | 重发首次登录码 | 管理员专属、审计 |
| users | `users.credentials.reset` | 重置员工凭证 | 管理员专属、敏感审计 |
| authorization | `permission_groups.read` | 查看规则组 | 管理员专属 |
| authorization | `permission_groups.create` | 创建规则组 | 管理员专属 |
| authorization | `permission_groups.update` | 修改规则组动作/范围 | 管理员专属、敏感审计 |
| authorization | `permission_groups.delete` | 删除规则组 | 管理员专属、敏感审计 |
| authorization | `permission_groups.members.assign` | 成员加入/移出规则组 | 管理员专属、敏感审计 |
| authorization | `permission_groups.effective.read` | 查看员工有效权限来源 | 管理员专属 |
| reporting | `reports.read` | 经营报表 | `reports_view` |
| reporting | `reports.export` | 报表导出 | 旧 `export` + `reports_view`，需确认原业务约定 |
| messaging | `notifications.read` | 查看消息 | `notifications_view` |
| messaging | `notifications.mark_read` | 标记已读 | `notifications_view` |
| devices | `devices.read` | 查看在线设备 | `devices_view` |
| devices | `devices.heartbeat` | 当前登录设备心跳 | 现有设备 POST 路由允许 dashboard/schedule/products/operation 任一权限；建议以已认证会话为前提，后续确认 |
| audit | `audit.read` | 查看审计日志 | `audit_view` |
| settings | `settings.read` | 查看系统设置 | `settings_view`、`settings_manage` |
| settings | `settings.update` | 修改系统设置 | `settings_manage`；现有规则组禁止普通组授予 |
| backup | `backup.read` | 查看备份列表 | `backup_view`、`backup_manage`、`backup_restore` |
| backup | `backup.create` | 创建备份 | `backup_manage`，敏感审计 |
| backup | `backup.download` | 下载备份 | `backup_view`、`backup_manage`、`backup_restore`，需单独评估数据泄露风险 |
| backup | `backup.restore` | 恢复数据库/备份包 | `backup_restore`，管理员级敏感权限，二次确认和审计 |
| system | `system.identity.read` | 系统名称/版本等 | 当前按若干业务权限之一允许；迁移时保持可见范围 |
| system | `system.health.read` | 健康状态 | 现有公开 liveness 与登录健康接口分开，保留健康探针语义 |

## 3. 旧权限映射的安全原则

- 旧权限路由策略含 `any` 组合；迁移时要保留每条 method/path 的实际允许集合，不可简单把一个旧键映射到所有新动作。
- `products_manage` 当前包含新增、编辑、停用和图片更新，拆分后应把旧授权逐项展开，不能丢权；之后管理员可主动收紧。
- `operation` 同时承担日常登记入口与 `/api/submit`，但商品选项/商品读取也会接受它。不能因此自动授予完整商品档案编辑权。
- `export` 目前覆盖 `/api/export/{kind}` 多种导出，必须按 kind 和导出字段清点后映射，不能把一个旧 export 直接扩大为所有业务导出。
- `schedule_manage` 与 `schedule_self_manage` 在旧路由级别有 any 语义；具体本人当天限制还依赖服务端业务校验。新模型必须同时保留身份绑定、日期条件、资源范围和操作差异。
- `DELETE /api/products/{id}` 的当前行为是停用，不得迁移为物理删除。
- `backup_restore`、`backup_manage`、`settings_manage` 已在普通规则组禁授列表；不能通过普通组间接获得。
- 未知权限拒绝；缺少新策略拒绝；旧授权差异必须先预览并审计。

## 4. 迁移步骤（dev.8 实施前置）

1. 从现有数据库快照和生产授权导出只读授权清单（生产数据不随源码包迁移）。
2. 逐路由记录 method、路径、旧权限 any/all、业务用例、记录归属和字段返回。
3. 生成旧键到新动作/范围的版本化映射；无法等价映射项标记人工决策。
4. 生成每个账号/规则组迁移前后 diff，列出新增、减少、未映射及敏感授权。
5. 测试所有组合：无权、只读、可新增不可编辑、本人当天、其他员工、全店、多个组叠加、撤权后的已有会话。
6. 新旧判定切换后，新模型为唯一判定源；不得永久使用旧粗权限与新细权限做 OR 兜底。

## 5. 本轮未实施

- 没有更改 `legacy_policy.py`、`defaults.py` 或生产路由守卫。
- 没有新增数据库字段/表，也没有运行 schema 迁移。
- 本目录所有动作名称均为草案，尚不可用于配置正式员工权限。
