// Records the narrated demo from the running app (uvicorn referee.server:app --port 8787).
//   npm i puppeteer-core            (Chrome and ffmpeg must be installed)
//   python scripts/video/voice.py   (synthesises the narration and writes lines.timed.json; needs `pip install edge-tts`)
//   node scripts/video/record_demo.mjs http://localhost:8787 main   -> take-main.webm  (replayed demo cases)
//   node scripts/video/record_demo.mjs http://localhost:8787 live   -> take-live.webm  (a live review: needs CLOCKED_API_KEY, spends credits)
//   node scripts/video/record_demo.mjs http://localhost:8787 end    -> take-end.webm
//   python scripts/video/mux.py demo.mp4                            (puts each take back on the clock and lays the narration at its marks)
// Each scene stays on screen for as long as its sentence takes to say. Captions are drawn in the page while it is recorded.
import puppeteer from 'puppeteer-core';
const base = process.argv[2] || 'http://localhost:8787';
const take = process.argv[3] || 'main';
const CASES = new URL('../../data/cases', import.meta.url).pathname.replace(/%20/g, ' ');
const wait = ms => new Promise(r => setTimeout(r, ms));
const fsp = await import('node:fs/promises');
const LINES = JSON.parse(await fsp.readFile(process.env.LINES || new URL('./lines.timed.json', import.meta.url), 'utf8'));
const marks = []; let T0 = 0;
const mark = id => marks.push([id, Date.now() - T0]);
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
const cap = async (id, pad = 550) => { const L = LINES[id]; await page.evaluate(t => { const c = document.getElementById('rec-cap'); (document.querySelector('dialog[open]') || document.body).append(c); c.textContent = t; c.classList.add('on'); }, L.cap); await wait(180); mark(id); await wait(L.dur + pad); };
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
  const rec = await page.screencast({ path: 'take-main.webm', ffmpegPath: 'ffmpeg' }); T0 = Date.now();
  await wait(500); mark('title'); await wait(LINES.title.dur + 500); await cardOff();
  await cap('s1');
  await click('.tab[data-tab="note"]');
  await cap('s2');
  await click('.tab[data-tab="changes"]'); await go('case=quiet-changes&change=c2'); await detailTop();
  await cap('s3');
  await cap('s4');
  await toSection('Playbook');
  await cap('s5');
  await toSection('Route');
  await cap('s6');
  await go('case=quiet-changes&change=c1'); await detailTop();
  await cap('s7');
  await go('case=quiet-changes&change=c3'); await detailTop(); await toSection('Route');
  await cap('s8');
  await go('case=quiet-changes&change=c5'); await detailTop();
  await cap('s9');
  await capOff(); await go('case=two-readers&change=c2'); await detailTop();
  await cap('s10');
  await capOff(); await go('case=instruction-in-note'); await click('.row:not([data-cid])');
  await cap('s11');
  await capOff(); await go('case=quiet-changes-injected'); await click('.row:not([data-cid])');
  await cap('s12');
  await capOff(); await go('case=wrong-file');
  await cap('s13');
  await capOff(); await go('case=quiet-changes&change=c2'); await click('.drawer-toggle'); await wait(700);
  await cap('s14');
  await click('.drawer-toggle'); await capOff();
  await go('case=quiet-changes&change=c1'); await detailTop();
  for (const key of ['a', 'j', 'c', 'j', 'e', 'j', 'e']) { await page.keyboard.press(key); await wait(520); }
  await click('#signoff-btn'); await wait(1400);
  await cap('s15');
  await page.evaluate(() => document.body.append(document.getElementById('rec-cap'))); await page.keyboard.press('Escape'); await wait(400);
  await page.evaluate(() => [...document.querySelectorAll('dialog[open] button')].find(b => /^(done|close)$/i.test(b.innerText.trim()))?.click()); await capOff();
  await go('case=clean-return'); await detailTop();
  await cap('s16');
  await capOff(); marks.push(['__end', Date.now() - T0]); await rec.stop();
}

if (take === 'live') {
  const rec = await page.screencast({ path: 'take-live.webm', ffmpegPath: 'ffmpeg' }); T0 = Date.now();
  await cap('l1', 200);
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
  await cap('l2');
  await capOff(); marks.push(['__end', Date.now() - T0]); await rec.stop();
}

if (take === 'end') {
  await card('<h1>Measured, not claimed</h1><div class="rc-row"><div class="stat"><b>30 / 30</b><span>planted edits held</span></div><div class="stat"><b>30 / 30</b><span>false statements caught</span></div><div class="stat"><b>0 / 41</b><span>cosmetic edits held</span></div><div class="stat"><b>15 / 15</b><span>final status correct</span></div></div><p style="margin-top:26px">Recorded answers of the live Clocked API against exact ground truth. Checked again on a second corpus built blind to the fixes: 8 of 8 statuses, 6 of 6 edits held. 991 tests.</p><small>github.com/Ian18858863/redline-referee</small>', 300);
  const rec = await page.screencast({ path: 'take-end.webm', ffmpegPath: 'ffmpeg' }); T0 = Date.now();
  await wait(600); mark('end'); await wait(LINES.end.dur + 1400); marks.push(['__end', Date.now() - T0]); await rec.stop();
}
await fsp.writeFile(`marks-${take}.json`, JSON.stringify(marks));
console.log('page errors:', errs.length, errs.slice(0, 3), 'marks', marks.length);
await browser.close();
