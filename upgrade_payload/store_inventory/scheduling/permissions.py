"""DEV10 action catalogue and legacy compatibility grants."""

MODULES = {
    'scheduling': ('排班', {'read':'查看每日排班', 'initialize':'初始化排班',
        'shift_update':'调整全店班次与休息', 'shift_update_self_today':'调整本人当天班次与休息',
        'rules_update':'调整基础档案与全局规则', 'workload_read_all':'查看全店工作负荷',
        'workload_read_self':'查看本人工作负荷'}),
    'appointments': ('预约', {'read':'查看全店预约', 'read_self':'查看本人预约与班次', 'create':'新增预约', 'update':'编辑预约',
        'delete':'删除预约', 'status_change':'变更预约状态', 'transfer':'转交预约',
        'create_self_today':'新增本人当天预约', 'update_self_today':'编辑本人当天预约',
        'delete_self_today':'删除本人当天预约', 'status_change_self_today':'变更本人当天预约状态',
        'contact_read':'查看预约联系方式与备注', 'contact_edit':'编辑预约联系方式与备注', 'export':'导出预约日报与备份'}),
    'schedule_requests': ('排班申请', {'read_all':'查看全部申请','read_self':'查看本人申请',
        'create_self':'提交本人申请','review':'审批申请'}),
    'customers': ('客户档案', {'read':'查看客户','create':'新增客户','update':'编辑客户','delete':'删除客户',
        'phone_read':'查看客户联系方式','phone_edit':'编辑客户联系方式','notes_read':'查看客户备注','notes_edit':'编辑客户备注'}),
    'waitlist': ('候补预约', {'read':'查看候补','create':'登记候补','status_change':'变更候补状态',
        'phone_read':'查看候补联系方式','phone_edit':'登记候补联系方式','notes_read':'查看候补备注','notes_edit':'登记候补备注'}),
}

def keys(namespace, *actions):
    return tuple('ext.' + namespace + '.' + a for a in actions)

def all_keys(namespace):
    return keys(namespace, *MODULES[namespace][1])

SCHEDULE_READ = keys('scheduling','read','workload_read_self') + keys('appointments','read','contact_read','export') + keys('schedule_requests','read_self')
LEGACY_SCHEDULE_ACTIONS = {
    'schedule_view': SCHEDULE_READ,
    'schedule_self_manage': SCHEDULE_READ + keys('scheduling','shift_update_self_today') + keys('appointments','create_self_today','update_self_today','delete_self_today','status_change_self_today','contact_edit') + keys('schedule_requests','create_self'),
    'schedule_manage': all_keys('scheduling') + tuple(key for key in all_keys('appointments') if key != 'ext.appointments.read_self') + all_keys('schedule_requests') + all_keys('waitlist'),
    'schedule_approve': keys('schedule_requests','read_all','review'),
    'customers_view': keys('customers','read','phone_read','notes_read'),
    'customers_manage': all_keys('customers'),
    'waitlist_manage': all_keys('waitlist'),
}

STATE_WRITE_KEYS = keys('scheduling','shift_update','shift_update_self_today','rules_update') + keys('appointments','create','update','delete','status_change','create_self_today','update_self_today','delete_self_today','status_change_self_today')
