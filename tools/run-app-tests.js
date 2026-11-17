// Replays app/tests.json (CloudCrane's test format) against app/index.html in Chromium.
// The CloudCrane UI kit isn't available outside CloudCrane, so a small stand-in provides
// tabs (role="tab"), pushed screens with a back button, and a fixed tab bar that can
// cover content, as the real kit's does.
//   node tools/run-app-tests.js [T-1 T-2 ...]
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
const ROOT = path.join(__dirname, '..');
const APP = fs.readFileSync(process.env.APP || path.join(ROOT, 'app/index.html'));
// Data tests: setup runs in the version phones have now (APP_PREV), check in the new one.
const PREV = process.env.APP_PREV ? fs.readFileSync(process.env.APP_PREV) : null;
const TESTS = JSON.parse(fs.readFileSync(path.join(ROOT, 'app/tests.json'), 'utf8'));
const only = process.argv.slice(2);

const KIT_CSS = `
.cc-tabbar { position: fixed; left: 0; right: 0; bottom: 0; height: 83px; display: flex; background: #f9f9fb; border-top: 1px solid #ddd; z-index: 10; }
.cc-tabbar button { flex: 1; }
.cc-tabbar[hidden] { display: none; }
.cc-navbar { display: flex; align-items: center; padding: 8px 16px; min-height: 44px; }
.cc-btn { min-height: 44px; padding: 0 16px; }
.cc-empty, .cc-error { display: flex; flex-direction: column; align-items: center; gap: 8px; text-align: center; }`;
const KIT_JS = `
(function () {
  var stack = [];
  function screens() { return document.querySelectorAll('main.cc-screen'); }
  function show(id) { screens().forEach(function (m) { m.hidden = m.id !== id; }); }
  // Like the kit, screen changes animate: the screen being left stays on the page until the
  // next animation frame, which a frozen test clock only runs on an advance step.
  function showAnimated(id) {
    var target = document.getElementById(id); target.hidden = false;
    requestAnimationFrame(function () { show(id); });
  }
  window.ccUI = {
    tabs: function () { return { show: function (id) { stack = []; showAnimated(id); document.querySelector('.cc-tabbar').hidden = false; } }; },
    // A pushed screen covers the one underneath, which stays on the page but inert.
    push: function (id) {
      var cur = [].filter.call(screens(), function (m) { return !m.hidden; })[0];
      stack.push(cur && cur.id);
      var t = document.getElementById(id); t.hidden = false; t.style.cssText = 'position:fixed;inset:0;overflow:auto;z-index:5;background:#fff';
      if (cur) cur.inert = true;
      document.querySelector('.cc-tabbar').hidden = true;
    },
    pop: function () {
      var top = [].filter.call(screens(), function (m) { return !m.hidden && !m.inert; }).pop();
      var back = stack.pop();
      if (top) { top.style.cssText = ''; requestAnimationFrame(function () { top.hidden = true; }); }
      if (back) { var b = document.getElementById(back); b.inert = false; b.hidden = false; }
      if (!stack.length) document.querySelector('.cc-tabbar').hidden = false;
    },
    toast: function () {}, swipe: function () {}, menu: function () {}
  };
  document.querySelectorAll('[data-cc-tab]').forEach(function (b) { b.setAttribute('role', 'tab'); });
  // Like the kit, switch tabs early (capture phase), before the app's own handlers run.
  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-cc-tab]'); if (t) window.ccUI.tabs().show(t.getAttribute('data-cc-tab'));
    var b = e.target.closest('[data-cc-back]'); if (b) window.ccUI.pop();
  }, true);
})();`;

function locate(page, step) {
  if (step.role) return page.getByRole(step.role, { name: step.name, exact: true });
  if (step.label) return page.getByLabel(step.label, { exact: true }).locator('visible=true');
  return page.getByText(step.text, { exact: false }).locator('visible=true');
}

async function runSteps(page, steps) {
  for (let i = 0; i < steps.length; i++) {
    const s = steps[i], at = `step ${i + 1} ${JSON.stringify(s)}`;
    try {
      if (s.do === 'tap') {
        const loc = locate(page, s);
        const n = await loc.count();
        if (n > 1) throw new Error(`${n} visible elements match`);
        // CloudCrane's runner doesn't scroll to what it taps: it must already be on screen.
        await loc.first().waitFor({ timeout: 3000 });
        const box = await loc.first().boundingBox();
        const inBar = await loc.first().evaluate(el => !!el.closest('.cc-tabbar'));
        const bar = !inBar && await page.locator('.cc-tabbar:not([hidden])').count() ? 83 : 0;
        if (box && box.y + box.height / 2 > 844 - bar) throw new Error(`below the visible screen (y=${Math.round(box.y)})`);
        await loc.first().click({ timeout: 3000 });
      }
      else if (s.do === 'type') await page.getByLabel(s.label, { exact: true }).locator('visible=true').first().fill(s.value, { timeout: 3000 });
      else if (s.do === 'wait') await page.waitForTimeout(s.ms);
      else if (s.do === 'advance') await page.clock.runFor(s.ms);
      else if (s.do === 'reload') await page.reload();
      else if (s.expect === 'see') await page.getByText(s.text, { exact: false }).locator('visible=true').first().waitFor({ timeout: 3000 });
      else if (s.expect === 'notSee') { await page.waitForTimeout(200); if (await page.getByText(s.text).locator('visible=true').count()) throw new Error('still visible'); }
      else if (s.expect === 'state') {
        const got = await page.evaluate(p => p.split('.').reduce((o, k) => o == null ? o : o[k],
          typeof window.appState === 'function' ? window.appState() : window.appState), s.path);
        if (JSON.stringify(got) !== JSON.stringify(s.equals)) throw new Error(`state ${s.path} = ${JSON.stringify(got)}`);
      } else throw new Error('unsupported step');
    } catch (e) { throw new Error(`${at}: ${e.message.split('\n')[0]}`); }
  }
}

(async () => {
  const browser = await chromium.launch();
  let failed = 0;
  for (const t of TESTS) {
    if (only.length && !only.includes(t.id)) continue;
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await ctx.newPage();
    const errors = [];
    let served = (t.kind === 'data' && PREV) ? PREV : APP;
    page.on('pageerror', e => errors.push(e.message));
    // CloudCrane's tests freeze time: timers and animation frames only run on advance steps.
    if (t.clock) { await page.clock.install({ time: new Date(Date.parse(t.clock) - 1000) }); await page.clock.pauseAt(new Date(t.clock)); }
    await page.route('**/*', route => {
      const u = new URL(route.request().url());
      if (u.host === 'app.test') {
        if (u.pathname === '/') return route.fulfill({ body: served, contentType: 'text/html' });
        if (u.pathname.endsWith('ccui-1.0.0.css')) return route.fulfill({ body: KIT_CSS, contentType: 'text/css' });
        if (u.pathname.endsWith('ccui-1.0.0.js')) return route.fulfill({ body: KIT_JS, contentType: 'text/javascript' });
        return route.fulfill({ body: '', contentType: 'text/javascript' });
      }
      const r = (t.responses || []).find(x => x.site === u.host && (!x.path || u.pathname.startsWith(x.path)));
      if (r) return route.fulfill({ status: r.status || 200, body: typeof r.body === 'string' ? r.body : JSON.stringify(r.body),
        contentType: 'application/json', headers: { 'access-control-allow-origin': '*' } });
      return route.abort();
    });
    try {
      await page.goto('https://app.test/');
      if (t.kind === 'data') { await runSteps(page, t.setup); served = APP; await page.reload(); await runSteps(page, t.check); }
      else await runSteps(page, t.steps);
      if (errors.length) throw new Error('page errors: ' + errors.join('; '));
      console.log(`PASS ${t.id} ${t.title}`);
    } catch (e) {
      failed++;
      console.log(`FAIL ${t.id} ${t.title}\n     ${e.message}`);
      await page.screenshot({ path: path.join(process.env.SHOTS || '/tmp', `${t.id}.png`) }).catch(() => {});
    }
    await ctx.close();
  }
  await browser.close();
  process.exit(failed ? 1 : 0);
})();
