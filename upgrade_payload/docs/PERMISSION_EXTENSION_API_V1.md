# StoreInventory V1.6 权限扩展接口（v1）

## 目标和范围

本接口用于**未来网页模块可复用的权限扩展能力**，dev.6 已完成后端权限模块分层；不改变现有库存、采购、每日排班的业务数据或 API 语义。现有粗粒度权限（如 `products_manage`、`schedule_manage`）仍然沿用；既有业务全部改为增删改查独立校验属于下一轮工作，不应将本轮接口扩展误认为该项已完成。

设计特点：权限名称与网页模块解耦、后端策略默认拒绝、管理员通过现有权限组分配、前端从服务端动态获得目录、旧权限键兼容、无需数据库结构迁移。

## 1. 对外的稳定 HTTP API

所有接口需要同源登录 Cookie，且用户必须完成首次密码设置。它们不接受被查询用户 ID，不允许自助授权。

| 请求 | 路径 | 用途 |
|---|---|---|
| GET | `/api/permissions/catalog` | 返回模块及动作目录，`schema_version: 1` |
| GET | `/api/permissions/me` | 返回当前登录账号的**有效权限**，含所属规则组 ID |
| POST | `/api/permissions/check` | 校验当前登录账号是否满足一组权限，`all` / `any` |

`GET /api/permissions/catalog` 返回示例（简化）：

```json
{
  "schema_version": 1,
  "modules": [
    {
      "id": "loyalty", "label": "会员管理", "source": "extension",
      "permissions": [
        {"key": "ext.loyalty.view", "label": "查看会员", "action": "view", "group_assignable": true},
        {"key": "ext.loyalty.create", "label": "新增会员", "action": "create", "group_assignable": true}
      ]
    }
  ]
}
```

实际返回同时包含内置 `core` 模块的旧权限定义以及已注册的扩展模块。

`POST /api/permissions/check` 请求示例：

```json
{"keys":["ext.loyalty.view","ext.loyalty.create"],"mode":"all"}
```

返回示例：

```json
{"schema_version":1,"allowed":false,"mode":"all","results":{"ext.loyalty.view":true,"ext.loyalty.create":false}}
```

- `mode="all"`：所列权限全部满足才返回 `allowed=true`。
- `mode="any"`：任一项满足即可。
- 未注册的权限键一律视为 `false`，即使当前账号是管理员也不能通过不存在的权限名。
- 只查询**当前会话**。客户端携带他人 ID 不会令接口查询他人权限。
- 响应带 `Cache-Control: no-store`，权限组变更后下一次请求按数据库实时计算。

## 2. 可信服务端注册模块

dev.6 起，新模块通过参数接收 `ModulePermissions` 接口，不再反向导入 `main.py`。由可信组合入口在应用开始接受请求前调用安装函数；普通员工不能从 HTTP 注册权限。

```python
from fastapi import APIRouter, FastAPI, Request
from store_inventory.permissions.ports import ModulePermissions
from store_inventory.permissions.http import require_permissions


def install(app: FastAPI, permissions: ModulePermissions):
    permissions.register_module("loyalty", "会员管理", {
        "view": "查看会员", "create": "新增会员", "delete": "删除会员"
    })
    permissions.register_route(
        "loyalty", "GET", "/api/extensions/loyalty/records", "view"
    )
    router = APIRouter()

    @router.get("/api/extensions/loyalty/records")
    def list_members(request: Request):
        require_permissions(permissions, request, "ext.loyalty.view")
        return {"items": []}  # 示例；实际查询应注入业务服务。

    app.include_router(router)
```

组合入口在创建 `PERMISSION_SERVICE` 和 `app` 后调用 `install(app, PERMISSION_SERVICE)`。可运行示例见 `examples/loyalty_module.py`，默认没有挂载或启用会员功能。

旧模块仍可调用 `main.register_permission_module`、`main.register_permission_route`、`main.require_permission` 和 `main.permission_granted`；它们现在转发到权限服务。不要直接写入 `permission_extension.REGISTRY`，它不是应用使用的注册实例。完整分层及依赖约定见 `ARCHITECTURE_V1_6.md`。

**必须同时注册模块权限、HTTP 路由策略以及实际 FastAPI 接口**。仅登记权限名不会自动创建业务 API、数据库表或页面。注册路径限制在 `/api/extensions/<模块名>/...`。路由策略遗漏时，扩展接口默认返回 403（包括管理员）；与已有授权模式重叠的路由也会被拒绝注册。

`admin_only=("delete",)` 表示指定动作不允许普通权限规则组授予，且非管理员即使在旧用户权限 JSON 中存在同名键，也不能通过该动作检查。

**权限命名约定**：模块 ID / 动作采用小写英文字母、数字、下划线，前缀为 `ext.`；例如 `ext.loyalty.view`。新增页面可使用 `view/create/update/delete/approve/export` 等独立动作，也可以按业务创建 `refund`、`publish` 等动作。

### 实时规则和兼容性

新模块权限在现有 `permission_groups.permissions` JSON 中保存，员工仍通过规则组成员关系获得授权，支持多组并集。未引入新的数据表或 schema 版本。

如果将来某个扩展暂时从服务端移除，其权限键将不再有效；编辑现有权限组时会保留原来保存的**未注册权限键**，避免静默丢失历史配置。重新引入相同权限键后，原先授权可以恢复。因此，停用敏感业务前应由管理员主动撤销有关权限组授权。

## 3. 新网页前端的统一 SDK

在同源网页中引入：

```html
<script src="/static/permission_client.js"></script>
```

在需要时调用：

```javascript
const catalog = await StorePermissions.catalog();
await StorePermissions.refresh();

// 仅控制页面展示，不作为服务端安全判断。
addButton.hidden = !StorePermissions.can("ext.loyalty.create");
exportButton.hidden = !StorePermissions.can("ext.loyalty.export");

// 关键动作提交前可再次远端检查，最终的权限约束仍由后端保证。
const result = await StorePermissions.check(
  ["ext.loyalty.view", "ext.loyalty.update"], "all"
);
if (!result.allowed) throw new Error("没有操作权限");
```

`StorePermissions` 提供 `catalog()`、`refresh()`、`can(key)`、`canAll(keys)`、`canAny(keys)`、`check(keys,mode)`、`clear()`。前端缓存可能滞后，因此**任何写操作必须由服务端路由保护**。

现有门店系统“人员管理 → 权限规则组”编辑器已改为读取 `/api/permissions/catalog`，未来模块注册后自动出现新的权限勾选项。权限目录无法加载时，页面会禁止编辑/保存权限组，避免在网络故障时清空未知权限。

## 4. 安全约束和验收

- `POST /api/permissions/check` **不是授权接口**，不会修改账号或权限组。
- 所有权限写入仍需管理员调用现有 `/api/permission-groups` 管理端点。
- 未配置授权策略的扩展路由拒绝调用；不能用前端 `can()` 的结果替代服务端校验。
- 同名模块重复注册、非法动作名、跨模块路由或匹配范围冲突均拒绝。
- 未知权限不自动授予，管理专属权限不允许通过普通规则组赋予。
- 无需数据库 schema 迁移；升级前仍需独立备份现有数据库/配置/图片/采购附件。

测试覆盖匿名访问拒绝、注册和目录、独立动作授权、权限组分配与即时撤销、敏感权限拒绝、未配置路由 403、动态模块暂时移除时旧权限保留、前端 SDK 接口存在性。真机、Windows 打包和门店生产环境部署尚未验收。
