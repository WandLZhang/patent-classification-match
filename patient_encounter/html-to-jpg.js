const puppeteer = require('puppeteer');

async function convertHtmlToJpeg() {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();
  await page.goto(`file://${__dirname}/patient_encounter.html`, {waitUntil: 'networkidle2'});
  await page.screenshot({path: 'patient_encounter.jpg', type: 'jpeg', quality: 80, fullPage: true});
  await browser.close();
  console.log('patient_encounter.jpg has been created');
}

convertHtmlToJpeg();
