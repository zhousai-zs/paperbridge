/* Browser regression against a generated, isolated library. No inference models needed. */
const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const python = process.env.PAPERBRIDGE_PYTHON || (process.platform==='win32'?path.join(root,'.venv','Scripts','python.exe'):'python');

async function main() {
  const reservation=net.createServer();
  await new Promise(resolve=>reservation.listen(0,'127.0.0.1',resolve));
  const port=reservation.address().port;
  await new Promise(resolve=>reservation.close(resolve));
  const server=spawn(python,[path.join(__dirname,'seed_workbench.py'),'--port',String(port)],{cwd:root,env:{...process.env,PYTHONUTF8:'1'},windowsHide:true});
  let stdout='',stderr='',browser;
  server.stdout.on('data',data=>stdout+=data.toString());
  server.stderr.on('data',data=>stderr+=data.toString());
  const url=`http://127.0.0.1:${port}`;
  try {
    let ready=false;
    for(let i=0;i<60;i++) {
      if(server.exitCode!==null) throw new Error(`Test server stopped: ${stderr}`);
      try {if((await fetch(url)).ok){ready=true;break;}} catch{}
      await new Promise(resolve=>setTimeout(resolve,250));
    }
    assert(ready,`Test server did not start: ${stderr}`);
    const source=JSON.parse(stdout.trim().split('\n')[0]).source;
    browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
      args:['--disable-crash-reporter','--disable-breakpad']});
    const context=await browser.newContext({viewport:{width:1440,height:1080},acceptDownloads:true});
    const page=await context.newPage();
    const errors=[], external=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(!request.url().startsWith(url)&&!request.url().startsWith('blob:')) external.push(request.url());});
    await page.goto(url);
    await page.locator('.document-card').waitFor();
    assert.equal(await page.locator('#stat-docs').innerText(),'1');
    await page.locator('#file-input').setInputFiles(source);
    assert.match(await page.locator('#selected-files').innerText(),/source.pdf/);
    await page.locator('#pages').fill('1,3');
    await page.locator('#start-button').click();
    await page.locator('.task-card').waitFor();
    assert.match(await page.locator('.task-card').innerText(),/等待转换/);
    await page.screenshot({path:path.join(root,'.cache/ui-checks/desktop.png'),fullPage:true});
    let failFourthPage=true;
    await page.route(/\/api\/documents\/[^/]+\/pages\/4$/,async route=>{
      if(route.request().method()==='GET'&&failFourthPage){failFourthPage=false;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'模拟临时加载失败'})});}
      else await route.continue();
    });
    await page.locator('.document-card [data-open]').click();
    await page.locator('[data-page-markdown="1"] table').waitFor();
    assert.equal(await page.locator('[data-page-markdown="1"] math').count(),1);
    assert(await page.locator('[data-reading-page="1"] .flow-source img').evaluate(img=>img.complete&&img.naturalWidth>0));
    assert.equal(await page.locator('[data-reading-page="8"]').count(),0,'Opening must not eagerly load the entire document');
    await page.locator('#reader-scroll').evaluate(el=>el.scrollTop=el.scrollHeight);
    await page.locator('[data-load-direction="next"]').waitFor();
    assert.equal(await page.locator('[data-reading-page="1"]').count(),1,'A failed continuation must retain the existing pages');
    await page.locator('[data-load-direction="next"]').click();
    await page.locator('[data-reading-page="4"]').waitFor();
    for(let i=0;i<12 && !await page.locator('[data-reading-page="8"]').count();i++){
      await page.locator('#reader-scroll').evaluate(el=>el.scrollTop=el.scrollHeight);
      await page.waitForTimeout(200);
    }
    await page.locator('[data-reading-page="8"]').waitFor();
    assert.deepEqual(await page.locator('.flow-page').evaluateAll(rows=>rows.map(row=>Number(row.dataset.readingPage))),[1,3,4,6,8]);
    assert.match(await page.locator('#reader-after').innerText(),/已到文末/);
    assert(await page.locator('#reader-scroll').evaluate(el=>el.scrollTop>0));
    await page.selectOption('#page-select','1');
    await page.waitForFunction(()=>document.querySelector('#page-select').value==='1');
    await page.locator('#edit-page').click();
    await page.locator('#revision-dialog[open]').waitFor();
    await page.locator('#markdown-editor').fill('# 人工修订验收\n\n这是已保存的修订。\n\n$$x^2$$');
    await page.locator('#save-revision').click();
    await page.waitForFunction(()=>document.querySelector('#revision-status').textContent==='已保存人工修订');
    await page.locator('#preview-tab').click();
    await page.locator('#revision-preview h1').waitFor();
    assert.match(await page.locator('#revision-preview h1').innerText(),/人工修订验收/);
    await page.locator('#close-revision').click();
    assert.match(await page.locator('[data-page-markdown="1"]').innerText(),/人工修订验收/);
    await page.locator('#next-page').click();
    await page.waitForFunction(()=>document.querySelector('#page-select').value==='3');
    assert.match(await page.locator('#page-issues').innerText(),/公式仅保留原图/);
    await page.locator('#prev-page').click();
    await page.waitForFunction(()=>document.querySelector('#page-select').value==='1');
    assert.match(await page.locator('[data-page-markdown="1"]').innerText(),/人工修订验收/);
    const pathValue=await page.locator('#folder-path').inputValue();
    await page.route('**/api/documents/*/open',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({requested:false,path:pathValue,message:'系统未允许自动打开文件夹，可复制路径手动打开。'})}));
    await page.locator('#open-folder').click();
    await page.locator('#folder-fallback').waitFor();
    assert.equal(await page.locator('#folder-path').inputValue(),pathValue);
    await context.grantPermissions(['clipboard-read','clipboard-write']);
    await page.locator('#folder-fallback [data-copy-path]').click();
    assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),pathValue);
    await page.locator('#toggle-source').click();
    assert(await page.locator('#reader').evaluate(el=>el.classList.contains('text-only')));
    await page.screenshot({path:path.join(root,'.cache/ui-checks/reader.png')});
    const downloadPromise=page.waitForEvent('download');
    await page.locator('#export-button').click();
    const download=await downloadPromise;
    assert(download.suggestedFilename().endsWith('-agent.zip'));
    await download.saveAs(path.join(root,'.cache/ui-checks/export.zip'));
    await page.locator('#edit-page').click();
    await page.locator('#markdown-editor').fill('尚未保存的内容');
    page.once('dialog',dialog=>dialog.dismiss());
    await page.locator('#close-revision').click();
    assert(await page.locator('#revision-dialog').evaluate(dialog=>dialog.open));
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#close-revision').click();
    await page.locator('#close-reader').click();
    // Jump to an unloaded last page, then verify upward loading preserves that reading position.
    await page.locator('.document-card [data-open]').click();
    await page.locator('[data-reading-page="1"]').waitFor();
    await page.selectOption('#page-select','8');
    await page.locator('[data-reading-page="8"]').waitFor();
    await page.locator('[data-reading-page="6"]').waitFor();
    await page.waitForFunction(()=>document.querySelector('#page-select').value==='8');
    await page.locator('#reader-scroll').evaluate(el=>el.scrollTop=0);
    await page.locator('[data-reading-page="4"]').waitFor();
    await page.locator('#close-reader').click();
    await page.locator('#search').fill('不存在的名称');
    assert.equal(await page.locator('.document-card').count(),0);
    await page.locator('#search').fill('');
    await page.setViewportSize({width:390,height:844});
    await page.locator('.document-card [data-open]').click();
    await page.locator('[data-reading-page="1"]').waitFor();
    assert(await page.locator('#reader').evaluate(el=>el.classList.contains('text-only')));
    await page.screenshot({path:path.join(root,'.cache/ui-checks/mobile-reader.png')});
    assert(await page.locator('#reader-scroll').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
    await page.locator('#close-reader').click();
    await page.screenshot({path:path.join(root,'.cache/ui-checks/mobile.png'),fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
    assert.deepEqual(errors,[]);
    assert.deepEqual(external,[]);
    console.log('PASS: upload queue, continuous append/prepend without page replacement, failed-load retry, noncontiguous pages, offline math/table preview, revisions, folder-permission fallback and clipboard, ZIP export, search and responsive reading; no browser errors or external requests.');
  } finally {
    if(browser) await browser.close();
    server.kill();
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
