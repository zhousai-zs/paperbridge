'use strict';
const $ = (selector) => document.querySelector(selector);
const token = $('meta[name="paperbridge-token"]').content;
const statusNames = {queued:'等待转换',running:'转换中',complete:'已完成',needs_review:'需复核',failed:'未完成'};
const stageNames = {queued:'等待前面的任务完成',preparing:'正在准备本地模型',parsing:'正在解析',assembling:'正在整理正文与裁片',rendering:'正在生成原页图',finished:'转换完成',failed:'转换未完成'};
const issueNames = {empty_page:'该页未识别到正文',formula_image_only:'公式仅保留原图，需要核对',cjk_formula_needs_review:'快速模式中的中文公式需要核对',missing_image:'图片引用缺失',unbalanced_display_math:'公式分隔符不成对',replacement_characters:'存在无法识别的字符'};
let files = [], state = {tasks:[],documents:[]}, activeDoc = null, activePage = null;
let savedText = '', revisionVersion = '', dirty = false, uploading = false, pageLoading = false, savingRevision = false;
let toastTimer, previewTimer, previewSequence = 0, polling = false;
let readerGeneration=0, flowPages=new Map(), firstIndex=0, lastIndex=-1;
let loadingNext=false, loadingPrevious=false, nextFailed=false, previousFailed=false;
let editingPage=null, readingFrame=null;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function toast(message, error=false) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').classList.toggle('error',error);
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => $('#toast').hidden=true, error ? 8500 : 4000);
}

async function api(path, options={}) {
  const response = await fetch(path, {...options, headers:{'X-PaperBridge-Token':token,...options.headers}});
  if (!response.ok) {
    const error = await response.json().catch(() => ({detail:'服务暂时无法响应。'}));
    throw new Error(typeof error.detail === 'string' ? error.detail : '请求无效，请检查输入。');
  }
  return response;
}
const jsonPost = (path, data) => api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});

function selectFiles(incoming) {
  let rejected = false;
  for (const file of incoming) {
    if (!file.name.toLowerCase().endsWith('.pdf') || file.size > 200*1024*1024) {rejected=true;continue;}
    if (!files.some(f => f.name===file.name && f.size===file.size && f.lastModified===file.lastModified)) files.push(file);
  }
  if (rejected) toast('请选择不超过 200 MB 的 PDF 文件。',true);
  renderFiles();
}
function renderFiles() {
  $('#selected-files').innerHTML=files.map((f,i)=>`<li><span>${esc(f.name)}</span><small>${(f.size/1024/1024).toFixed(1)} MB</small><button data-remove="${i}" aria-label="移除 ${esc(f.name)}">×</button></li>`).join('');
  $('#start-button').disabled=files.length===0 || uploading;
  $('#start-button').innerHTML=uploading?'正在添加到队列…':`开始转换${files.length?` ${files.length} 份论文`:''} <span>↗</span>`;
}

async function uploadFiles() {
  if (uploading || !files.length) return;
  const pages=$('#pages').value.trim() || 'all';
  if (pages!=='all' && !/^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$/.test(pages)) {toast('页码请使用 1-3,5 这样的格式，或留空转换全文。',true);return;}
  const tier=$('input[name="tier"]:checked').value;
  const batch=[...files];
  uploading=true;renderFiles();
  let succeeded=0;
  for (const file of batch) {
    const form=new FormData();form.append('file',file);
    try {
      await api(`/api/tasks?tier=${tier}&pages=${encodeURIComponent(pages)}`,{method:'POST',body:form});
      files=files.filter(f=>f!==file);succeeded++;renderFiles();await refresh();
    } catch(error) {toast(`${file.name}：${error.message}`,true);}
  }
  uploading=false;renderFiles();
  if (succeeded) toast(`已添加 ${succeeded} 份论文，队列将依次转换。`);
}

function taskCard(job) {
  const total=job.total_pages || 0, completed=job.completed_pages || 0;
  const percent=['complete','needs_review'].includes(job.status)?100:total?Math.min(95,completed/total*100):0;
  let progress=stageNames[job.stage] || statusNames[job.status];
  if (job.stage==='parsing' && job.current_pages?.length) progress=`解析第 ${job.current_pages[0]}–${job.current_pages.at(-1)} 页`;
  return `<article class="task-card"><div class="task-top"><span class="pdf-icon">PDF</span><div class="task-info"><h3 class="task-title">${esc(job.filename)}</h3><div class="task-meta">${job.tier==='standard'?'标准模式':'快速模式'} · ${job.pages==='all'?'全部页面':esc(job.pages)+' 页'}</div></div><span class="status ${esc(job.status)}">${statusNames[job.status]}</span></div><div class="progress-track"><div class="progress-fill" style="width:${percent}%"></div></div><div class="task-bottom"><span>${esc(progress)}${total?` · ${completed}/${total} 页已解析`:''}</span>${job.document_id?`<button class="text-button" data-open="${job.document_id}">对照阅读 ↗</button>`:job.status==='failed'?`<button class="text-button" data-retry="${job.id}">重试 ↻</button>`:''}</div>${job.error?`<p class="task-error">${esc(job.error)}</p>`:''}${job.status==='failed'&&job.logs?.length?`<details class="task-logs" data-log-id="${job.id}"><summary>查看运行记录</summary><pre>${esc(job.logs.join('\n'))}</pre></details>`:''}</article>`;
}
function renderState() {
  $('#app-version').textContent=`PaperBridge ${state.version}`;
  $('#disk-space').textContent=`输出磁盘可用 ${state.free_gb} GB`;
  $('#stat-docs').textContent=state.documents.length;
  $('#stat-running').textContent=state.tasks.filter(t=>['queued','running'].includes(t.status)).length;
  $('#stat-review').textContent=state.documents.filter(d=>d.status==='needs_review').length;
  $('#nav-count').textContent=state.documents.length;
  $('#queue-count').textContent=`${state.tasks.length} 个任务`;
  const opened = new Set([...document.querySelectorAll('details[open][data-log-id]')].map(d=>d.dataset.logId));
  const scroll=$('#task-list').scrollTop;
  const ordered=[...state.tasks].sort((a,b)=>Number(['queued','running'].includes(b.status))-Number(['queued','running'].includes(a.status)) || b.created_at.localeCompare(a.created_at));
  $('#task-list').innerHTML=ordered.length?ordered.map(taskCard).join(''):'<div class="empty-state"><span class="empty-symbol">▤</span><strong>你的下一篇论文，从这里开始</strong><p>添加 PDF 后，可以在这里查看进度。<br>关闭后重新打开，任务记录仍会保留。</p></div>';
  document.querySelectorAll('details[data-log-id]').forEach(d=>d.open=opened.has(d.dataset.logId));
  $('#task-list').scrollTop=scroll;
  $('#output-path').textContent=`结果保存在：${state.output}`;
  renderDocuments();
}
function renderDocuments() {
  const search=$('#search').value.trim().toLowerCase(), filter=$('#status-filter').value;
  const docs=state.documents.filter(d=>d.name.toLowerCase().includes(search) && (filter==='all' || filter==='revised'&&d.revision_count>0 || filter===d.status));
  $('#library-count').textContent=docs.length;
  $('#document-list').innerHTML=docs.length?docs.map(d=>`<article class="document-card"><div class="document-top"><span class="pdf-icon">PDF</span><span class="status ${esc(d.status)}">${statusNames[d.status]}</span></div><h3>${esc(d.name)}</h3><p>${d.pages.length} 页 · ${d.tier==='standard'?'标准模式':d.tier==='basic'?'快速模式':esc(d.tier)}${d.revision_count?` · ${d.revision_count} 页有修订`:''}</p><div class="document-bottom"><small>${d.issues.length?`${d.issues.length} 项待复核`:'已保留原页与证据'}</small><button class="text-button" data-open="${d.id}">打开阅读 ↗</button></div></article>`).join(''):`<div class="empty-state"><strong>${search||filter!=='all'?'没有符合条件的文档':'文档库还没有论文'}</strong><p>${search||filter!=='all'?'试试调整搜索词或筛选条件。':'转换完成后，文档会自动出现在这里。'}</p></div>`;
}
async function refresh() {
  if (polling) return;
  polling=true;
  try {state=await (await api('/api/state')).json();renderState();}
  finally {polling=false;}
}

function canLeavePage() {
  if (savingRevision) {toast('正在保存修订，请稍候。');return false;}
  return !dirty || window.confirm('本页有尚未保存的修订。离开会丢弃这些修改，是否继续？');
}
async function openDocument(id) {
  const doc=state.documents.find(d=>d.id===id);
  if (!doc || !doc.pages.length || !canLeavePage()) return;
  activeDoc=doc;dirty=false;
  $('#reader-title').textContent=doc.name;
  $('#page-select').innerHTML=doc.pages.map(p=>`<option value="${p}">第 ${p} 页</option>`).join('');
  $('#folder-fallback').hidden=true;
  $('#folder-path').value=doc.path;
  const textOnly=window.matchMedia('(max-width:800px)').matches;
  $('#reader').classList.toggle('text-only',textOnly);
  $('#toggle-source').textContent=textOnly?'显示原文':'隐藏原文';
  $('#toggle-source').setAttribute('aria-pressed',String(!textOnly));
  if (!$('#reader').open) $('#reader').showModal();
  await resetReadingAt(doc.pages[0]);
}

function readingRow(data) {
  const section=document.createElement('section');
  section.className='flow-page';section.dataset.readingPage=data.page;
  const size=data.source_size?`width="${data.source_size.width}" height="${data.source_size.height}"`:'';
  const evidence=data.evidence.length?`<div class="flow-evidence"><span>原图核对</span>${data.evidence.map((e,i)=>`<a href="${esc(e.url)}" target="_blank" rel="noopener"><img src="${esc(e.url)}" loading="lazy" alt="${e.type==='equation'?'公式':'表格'} ${i+1}"><span>${e.type==='equation'?'公式':'表格'} ${i+1} ↗</span></a>`).join('')}</div>`:'';
  section.innerHTML=`<header class="flow-page-heading"><span>原文第 ${data.page} 页${data.revised?' · 已保存人工修订':''}${data.revision_stale?' · 识别稿已变化，请核对修订':''}</span><button class="text-button" data-edit-page="${data.page}">修订本页 ↗</button></header><div class="flow-page-columns"><div class="flow-source"><a href="${esc(data.source)}" target="_blank" rel="noopener" aria-label="打开第 ${data.page} 页原图"><img src="${esc(data.source)}" ${size} loading="lazy" alt="${esc(activeDoc.name)} 第 ${data.page} 页原图"></a></div><div class="flow-result"><article class="markdown" data-page-markdown="${data.page}">${data.html}</article>${evidence}</div></div>`;
  return section;
}

function markActivePage(page) {
  if (!activeDoc || !flowPages.has(page)) return;
  activePage=page;
  $('#page-select').value=page;
  const index=activeDoc.pages.indexOf(page);
  $('#prev-page').disabled=index===0;
  $('#next-page').disabled=index===activeDoc.pages.length-1;
  $('#reading-progress').textContent=`已载入 ${flowPages.size} / ${activeDoc.pages.length} 页`;
  $('#page-issues').hidden=!activeDoc.issues.length;
  $('#page-issues').innerHTML=activeDoc.issues.length?'复核提示：'+activeDoc.issues.map(i=>i.page&&activeDoc.pages.includes(i.page)?`<button class="text-button" data-jump-page="${i.page}">${i.page===page?'● ':''}第 ${i.page} 页 · ${esc(issueNames[i.code]||i.code)}</button>`:`<span>${esc(issueNames[i.code]||i.code)}</span>`).join(' '):'';
}

function updateSentinels() {
  if (!activeDoc) return;
  const sentinel=(direction,loading,failed,index,ended)=>{
    const node=$(direction==='next'?'#reader-after':'#reader-before');
    if (ended) {node.textContent=direction==='next'?`已到文末 · 共 ${activeDoc.pages.length} 页`:'从这里开始阅读';return;}
    if (loading) {node.textContent=`正在接上第 ${activeDoc.pages[index]} 页…`;return;}
    node.innerHTML=failed?`第 ${activeDoc.pages[index]} 页暂时无法载入 <button class="text-button" data-load-direction="${direction}">重试</button>`:direction==='next'?'继续下滑，自动接上下一页…':'向上滚动，自动接上前一页…';
  };
  sentinel('previous',loadingPrevious,previousFailed,firstIndex-1,firstIndex<=0);
  sentinel('next',loadingNext,nextFailed,lastIndex+1,lastIndex>=activeDoc.pages.length-1);
}

async function loadAdjacent(direction, force=false) {
  if (!activeDoc || !$('#reader').open) return;
  const next=direction==='next';
  if (next && (loadingNext || nextFailed&&!force)) return;
  if (!next && (loadingPrevious || previousFailed&&!force || !flowPages.size)) return;
  const index=next?lastIndex+1:firstIndex-1;
  if (index<0 || index>=activeDoc.pages.length) return;
  const generation=readerGeneration, doc=activeDoc, page=doc.pages[index];
  if (next) {loadingNext=true;nextFailed=false;} else {loadingPrevious=true;previousFailed=false;}
  updateSentinels();
  let succeeded=false;
  try {
    const data=await (await api(`/api/documents/${doc.id}/pages/${page}`)).json();
    if (generation!==readerGeneration) return;
    const row=readingRow(data), scroll=$('#reader-scroll');
    if (next) {$('#reading-pages').append(row);lastIndex=index;}
    else {
      const oldFirst=$('#reading-pages').firstElementChild;
      const top=oldFirst.getBoundingClientRect().top;
      $('#reading-pages').prepend(row);firstIndex=index;
      scroll.scrollTop+=oldFirst.getBoundingClientRect().top-top;
    }
    flowPages.set(page,{data,element:row});
    $('#edit-page').disabled=false;
    if (activePage===null) markActivePage(page);
    $('#reading-progress').textContent=`已载入 ${flowPages.size} / ${doc.pages.length} 页`;
    succeeded=true;
  } catch(error) {
    if (generation===readerGeneration) {
      if(next) nextFailed=true;else previousFailed=true;
      toast(`第 ${page} 页未能载入：${error.message}`,true);
    }
  } finally {
    if(generation===readerGeneration){
      if(next) loadingNext=false;else loadingPrevious=false;
      updateSentinels();
      if(succeeded) requestAnimationFrame(checkReadingPosition);
    }
  }
}

async function resetReadingAt(page) {
  readerGeneration++;flowPages=new Map();activePage=null;
  const generation=readerGeneration;
  firstIndex=activeDoc.pages.indexOf(page);lastIndex=firstIndex-1;
  loadingNext=false;loadingPrevious=false;nextFailed=false;previousFailed=false;
  $('#reading-pages').replaceChildren();$('#reader-scroll').scrollTop=0;
  $('#page-select').value=page;
  $('#reading-progress').textContent='正在载入正文…';
  $('#edit-page').disabled=true;
  updateSentinels();
  await loadAdjacent('next');
  if(generation===readerGeneration) $('#edit-page').disabled=!flowPages.size;
}

function checkReadingPosition() {
  readingFrame=null;
  if (!activeDoc || !$('#reader').open || !flowPages.size) return;
  const scroll=$('#reader-scroll'), bounds=scroll.getBoundingClientRect();
  const line=bounds.top+Math.min(130,bounds.height/3);
  const rows=[...$('#reading-pages').children];
  const current=rows.find(row=>row.getBoundingClientRect().bottom>line) || rows.at(-1);
  const page=Number(current.dataset.readingPage);
  if(page!==activePage) markActivePage(page);
  if($('#reader-after').getBoundingClientRect().top<bounds.bottom+600) void loadAdjacent('next');
  if($('#reader-before').getBoundingClientRect().bottom>bounds.top-250) void loadAdjacent('previous');
}

function scrollToPage(page) {
  const row=flowPages.get(page)?.element;
  if(!row) return;
  const scroll=$('#reader-scroll');
  scroll.scrollTop+=row.getBoundingClientRect().top-scroll.getBoundingClientRect().top-6;
  markActivePage(page);
}

async function changePage(page) {
  if (!activeDoc?.pages.includes(page)) return;
  if(flowPages.has(page)) scrollToPage(page);
  else await resetReadingAt(page);
}

async function openEditor(page) {
  if(pageLoading || !activeDoc || !activeDoc.pages.includes(page)) return;
  pageLoading=true;
  const generation=readerGeneration;
  try {
    const data=await (await api(`/api/documents/${activeDoc.id}/pages/${page}`)).json();
    if(generation!==readerGeneration || !$('#reader').open) return;
    editingPage=page;savedText=data.markdown;revisionVersion=data.version;dirty=false;
    previewSequence++;clearTimeout(previewTimer);
    $('#revision-title').textContent=`修订原文第 ${page} 页`;
    $('#markdown-editor').value=data.markdown;
    $('#revision-preview').innerHTML=data.html;
    $('#revision-status').textContent=data.revision_stale?'识别稿已变化，请重新核对':data.revised?'已保存人工修订':'原始识别稿';
    $('#save-revision').disabled=true;
    $('#revision-dialog').showModal();setTab('edit');
  } finally {pageLoading=false;}
}

function setTab(tab) {
  $('#markdown-editor').hidden=tab!=='edit';$('#revision-preview').hidden=tab==='edit';
  $('#preview-tab').classList.toggle('active',tab==='preview');$('#edit-tab').classList.toggle('active',tab==='edit');
  $('#preview-tab').setAttribute('aria-selected',String(tab==='preview'));$('#edit-tab').setAttribute('aria-selected',String(tab==='edit'));
  if (tab==='edit') $('#markdown-editor').focus();
}
async function previewEdit() {
  if(!$('#revision-dialog').open) return;
  const sequence=++previewSequence;
  const result=await (await jsonPost('/api/preview',{document_id:activeDoc.id,markdown:$('#markdown-editor').value})).json();
  if (sequence===previewSequence) $('#revision-preview').innerHTML=result.html;
}
async function saveRevision() {
  if (!dirty || pageLoading || savingRevision) return;
  const text=$('#markdown-editor').value;
  savingRevision=true;
  $('#save-revision').disabled=true;
  try {
    const result=await (await jsonPost(`/api/documents/${activeDoc.id}/pages/${editingPage}`,{markdown:text,version:revisionVersion})).json();
    savedText=text;revisionVersion=result.version;dirty=$('#markdown-editor').value!==savedText;
    $('#revision-status').textContent=dirty?'存在未保存的修订':'已保存人工修订';
    const data=await (await api(`/api/documents/${activeDoc.id}/pages/${editingPage}`)).json();
    const previous=flowPages.get(editingPage);
    if(previous){
      const row=readingRow(data);
      previous.element.replaceWith(row);flowPages.set(editingPage,{data,element:row});
    }
    toast('本页修订已保存，导出时会自动合入。');await refresh();
  } finally {savingRevision=false;$('#save-revision').disabled=!dirty;}
}

function closeEditor() {
  if(!canLeavePage()) return;
  $('#revision-dialog').close();dirty=false;previewSequence++;clearTimeout(previewTimer);
  editingPage=null;
}

async function copyResultPath() {
  try {await navigator.clipboard.writeText(activeDoc.path);toast('结果文件夹路径已复制。');}
  catch {
    $('#folder-fallback').hidden=false;$('#folder-path').value=activeDoc.path;
    $('#folder-path').focus();$('#folder-path').select();
    toast('路径已选中，可按 Ctrl+C 复制。');
  }
}

async function openResultFolder() {
  const data=await (await api(`/api/documents/${activeDoc.id}/open`,{method:'POST'})).json();
  if(!data.requested){$('#folder-fallback').hidden=false;$('#folder-path').value=data.path;}
  toast(data.message,!data.requested);
}
async function exportDocument() {
  if (savingRevision || pageLoading) return;
  if (dirty) {toast('请先保存本页修订，再导出阅读包。',true);return;}
  $('#export-button').disabled=true;$('#export-button').textContent='正在打包…';
  try {
    const response=await api(`/api/documents/${activeDoc.id}/export`);
    const blob=await response.blob();
    const url=URL.createObjectURL(blob), a=document.createElement('a');
    a.href=url;
    const disposition=response.headers.get('Content-Disposition')||'';
    a.download=disposition.includes("UTF-8''")?decodeURIComponent(disposition.split("UTF-8''")[1]):'paperbridge-agent.zip';
    document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
    toast('阅读包已导出，包含原文、图片和已保存的人工修订。');
  } finally {$('#export-button').disabled=false;$('#export-button').textContent='导出阅读包 ↗';}
}
const guarded = (fn) => (...args) => Promise.resolve().then(()=>fn(...args)).catch(e=>toast(e.message,true));

$('#file-input').addEventListener('change',event=>{selectFiles(event.target.files);event.target.value='';});
$('#dropzone').addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();$('#file-input').click();}});
['dragenter','dragover'].forEach(name=>$('#dropzone').addEventListener(name,event=>{event.preventDefault();$('#dropzone').classList.add('dragover');}));
['dragleave','drop'].forEach(name=>$('#dropzone').addEventListener(name,event=>{event.preventDefault();$('#dropzone').classList.remove('dragover');}));
$('#dropzone').addEventListener('drop',event=>selectFiles(event.dataTransfer.files));
document.addEventListener('dragover',event=>event.preventDefault());
document.addEventListener('drop',event=>event.preventDefault());
$('#selected-files').addEventListener('click',event=>{const button=event.target.closest('[data-remove]');if(button&&!uploading){files.splice(Number(button.dataset.remove),1);renderFiles();}});
$('#start-button').addEventListener('click',guarded(uploadFiles));
document.addEventListener('click',guarded(async event=>{
  const open=event.target.closest('[data-open]');if(open) await openDocument(open.dataset.open);
  const retry=event.target.closest('[data-retry]');if(retry){retry.disabled=true;await api(`/api/tasks/${retry.dataset.retry}/retry`,{method:'POST'});await refresh();}
  const jump=event.target.closest('[data-jump-page]');if(jump) await changePage(Number(jump.dataset.jumpPage));
  const edit=event.target.closest('[data-edit-page]');if(edit) await openEditor(Number(edit.dataset.editPage));
  const load=event.target.closest('[data-load-direction]');if(load) await loadAdjacent(load.dataset.loadDirection,true);
  if(event.target.closest('[data-copy-path]')) await copyResultPath();
  const nav=event.target.closest('[data-view]');
  if(nav){document.body.classList.toggle('library-only',nav.dataset.view==='library');document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b===nav));$('#breadcrumb').textContent=`工作空间 / ${nav.dataset.view==='library'?'文档库':'转换工作台'}`;window.scrollTo(0,0);}
}));
$('#search').addEventListener('input',renderDocuments);$('#status-filter').addEventListener('change',renderDocuments);
$('#prev-page').addEventListener('click',guarded(()=>changePage(activeDoc.pages[activeDoc.pages.indexOf(activePage)-1])));
$('#next-page').addEventListener('click',guarded(()=>changePage(activeDoc.pages[activeDoc.pages.indexOf(activePage)+1])));
$('#page-select').addEventListener('change',guarded(event=>changePage(Number(event.target.value))));
$('#close-reader').addEventListener('click',()=>{if(canLeavePage()){$('#reader').close();readerGeneration++;dirty=false;previewSequence++;}});
$('#reader').addEventListener('cancel',event=>{event.preventDefault();$('#close-reader').click();});
$('#reader-scroll').addEventListener('scroll',()=>{if(readingFrame===null) readingFrame=requestAnimationFrame(checkReadingPosition);},{passive:true});
window.addEventListener('resize',()=>{if(readingFrame===null) readingFrame=requestAnimationFrame(checkReadingPosition);});
$('#toggle-source').addEventListener('click',()=>{
  const anchor=flowPages.get(activePage)?.element;
  const top=anchor?.getBoundingClientRect().top;
  const textOnly=$('#reader').classList.toggle('text-only');
  $('#toggle-source').textContent=textOnly?'显示原文':'隐藏原文';
  $('#toggle-source').setAttribute('aria-pressed',String(!textOnly));
  if(anchor) $('#reader-scroll').scrollTop+=anchor.getBoundingClientRect().top-top;
  requestAnimationFrame(checkReadingPosition);
});
$('#edit-page').addEventListener('click',guarded(()=>openEditor(activePage)));
$('#close-revision').addEventListener('click',closeEditor);
$('#revision-dialog').addEventListener('cancel',event=>{event.preventDefault();closeEditor();});
$('#preview-tab').addEventListener('click',guarded(async()=>{await previewEdit();setTab('preview');}));
$('#edit-tab').addEventListener('click',()=>setTab('edit'));
$('#markdown-editor').addEventListener('input',()=>{dirty=$('#markdown-editor').value!==savedText;$('#save-revision').disabled=!dirty;$('#revision-status').textContent=dirty?'存在未保存的修订':'内容与已载入版本一致';clearTimeout(previewTimer);previewTimer=setTimeout(guarded(previewEdit),600);});
$('#save-revision').addEventListener('click',guarded(saveRevision));
$('#export-button').addEventListener('click',guarded(exportDocument));
$('#open-folder').addEventListener('click',guarded(openResultFolder));
$('#copy-path').addEventListener('click',guarded(copyResultPath));
for(const node of [$('#reading-pages'),$('#revision-preview')]) node.addEventListener('click',event=>{const link=event.target.closest('a');if(link){link.target='_blank';link.rel='noopener noreferrer';}});
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
document.addEventListener('keydown',guarded(async event=>{if(event.ctrlKey&&event.key==='s'&&$('#revision-dialog').open){event.preventDefault();await saveRevision();}}));
refresh().catch(error=>toast(error.message,true));
setInterval(()=>refresh().catch(()=>toast('与本地工作台的连接已断开，请确认启动窗口仍在运行。',true)),2500);
