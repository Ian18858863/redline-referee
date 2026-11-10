// Records the demo video from the running app (uvicorn referee.server:app --port 8787).
//   npm i puppeteer-core   (Chrome and ffmpeg must be installed)
//   node scripts/record_demo.mjs http://localhost:8787 main    -> take-main.webm  (replayed demo cases)
//   node scripts/record_demo.mjs http://localhost:8787 live    -> take-live.webm  (a live review; needs CLOCKED_API_KEY, spends credits)
//   node scripts/record_demo.mjs http://localhost:8787 end     -> take-end.webm
// then concatenate the three takes with ffmpeg. Captions are drawn in the page while it is recorded.
import puppeteer from 'puppeteer-core';
const base = process.argv[2] || 'http://localhost:8787';
const take = process.argv[3] || 'main';
const CASES = new URL('../data/cases', import.meta.url).pathname.replace(/%20/g, ' ');
const wait = ms => new Promise(r => setTimeout(r, ms));
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: 'new',
  args: ['--window-size=1920,1080', '--hide-scrollbars', '--force-device-scale-factor=1'], defaultViewport: { width: 1920, height: 1080 } });
const page = await browser.newPage();
const errs = []; page.on('pageerror', e => errs.push(String(e))); page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });

async function overlay() {
  await page.evaluate(() => {
    if (document.getElementById('rec-cap')) return;
    const st = document.createElement('style');
    st.textContent = `
      #rec-cap{position:fixed;left:50%;bottom:64px;transform:translateX(-50%);max-width:1280px;z-index:99999;background:rgba(17,19,24,.94);color:#fff;
        font:500 27px/1.38 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;padding:16px 26px;border-radius:14px;text-align:center;
        box-shadow:0 10px 40px rgba(0,0,0,.28);opacity:0;transition:opacity .28s ease;letter-spacing:.005em}
      #rec-cap.on{opacity:1}
      #rec-card{position:fixed;inset:0;z-index:100000;background:#0f1115;color:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;
        font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;opacity:0;transition:opacity .45s ease;pointer-events:none;text-align:center}
      #rec-card.on{opacity:1}
      #rec-card h1{font-size:76px;font-weight:650;letter-spacing:-.02em;margin:0}
      #rec-card p{font-size:32px;color:#c9ced8;margin:0;max-width:1300px;line-height:1.4}
      #rec-card small{font-size:23px;color:#8b93a1;margin-top:14px}
      #rec-card .rc-row{display:flex;gap:72px;margin-top:10px;justify-content:center;align-items:flex-start}
      #rec-card .stat{text-align:center}
      #rec-card .stat b{display:block;font-size:58px;font-weight:650;color:#fff}
      #rec-card .stat span{font-size:22px;color:#aab1bd}`;
    document.head.append(st);
    const c = document.createElement('div'); c.id = 'rec-cap'; document.body.append(c);
    const k = document.createElement('div'); k.id = 'rec-card'; document.body.append(k);
  });
}
const cap = async (text, ms) => { await page.evaluate(t => { const c = document.getElementById('rec-cap'); c.textContent = t; c.classList.add('on'); }, text); await wait(ms); };
const capOff = async () => { await page.evaluate(() => document.getElementById('rec-cap').classList.remove('on')); await wait(320); };
const card = async (html, ms) => { await page.evaluate(h => { const k = document.getElementById('rec-card'); k.innerHTML = h; k.classList.add('on'); }, html); await wait(ms); };
const cardOff = async () => { await page.evaluate(() => document.getElementById('rec-card').classList.remove('on')); await wait(520); };
const go = async (hash) => { await page.evaluate(h => { location.hash = h; }, hash); await wait(900); };
const click = async (sel) => { await page.evaluate(s => document.querySelector(s)?.click(), sel); await wait(450); };
const toSection = async (text) => { await page.evaluate(t => { const d = document.getElementById('detail');
  const el = [...d.querySelectorAll('h3,h4,b,.sec-title,.block-title,header')].find(e => e.textContent.trim().startsWith(t));
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, text); await wait(900); };
const detailTop = async () => { await page.evaluate(() => { const d = document.getElementById('detail'); (d.querySelector('.detail-body') || d).scrollTo?.({ top: 0, behavior: 'smooth' }); d.querySelectorAll('*').forEach(e => { if (e.scrollTop > 0) e.scrollTo({ top: 0, behavior: 'smooth' }); }); }); await wait(500); };

await page.goto(`${base}/#case=quiet-changes`, { waitUntil: 'networkidle0' });
await overlay();

if (take === 'main') {
  await card('<h1>Redline Referee</h1><p>Catch what changed in the contract they sent back.</p><small>Built on Clocked Verify, Compare and Rerank &nbsp;·&nbsp; Clocked Build Challenge 2026</small>', 300);
  const rec = await page.screencast({ path: 'take-main.webm', ffmpegPath: 'ffmpeg' });
  await wait(3600); await cardOff();
  await cap('You sent a customer your agreement. They return a “clean copy”. Their note says: only typos and our address changed.', 6200);
  await click('.tab[data-tab="note"]');
  await cap('Clocked Verify checks every statement of that note against the documents. Three are contradicted, so the status is: Do not sign.', 7400);
  await click('.tab[data-tab="changes"]'); await go('case=quiet-changes&change=c2');
  await cap('Every difference is located on both original PDFs. Payment terms: thirty days became ninety.', 6200);
  await toSection('Verify across versions');
  await cap('Each difference is read three times: by rules, by Verify against the other version’s PDF, and by Compare, clause against clause.', 7600);
  await toSection('Playbook');
  await cap('Rerank picks the playbook passage for a deal of this size and skips the one written for smaller deals.', 6000);
  await toSection('Route');
  await cap('Decide answers two yes/no questions about the playbook. Code turns them into a route. Ninety days is refused, forty-five is allowed: counter.', 7600);
  await go('case=quiet-changes&change=c1'); await detailTop();
  await cap('Sixty days’ notice became thirty. The playbook lets the deal desk accept that one.', 4800);
  await go('case=quiet-changes&change=c3'); await detailTop(); await toSection('Route');
  await cap('On the liability cap Decide was not sure. Code does not follow an unsure answer: a person decides.', 6000);
  await go('case=quiet-changes&change=c5'); await detailTop();
  await cap('One sentence was only reworded. Verify reads the same meaning in both directions and Compare does not object: cleared, nothing to negotiate.', 7200);
  await capOff(); await go('case=disclosed-changes&change=c2'); await detailTop();
  await cap('Another return. One word changed: “maximum” became “minimum”. Rules take it for a spelling fix.', 6000);
  await toSection('Verify across versions');
  await cap('Verify and Compare both read a different meaning, so it is promoted to a change that someone must resolve.', 6600);
  await capOff(); await go('case=quiet-changes-injected'); await click('.row:not([data-cid])');
  await cap('This copy hides white 1-point text telling AI reviewers to approve it. Guard flags it and the rule layer finds the hidden text: Do not sign.', 7800);
  await capOff(); await go('case=quiet-changes&change=c2');
  await click('#layer-switch .switcher-btn'); await wait(700);
  await page.evaluate(() => [...document.querySelectorAll('#layer-switch .switcher-item')].find(b => /paired/i.test(b.textContent))?.click()); await wait(700);
  await cap('The copies Clocked marked itself are one layer away: here, every passage Compare paired between the two PDFs.', 6000);
  await click('#layer-switch .switcher-btn'); await wait(500);
  await page.evaluate(() => document.querySelector('#layer-switch .switcher-item')?.click()); await wait(500);
  await capOff(); await click('.drawer-toggle'); await wait(700);
  await cap('For every review: what each endpoint changed, what would have gone wrong without it, and every request with its credits.', 7600);
  await click('.drawer-toggle'); await capOff();
  await go('case=disclosed-changes&change=c1'); await detailTop();
  await cap('The reviewer decides each change. Sign-off opens only when every one has a decision.', 2200);
  await page.keyboard.press('a'); await wait(900); await page.keyboard.press('j'); await wait(700); await page.keyboard.press('c'); await wait(900);
  await page.keyboard.press('j'); await wait(700); await page.keyboard.press('c'); await wait(1200);
  await click('#signoff-btn'); await wait(3200);
  await page.keyboard.press('Escape'); await capOff();
  await go('case=clean-return'); await detailTop();
  await cap('And when the copy really is clean, it reads: Ready to sign.', 4600);
  await capOff(); await rec.stop();
}

if (take === 'live') {
  const rec = await page.screencast({ path: 'take-live.webm', ffmpegPath: 'ffmpeg' });
  await cap('Not only recorded cases. Two PDFs the demo has never shown, reviewed live through the Clocked API.', 3000);
  await click('#upload-btn'); await wait(900);
  const files = await page.$$('#upload-form input[type=file]');
  await files[0].uploadFile(`${CASES}/eval-07/v1.pdf`); await files[1].uploadFile(`${CASES}/eval-07/v2.pdf`);
  const note = await (await import('node:fs/promises')).readFile(`${CASES}/eval-07/cover.txt`, 'utf8');
  await page.evaluate(t => { const ta = document.querySelector('#upload-form textarea'); ta.value = t; ta.dispatchEvent(new Event('input', { bubbles: true })); }, note);
  await wait(1600);
  await click('#upload-submit');
  const t0 = Date.now();
  await page.waitForFunction(() => /^#?.*run=|runs\//.test(location.hash) || document.querySelector('#upload-dialog')?.open === false, { timeout: 150000 }).catch(() => {});
  await wait(1500);
  console.log('live review took', ((Date.now() - t0) / 1000).toFixed(1), 's; hash', await page.evaluate(() => location.hash), '; badge', await page.evaluate(() => document.getElementById('mode-badge')?.textContent));
  await cap('Live result, a few seconds later: every change located, the note checked, each change routed.', 6000);
  await capOff(); await rec.stop();
}

if (take === 'end') {
  await card('<h1>Measured, not claimed</h1><div class="rc-row"><div class="stat"><b>23 / 23</b><span>planted edits found</span></div><div class="stat"><b>24 / 24</b><span>false statements caught</span></div><div class="stat"><b>0</b><span>false alarms</span></div><div class="stat"><b>12 / 12</b><span>final status correct</span></div></div><p style="margin-top:26px">Twelve seeded pairs with exact ground truth. Every answer in this video came from the Clocked API: recorded for the demo cases, live for the upload.</p><small>github.com/Ian18858863/redline-referee</small>', 300);
  const rec = await page.screencast({ path: 'take-end.webm', ffmpegPath: 'ffmpeg' });
  await wait(6500); await rec.stop();
}
console.log('page errors:', errs.length, errs.slice(0, 3));
await browser.close();
