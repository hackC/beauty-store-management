# DEV10 排班、客户与候补模块架构

HTTP 门面仍由 `main.py` 安装，组合入口注入 SQLite 连接工厂、审计写入器和客户档案同步接口。`store_inventory/scheduling/policy.py` 实现纯 Python 动作与本人当天范围校验，`rules.py` 实现业务数据与时间冲突验证，`permissions.py` 是动作目录与旧权限映射。`repository.py` 声明排班、客户、候补操作协议和统一事务边界；具体 SQLite 适配器分别放在 `scheduling/sqlite.py`、`customers/sqlite.py`、`waitlist/sqlite.py`。转单与审批事务中的业务处理仍在排班适配器内，本阶段未进一步拆成完全独立的领域命令服务。

## 接口及边界

| 接口 | 控制 |
|---|---|
| GET /api/schedule/state | 排班或预约 read；没有预约 read 返回空预约/分组；联系方式与备注单独过滤 |
| POST /api/schedule/migrate | initialize；实际初始写入仍检查每一类动作 |
| PUT /api/schedule/state | 在 BEGIN IMMEDIATE 后读取旧状态、校验 revision，逐预约新增、编辑、删除、状态、转交、分组、班次、休息、其他全局字段分别鉴权；任一失败整体回滚 |
| PATCH /api/schedule/appointments/{id}/status | 独立状态命令；全店或本人当天；revision 和恢复冲突保护 |
| POST /api/schedule/appointments/transfer | transfer；原有工作时段与重叠校验、拆分、历史、审计和 revision 保留 |
| GET /api/schedule/transfers | 预约 read，未授权联系方式时移除 detail_json |
| /api/schedule/staff-links | 管理员专属 |
| GET /api/schedule/workload | workload_read_all 或 workload_read_self，服务端读取绑定 |
| /api/schedule/requests | read_all/read_self、create_self、review 分离；提交申请时绑定校验；批准后原子落实至排班 |
| /api/customers | read/create/update/delete；存在性在事务内判断；联系方式和备注读写分离，未授权时搜索不匹配尾号；未提交字段保留 |
| /api/schedule/waitlist | read/create/status_change；联系方式和备注单独读写 |

旧 `schedule_manage` 扩展为全店排班、预约、申请和候补动作；`schedule_self_manage` 映射为本人当天动作并保留旧排班读取范围；`schedule_view` 对应只读及旧界面本地导出，`schedule_approve` 对应审批与查询全部申请。客户和候补旧管理权限按旧能力扩展。新规则组可以只授权独立动作，不需授予旧粗粒度权限。用户或组已有的旧权限仍会自动扩展，因此撤销新动作之前需检查旧授权来源。

现阶段范围支持全店或登录账号绑定的本人，修改本人预约和班次仅当天。任意指定的多个员工范围暂未引入数据库授权结构（需要后续设计选择）；不宣称已覆盖任意指定人员条件。报表、备份、审计及实时消息的全平台联动权限将在 DEV11 完成验收。旧数据库不变，schema=8。

