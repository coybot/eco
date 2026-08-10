// Uses Puppeteer's own Chrome for Testing build, not the machine's managed
// Chrome (whose enterprise policy/GCM startup hangs a headless run).
const puppeteer = require("puppeteer");
const fs = require("fs");
const path = require("path");

const HTML = "file://" + path.join(__dirname, "scenes.html");
const FPS = 20;

// each scene's master loop length in seconds
const SCENES = [
  { id: "scene1", secs: 8 },
  { id: "scene2", secs: 7 },
  { id: "scene3", secs: 14 },
];

(async () => {
  const browser = await puppeteer.launch({
    headless: true,
    args: ["--force-color-profile=srgb", "--disable-lcd-text"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 900, height: 1200, deviceScaleFactor: 2 });
  await page.goto(HTML, { waitUntil: "networkidle0" });
  await page.evaluate(() => window.__ready);

  for (const { id, secs } of SCENES) {
    const outDir = path.join(__dirname, "frames", id);
    fs.rmSync(outDir, { recursive: true, force: true });
    fs.mkdirSync(outDir, { recursive: true });

    const el = await page.$("#" + id);
    const total = secs * FPS;
    for (let i = 0; i < total; i++) {
      const t = (i / FPS) * 1000;
      await page.evaluate((ms) => window.__seek(ms), t);
      await el.screenshot({
        path: path.join(outDir, String(i).padStart(4, "0") + ".png"),
      });
    }
    console.log(`${id}: ${total} frames @ ${FPS}fps (${secs}s loop)`);
  }

  await browser.close();
})();
