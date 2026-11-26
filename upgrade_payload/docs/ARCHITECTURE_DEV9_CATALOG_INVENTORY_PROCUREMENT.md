# V1.6.0-dev.9 商品、库存与采购模块化报告

## 1. 本轮目标

将商品主档、库存流水/单据和现有采购建议查询从 `main.py` 的 SQL 与业务逻辑中拆分为 `catalog`、`inventory`、`procurement` 三个业务模块。保留现有 HTTP 路径、响应字段、条码规则、库存算法、单据幂等、低库存提醒、图片存储和采购建议语义。

## 2. 新增模块

### `store_inventory/catalog/`

- `domain.py`：商品领域错误。
- `ports.py`：商品仓储、库存读取、图片处理和图片存储端口。
- `sqlite.py`：商品主档查询/新增/编辑/停用/删除、图片引用持久化；只写 `products` 主档。
- `images.py`：Pillow 图片验证、EXIF 方向处理、中心裁切为 640x640 JPEG、安全文件名和原子文件替换。
- `service.py`：商品列表与库存读模型组合、商品保存、图片读写和停用用例。
- `composition.py`：注入数据库、库存读取接口、图片目录和库存阈值配置。

### `store_inventory/inventory/`

- `domain.py`、`ports.py`：库存业务错误与仓储/安全备份端口。
- `sqlite.py`：库存汇总、入库/出库/盘点调整事务、幂等 request_id、出库库存校验、库存流水和低库存通知。
- `service.py`：业务类型/数量/重复商品校验、安全备份前置、库存用例。
- `composition.py`：注入数据库、备份守卫和阈值配置。

库存写入仍在 `BEGIN IMMEDIATE` 事务内完成；文档、流水、审计和低库存通知在同一事务提交。重复 request_id 返回原单据；无差异盘点不生成空单据。

### `store_inventory/procurement/`

- 当前仅封装已有采购建议清单查询，调用 catalog 查询端口获取低库存商品。
- 没有擅自新增采购审批、收货、供应商或采购单据流程。

### `store_inventory/platform/audit.py`

新增可复用审计操作者值对象和 SQLite 审计写入适配器。业务仓储在自己的事务连接上写审计，避免反向依赖 `main.py`。

## 3. 依赖关系

`procurement -> catalog query port`；`catalog -> inventory stock read port`；`inventory` 拥有单据与流水写入用例，并通过注入的备份守卫调用现有运维备份能力。SQLite 适配器位于各模块内部。`main.py` 负责 FastAPI 路由、Cookie/HTTP 错误转换、服务组合、revision 和 WebSocket 广播。

`catalog` 读取库存通过 `InventoryStockReader`；`inventory` 仅在单据事务中只读 `products` 主档以校验商品启用状态和名称，不写商品主档。

## 4. 细粒度权限落地

注册四个模块及动作：

- `ext.catalog.*`：read/create/update/disable/image_read/image_update/export。
- `ext.inventory.*`：read/inbound_create/outbound_create/stocktake_adjust。
- `ext.transactions.*`：read/export。
- `ext.procurement.*`：read/export。

旧权限通过 `permissions/migration.py` 展开为新增动作：

- `products_manage` -> 商品读取/新增/编辑/停用/图片读写；不自动获得导出。
- `products_view` -> 商品读取、图片读取、库存预警读取。
- `operation` -> 商品读取、库存读取、入库/出库/盘点调整。
- `transactions_view` -> 流水读取。
- `purchase_view` -> 采购建议读取和商品图片读取，不自动获得完整商品档案读取。
- `export` -> 当前已有商品/流水/采购三类导出动作。

路由守卫保留旧权限兼容，业务入口再次执行精确动作校验。商品 `POST /api/products` 根据数据库事务中的真实记录存在状态分别检查 create/update，避免仅凭客户端提交 ID 选择权限。库存 `/api/submit` 根据真实单据类型分别检查入库、出库或盘点调整。导出根据 kind 单独鉴权。

旧权限扩展是可解释的兼容映射，不代表所有旧权限都被合并成一个宽泛新权限。管理员可直接访问；普通权限组只能授予注册且可分配的新动作。

## 5. 保留的业务规则

- 条码原样保存，不 trim、不补零、不改变前导零；唯一性仍由 SQLite 约束；多个空条码允许存在。
- 商品 ID 不因条码变化；商品停用/删除仍依据是否存在库存流水决定软停用或物理删除。
- 商品列表库存 = opening + 入库 - 出库 + 调整，结果保留三位小数。
- 阈值为 null 或 0 时商品列表使用系统默认阈值；库存单据预警仍按旧规则对 null 使用默认值、显式 0 保留 0。
- 出库不能超过当前库存；入库/出库数量必须大于 0；盘点实盘数不能小于 0。
- 同一单据内不允许重复商品行；request_id 防重复提交。
- 盘点无差异时不保留空文档。
- 商品图片仅接受 JPEG/PNG/WebP，限制 6MB 和像素尺寸，验证内容后转为 640x640 JPEG；历史图片文件不自动删除。
- 低库存消息按商品+日期去重。
- 采购仍然是基于低库存商品的建议列表，不新增采购审批工作流。

## 6. 数据库与交付边界

- 无新增表、列、索引或 schema 迁移；schema 仍为 8。
- 不合并或覆盖根目录与 Release 两份数据库。
- 原始开发基线包只读；所有代码修改在 dev.9 独立工作副本进行。
- Windows EXE、真机和生产门店服务器不在当前 Linux 测试环境内，必须明确标记未测试。

## 7. 尚未完成

- 全站所有业务模块仍未迁出 `main.py`；排班/客户在 dev.10，消息/报表/运维在 dev.11。
- 全站 PC/手机独立生产布局及所有页面 sticky 仍在 dev.12。
- 权限动作目录的范围/字段权限和完整旧授权差异迁移工具仍需继续；本轮对 catalog/inventory/procurement 已接入的动作不等于全站权限已完成。
