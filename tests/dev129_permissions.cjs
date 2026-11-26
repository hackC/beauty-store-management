const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const html = fs.readFileSync(process.argv[2], "utf8");
const {catalog, templates, savedGroup} = JSON.parse(fs.readFileSync(0, "utf8"));
const nodes = new Map();
const $ = id => {
    if (!nodes.has(id)) nodes.set(id, {classList: {add() {}, remove() {}}});
    return nodes.get(id);
};
const requests = [];
let inputs = [];
const ctx = vm.createContext({
    $, console, catalog, templates, savedGroup, esc: String, loadUsers() {},
    document: {querySelectorAll() { return inputs; }},
    toast(message) { assert.match(message, /规则已保存/); },
    async api(path, options) {
        if (path === "/api/permissions/catalog") return catalog;
        if (path === "/api/permission-groups/templates") return templates;
        requests.push({path, ...JSON.parse(options.body)});
    },
});
const start = html.indexOf("let permissionDefinitions");
const end = html.indexOf("async function deletePermissionGroup", start);
vm.runInContext(html.slice(start, end) + "\npermissionGroupsCache = [savedGroup];", ctx);

(async () => {
    await vm.runInContext("loadPermissionCatalog()", ctx);
    vm.runInContext("openPermissionGroupModal(savedGroup.id)", ctx);
    const markup = $("permissionGroupPermissions").innerHTML;
    assert.equal((markup.match(/>查看商品档案<\/label>/g) || []).length, 1);
    assert.doesNotMatch(markup, /新增、编辑、停用商品|data-group-permission="products_manage"/);
    inputs = [...markup.matchAll(/data-group-permission="([^"]+)" (checked)?>/g)]
        .map(([, key, checked]) => ({dataset: {groupPermission: key}, checked: Boolean(checked)}));
    assert.equal(inputs.find(x => x.dataset.groupPermission === "ext.catalog.disable").checked, true);
    assert.equal(inputs.some(x => x.dataset.groupPermission === "backup_restore"), false);
    await vm.runInContext("savePermissionGroup()", ctx);
    assert.equal(requests[0].permissions.products_manage, true);
    inputs.find(x => x.dataset.groupPermission === "ext.catalog.disable").checked = false;
    await vm.runInContext("savePermissionGroup()", ctx);
    assert.equal(requests[1].permissions.products_manage, undefined);
    assert.equal(requests[1].permissions["ext.catalog.update"], true);
    vm.runInContext('applyGroupPreset("inventory")', ctx);
    assert.equal(inputs.find(x => x.dataset.groupPermission === "ext.catalog.disable").checked, true);
    console.log(JSON.stringify(requests[1]));
})().catch(error => { console.error(error); process.exitCode = 1; });
