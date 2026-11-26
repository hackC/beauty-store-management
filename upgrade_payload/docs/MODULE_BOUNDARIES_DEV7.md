# V1.6 全站模块边界与接口清单（dev.7）

状态：设计基线；不是已完成的代码迁移。依据 dev.6 源码静态审计形成。

## 1. 设计约束

- 领域层只表达业务规则，不依赖 FastAPI、SQLite、Tkinter、文件系统或 `main.py`。
- 应用层组织用例、鉴权、事务、幂等、并发版本和结果；只依赖最小接口（`Protocol`）。
- 端口/接口层定义输入输出、错误、数据归属、权限动作和并发约定。
- 适配层实现 SQLite、文件/图片、HTTP、WebSocket、桌面窗口。
- 组合入口创建具体适配器并注入；业务模块不得反向导入 `main.py`。
- 模块不得任意读写其他模块的数据表。跨模块写入由明确应用用例协调，提交成功后再发布事件。
- 先保留旧 HTTP 契约和业务语义；拆分代码与改变业务权限语义分开验收。

## 2. 建议模块及数据所有权

| 模块 | 主要职责 | 当前接口/页面 | 主要数据所有权（目标） | 主要端口 |
|---|---|---|---|---|
| `identity` 账号与会话 | 初始化、登录、登出、首登设密、账号启停、会话失效 | `/api/auth/*`、`/api/users*`；人员管理页 | `users`、`user_sessions` | `UserRepository`、`SessionRepository`、`PasswordHasher`、`Clock` |
| `authorization` 权限与规则组 | 权限目录、规则组、成员、有效权限、审计 | `/api/permission-groups*`、现有 `/api/permissions/*` | `permission_groups`、`permission_group_members` | 复用 dev.6 权限 Ports；增加授权写入/审计端口 |
| `catalog` 商品档案 | 商品主档、条码、图片元数据、分类、阈值、启停 | `/api/products*`、`/api/product-options`；商品档案页 | `products`、`data/product_images/` | `ProductRepository`、`ProductImageStore` |
| `inventory` 库存与单据 | 入库、出库、盘点调整、库存计算、幂等、流水 | `/api/submit`、`/api/transactions`、`/api/inventory/alerts`；日常登记/流水/历史 | `documents`、`transactions`；与 catalog 通过接口读商品 | `InventoryUnitOfWork`、`StockPolicy`、`IdempotencyStore`、`ProductReader` |
| `procurement` 采购 | 当前采购建议/清单查询、筛选、导出；不擅自新增采购审批/收货流程 | `/api/purchase`、相关导出；采购管理页 | 当前采购附件 `data/purchase/`；采购能力需先核实现有数据来源 | `PurchaseFileStore`、`PurchaseQuery` |
| `scheduling` 排班与预约 | 排班规则、班次、预约、冲突、转交、申请审批、工作量 | `/api/schedule/state`、`/migrate`、`/staff-links`、`/transfers`、`/appointments/transfer`、`/requests*`、`/workload`；`/schedule` | `schedule_state`、`schedule_staff_users`、`schedule_transfer_history`、`schedule_requests`、`schedule_reminder_log` | `ScheduleRepository`、`StaffDirectory`、`AppointmentConflictChecker`、`Clock` |
| `customers` 客户与候补 | 客户档案、备注、联系方式尾号、候补登记和状态 | `/api/customers*`、`/api/schedule/waitlist*`；排班协作区 | `customer_profiles`、`schedule_waitlist` | `CustomerRepository`、`WaitlistRepository` |
| `messaging` 消息中心 | 消息列表、未读/已读、权限过滤、事件发布 | `/api/notifications*`、WebSocket 事件；主站消息中心 | `app_notifications` | `NotificationRepository`、`EventPublisher` |
| `reporting` 看板与报表 | 汇总、库存/预约/员工负荷统计、授权过滤、导出 | `/api/summary`、`/api/reports/operations`、`/api/export/{kind}`；首页/运营监控中心 | 只读投影，不拥有业务主表 | `InventoryReadModel`、`ScheduleReadModel`、`ReportExporter` |
| `operations` 系统与运维 | 配置、备份/恢复、审计、设备、健康状态、日志 | `/api/settings*`、`/api/backup*`、`/api/restore`、`/api/audit`、`/api/devices*`、`/api/health*`、`/api/system/identity` | `settings`、`meta`、`audit_logs`、`device_presence`；备份文件 | `SettingsRepository`、`BackupStore`、`RestoreCoordinator`、`AuditWriter`、`DevicePresenceRepository` |
| `desktop` 桌面服务管理 | Tkinter 管理窗口、启动/停止、日志、目录打开 | `main.py` 中桌面窗口/启动管理 | 不拥有业务数据 | `ServerLifecycle`、`LogReader`、`PathProvider` |
| `webapp` 前端应用壳 | 导航、路由、权限态、API 客户端、错误态、响应式布局 | `static/index.html`、`static/index_V2_5_5.html` | 不拥有业务数据 | `ApiClient`、`PermissionClient`、共享状态接口 |

## 3. 当前源码落点（审计事实）

- `main.py` 约 5,300 行，当前 65 个 HTTP 路由和 1 个 WebSocket 路由；账号、库存、排班、备份、报表、审计、设备和桌面管理大量集中在入口文件。
- `static/index.html` 同时承载首页、日常登记、商品档案、库存流水、采购、历史、设置、备份、帮助、人员/规则组和运营监控中心等页面。
- `static/index_V2_5_5.html` 是独立的每日排班页面；两份 HTML 都混合结构、样式与交互脚本。新架构共享业务用例与 API 客户端，PC/手机分别呈现，不复制业务规则。
- dev.6 已独立 `store_inventory/permissions/`；该模块是后续模块化的参考实现，不要求重写。

## 4. 关键用例接口草案

### 商品与库存

- `CreateProduct.execute(command) -> ProductResult`
- `UpdateProduct.execute(product_id, expected_version, command) -> ProductResult`
- `ChangeProductStatus.execute(product_id, status, actor) -> Result`
- `RecordStockMovement.execute(kind, lines, idempotency_key, actor) -> DocumentResult`
- `AdjustStocktake.execute(counts, expected_revision, actor) -> StocktakeResult`

商品新增、编辑、启停、图片更新分别鉴权。库存操作按入库/出库/盘点类型鉴权；商品读取通过 `ProductReader`，不得让 inventory 直接 SQL 写 products。

### 排班与预约

- `CreateAppointment` / `UpdateAppointment` / `ChangeAppointmentStatus`
- `TransferAppointmentSegment`
- `SubmitScheduleRequest` / `ReviewScheduleRequest`
- `SaveScheduleRules` / `BindScheduleStaff`

每个命令显式携带当前身份、目标记录、期望 revision 和必要幂等键。本人当天限制在服务端通过当前账号与美容师绑定计算，不能信任客户端传入的 user/staff/scope。

### 平台

- 备份创建、下载、恢复分离；恢复是敏感用例，必须验证文件、先备份、授权、事务替换、回滚及审计。
- 报表只通过各业务模块只读查询端口获取数据；导出与页面查询使用相同范围和字段过滤。
- 消息在业务事务提交后发布；WebSocket 订阅与每条事件都受当前权限约束，撤权后需重新校验或断开。

## 5. 模块迁移顺序

1. dev.8：identity + authorization + webapp 公共壳；保留 dev.6 permission 核心并补授权写入端口。
2. dev.9：catalog + inventory + procurement；先迁移读模型，再迁移写用例；库存事务/幂等回归优先。
3. dev.10：scheduling + customers；拆解全量状态接口的语义差异，逐步引入命令接口，旧接口仅作兼容适配。
4. dev.11：reporting + messaging + operations + desktop。
5. dev.12：全站两端视觉、交互、滚动固定和性能验收。

本清单是边界草案。若发现现有业务跨表事务或实际文件归属与表格不同，先通过代码/数据审计修正模块所有权，不为追求目录整齐而破坏事务边界。
