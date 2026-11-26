const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = require('node:fs').readFileSync(process.argv[2], 'utf8');
assert.match(html, /<form id="loginPane"/);
assert.doesNotMatch(html,/id="loginActivationCode"/);
const elements = {
  loginUsername: {value:'staff01'}, loginPassword: {value:'01234567'},
  authError: {}, loginPane: {querySelector: () => button, addEventListener(type, fn) {
    assert.equal(type, 'submit'); submit = fn;
  }},
};
const button = {disabled:false};
let submit, verification, feedback, rejected=false;
const context = vm.createContext({
  $: id => elements[id],
  showAuthPane: (pane, message) => feedback = {pane,message},
  authInit: async options => {verification=options.requireSession;},
  api: async (url, opt) => {
    assert.equal(button.disabled,true);
    assert.equal(elements.authError.textContent,'正在登录…');
    if(url==='/api/auth/cookie-probe') return {probe:'probe-token'};
    assert.equal(url,'/api/auth/login');
    assert.deepEqual(JSON.parse(opt.body),{username:'staff01',password:'01234567',cookie_probe:'probe-token',remember_browser:false});
    if (rejected) throw Error('首次登录码错误或已失效');
    return {must_set_password:true};
  }
});
vm.runInContext(html.slice(html.indexOf('let authSubmissionPending'),html.indexOf('async function doSetFirstPassword')) +
  html.match(/\$\("loginPane"\)\.addEventListener\("submit", event => \{[\s\S]*?\n\}\);/)[0],context);
(async()=>{
  let prevented=false;
  submit({preventDefault() {prevented=true;}});
  await new Promise(setImmediate);
  assert.equal(prevented,true); assert.equal(verification,'first');
  assert.equal(button.disabled,false);
  elements.loginPassword.value='01234567'; rejected=true;
  await vm.runInContext('doLogin()', context);
  assert.equal(button.disabled,false);
  assert.equal(feedback.message,'首次登录码错误或已失效');
  console.log('PASS: login form submit, pending state, password field activation code, first-password session, visible error');
})().catch(e=>{console.error(e);process.exitCode=1});
