const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const page = fs.readFileSync(process.argv[2], 'utf8');
let choices = [];
let usedUrl, redirected, submitted;
const elements = {
    txSearch: {value: '护理'}, txType: {value: '全部'},
    txStart: {value: '2026-11-01'}, txEnd: {value: '2026-11-15'},
    excelExportModal: {classList: {add() {}, remove() {}}},
    excelTemplateChoice: {replaceChildren() {}, add() {}, focus() {}},
    excelFieldChoices: {
        set innerHTML(markup) {
            choices = [...markup.matchAll(/value="([^"]+)" (checked)?>/g)]
                .map(([, value, checked]) => ({value, checked: Boolean(checked)}));
        },
        querySelectorAll(selector) {
            return selector === 'input:checked' ? choices.filter(x => x.checked) : choices;
        },
    },
};
function $(id) { return elements[id] ??= {}; }
const ctx = vm.createContext({
    $, URLSearchParams, esc: String, Option: class {}, toast() {},
    async api(url, options) {
        if (url.endsWith('/options')) return {
            title: '库存流水',
            templates: [{id: 'default', name: '交班', is_default: true, fields: ['note']}],
            fields: [{key: 'id', label: '编号'}, {key: 'note', label: '备注'}],
        };
        submitted = {url, data: JSON.parse(options.body)};
        return {id: 'new', ...submitted.data};
    },
    async fetch(url) {
        usedUrl = url;
        return {ok: true, headers: {get: () => "attachment; filename*=utf-8''%E5%BA%93%E5%AD%98.xlsx"}, blob: async () => ({})};
    },
    URL: {createObjectURL: () => 'blob:export'},
    document: {body: {append() {}}, createElement: () => ({
        click() { redirected = this.download; }, remove() {},
    })},
    setTimeout() {},
});
const start = page.indexOf('let excelExportState = null;');
const end = page.indexOf('// ============================================================', start);
const run = code => vm.runInContext(code, ctx);
run(page.slice(start, end));
(async () => {
    await run("exportFile('transactions','入库')");
    assert.equal($('excelTemplateChoice').value, 'default');
    assert.equal(JSON.stringify(run('excelSelectedFields()')), '["note"]');
    await run('downloadExcelExport()');
    const url = new URL(usedUrl, 'https://testserver');
    assert.equal(url.searchParams.get('type'), '入库');
    assert.equal(url.searchParams.get('q'), '护理');
    assert.equal(url.searchParams.get('start'), '2026-11-01');
    assert.deepEqual(url.searchParams.getAll('fields'), ['note']);
    assert.equal(redirected, '库存.xlsx');
    run('selectAllExcelFields(true)');
    $('excelTemplateName').value = '完整交班';
    await run('saveExcelTemplate(true)');
    assert.deepEqual(submitted.data.fields, ['id', 'note']);
    assert.equal(submitted.data.is_default, true);
    assert.equal($('excelTemplateChoice').value, 'new');
})().catch(error => {console.error(error); process.exitCode = 1;});
