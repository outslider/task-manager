/* ヘルプ画面の図（16:9、3840×2160）を書き出す。
 *
 *   cd tools/help-slides && npm install && node render.js
 *
 * slide1.js（関係の全体図）と slide2.js（役割ごとの権限）の HTML をブラウザで開き、
 * static/img/help/ に PNG で保存する。機能や権限を変えたら、中身を直して作り直す。
 */
const path = require('path');
const puppeteer = require('puppeteer');

const OUT = path.join(__dirname, '..', '..', 'static', 'img', 'help');
const SLIDES = [['slide1', 'relationships.png'], ['slide2', 'roles.png']];

(async () => {
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox', '--font-render-hinting=none'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 2 });
  for (const [name, file] of SLIDES) {
    await page.setContent(require(`./${name}.js`), { waitUntil: 'load' });
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: path.join(OUT, file) });
    console.log('wrote', path.join('static/img/help', file));
  }
  await browser.close();
})();
