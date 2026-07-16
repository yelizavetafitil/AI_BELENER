let currentFile = null, isStreaming = false;
let abortController = null;
let currentConvId = null;
let autoModelEnabled = true;
let userOverrodeModel = false;
let _modelMap = {}; // id → label
let userScrolled = false;
let currentPreviewUrl = '';
let lastRetryRequest = null;
const GOST_DEFAULT_QUESTION = 'Проверка ГОСТ на листе';
const DRAWING_FILE_RE = /\.(pdf|png|jpe?g|bmp|gif|webp|tiff?)$/i;

function getSelectedModel() {
  const el = document.getElementById('model-select');
  return (el && el.value) ? el.value : 'gemma3:4b';
}

const sendIcon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>`;
const stopIcon  = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>`;
const cpIco = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>`;
const okIco  = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;

// ── copy ─────────────────────────────────────────────────────────────────────

function htmlToReadable(html) {
  const d = document.createElement('div');
  d.innerHTML = html;
  return nodeToText(d).replace(/\n{3,}/g, '\n\n').trim();
}

function nodeToText(n) {
  if (n.nodeType === Node.TEXT_NODE) return n.textContent;
  const t = n.tagName ? n.tagName.toLowerCase() : '';
  const ch = () => Array.from(n.childNodes).map(nodeToText).join('');
  if (t === 'pre') {
    const c = n.querySelector('code');
    const tx = (c || n).innerText;
    const lg = (c && c.className.match(/language-(\w+)/)?.[1]) || '';
    return '\n```' + lg + '\n' + tx.trimEnd() + '\n```\n';
  }
  if (t === 'code') return '`' + n.textContent + '`';
  if (t === 'table') return tblAscii(n) + '\n';
  if (['thead','tbody','tfoot','tr','th','td'].includes(t)) return ch();
  if (t === 'h1') return '\n# '   + ch().trim() + '\n';
  if (t === 'h2') return '\n## '  + ch().trim() + '\n';
  if (t === 'h3') return '\n### ' + ch().trim() + '\n';
  if (t === 'ul') return '\n' + Array.from(n.children).map(li => '• ' + nodeToText(li).trim()).join('\n') + '\n';
  if (t === 'ol') return '\n' + Array.from(n.children).map((li, i) => (i+1) + '. ' + nodeToText(li).trim()).join('\n') + '\n';
  if (t === 'li') return ch();
  if (['p','div','blockquote','section','article'].includes(t)) return '\n' + ch().trim() + '\n';
  if (t === 'br') return '\n';
  if (t === 'hr') return '\n' + '─'.repeat(36) + '\n';
  return ch();
}

function tblAscii(el) {
  const rows = [];
  el.querySelectorAll('tr').forEach(r =>
    rows.push(Array.from(r.querySelectorAll('th,td')).map(c => c.innerText.replace(/\n/g,' ').trim()))
  );
  if (!rows.length) return '';
  const cols = Math.max(...rows.map(r => r.length));
  rows.forEach(r => { while (r.length < cols) r.push(''); });
  const ws = Array.from({length: cols}, (_, c) => Math.max(...rows.map(r => r[c].length), 3));
  const sep = '+-' + ws.map(w => '-'.repeat(w)).join('-+-') + '-+';
  const row = cs => '| ' + cs.map((c,i) => c.padEnd(ws[i])).join(' | ') + ' |';
  const ls = [sep];
  rows.forEach((r,i) => { ls.push(row(r)); if (i===0) ls.push(sep); });
  ls.push(sep);
  return '\n' + ls.join('\n') + '\n';
}

function tblPlain(el) {
  const rows = [];
  el.querySelectorAll('tr').forEach(r =>
    rows.push(Array.from(r.querySelectorAll('th,td')).map(c => c.innerText.replace(/\n+/g,' ').trim()).join('\t'))
  );
  return rows.join('\n').trim();
}

function doCopy(text, btn) {
  const iconOnly = btn.classList.contains('tcbtn');
  const ok = () => {
    btn.classList.add('ok');
    btn.innerHTML = iconOnly ? okIco : okIco + ' скопировано';
    setTimeout(() => {
      btn.classList.remove('ok');
      btn.innerHTML = iconOnly ? cpIco : cpIco + (btn.dataset.lbl || ' копировать');
    }, 2000);
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(ok).catch(() => fbCopy(text, ok));
  } else {
    fbCopy(text, ok);
  }
}

function fbCopy(text, cb) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.cssText = 'position:fixed;top:-9999px;opacity:0';
  document.body.appendChild(ta);
  ta.focus(); ta.select();
  try { document.execCommand('copy'); cb(); } catch(e) {}
  document.body.removeChild(ta);
}

function colorizeStnStatus(el) {
  el.querySelectorAll('table tr').forEach(tr => {
    tr.classList.remove('row-stn-cancel', 'row-stn-warn');
    const cells = tr.querySelectorAll('td');
    if (cells.length < 5) return;
    const st = cells[4].textContent.trim().toLowerCase();
    cells[4].classList.remove('stn-ok', 'stn-cancel', 'stn-warn', 'stn-miss');
    if (st === 'актуален') {
      cells[4].classList.add('stn-ok');
    } else if (st === 'отменён' || st === 'отменен') {
      cells[4].classList.add('stn-cancel');
      tr.classList.add('row-stn-cancel');
    } else if (st.includes('не введён') || st.includes('не введен')) {
      cells[4].classList.add('stn-warn');
      tr.classList.add('row-stn-warn');
    } else if (st.includes('нет') || st.includes('ошибка')) {
      cells[4].classList.add('stn-miss');
    }
  });
}

function addCodeBtns(el) {
  el.querySelectorAll('pre').forEach(pre => {
    if (pre.parentElement.classList.contains('code-wrap')) return;
    const w = document.createElement('div');
    w.className = 'code-wrap';
    pre.parentNode.insertBefore(w, pre);
    w.appendChild(pre);
    const b = document.createElement('button');
    b.className = 'cbtn'; b.dataset.lbl = ' копировать';
    b.innerHTML = cpIco + ' копировать';
    b.onclick = () => doCopy(pre.innerText.trimEnd(), b);
    w.appendChild(b);
  });

  el.querySelectorAll('table').forEach(table => {
    if (table.parentElement.classList.contains('table-wrap')) return;
    const outer = document.createElement('div');
    outer.className = 'table-outer';
    table.parentNode.insertBefore(outer, table);
    const tools = document.createElement('div');
    tools.className = 'table-tools';
    outer.appendChild(tools);
    const w = document.createElement('div');
    w.className = 'table-wrap';
    outer.appendChild(w);
    w.appendChild(table);
    const b = document.createElement('button');
    b.className = 'tcbtn';
    b.title = 'Копировать таблицу';
    b.setAttribute('aria-label', 'Копировать таблицу');
    b.innerHTML = cpIco;
    b.onclick = () => doCopy(tblPlain(table), b);
    tools.appendChild(b);
  });
  colorizeStnStatus(el);
}

function addMsgCopy(el, html) {
  const e = el.querySelector('.mcopy'); if (e) e.remove();
  const b = document.createElement('button');
  b.className = 'mcopy'; b.dataset.lbl = ' копировать ответ';
  b.innerHTML = cpIco + ' копировать ответ';
  b.onclick = () => doCopy(htmlToReadable(html), b);
  el.appendChild(b);
}

function addReportActions(el, html) {
  const old = el.querySelector('.report-actions'); if (old) old.remove();
  // Disabled by request: no extra report download button.
}

function downloadReportPdf(text) {
  if (!window.pdfMake) {
    showToast('PDF-генератор не загрузился, обновите страницу', true);
    return;
  }
  const lines = String(text || '').split('\n');
  const content = lines.map(line => ({
    text: line.length ? line : ' ',
    margin: [0, 0, 0, 2],
  }));
  const docDefinition = {
    pageSize: 'A4',
    pageMargins: [36, 40, 36, 40],
    defaultStyle: {
      fontSize: 10.5,
      lineHeight: 1.28,
    },
    content: [
      {text: 'Отчёт БелнипиAI', style: 'title', margin: [0, 0, 0, 10]},
      ...content,
    ],
    styles: {
      title: {fontSize: 16, bold: true},
    },
  };
  window.pdfMake.createPdf(docDefinition).download('belener-gost-report.pdf');
}

function createProgress() {
  const box = document.createElement('div');
  box.className = 'progress-card';
  box.innerHTML = `
    <div class="progress-head">
      <span class="progress-stage">Подготовка</span>
      <span class="progress-value" style="display: none;"></span>
    </div>
    <div class="progress-track"><div class="progress-fill indeterminate"></div></div>
    <div class="progress-status">Ожидаю ответ сервера...</div>`;
  return box;
}

function updateProgress(box, payload) {
  if (!box) return;
  const progress = Number.isFinite(payload.progress) ? payload.progress : null;
  if (payload.stage) box.querySelector('.progress-stage').textContent = payload.stage;
  if (progress !== null) {
    const fill = box.querySelector('.progress-fill');
    fill.classList.remove('indeterminate');
    fill.style.width = `${Math.max(0, Math.min(progress, 100))}%`;
    const val = box.querySelector('.progress-value');
    val.style.display = 'inline';
    val.textContent = `${Math.max(0, Math.min(progress, 100))}%`;
  }
  if (payload.status) box.querySelector('.progress-status').textContent = payload.status;
}

function retryLastMessage() {
  if (!lastRetryRequest || isStreaming) return;
  ta.value = lastRetryRequest.question || '';
  ta.dispatchEvent(new Event('input'));
  if (lastRetryRequest.file) setFile(lastRetryRequest.file, {skipAutostart: true});
  sendMessage();
}

function confirmDialog(text, title = 'Подтверждение') {
  return new Promise(resolve => {
    const modal = document.getElementById('confirm-modal');
    const titleEl = document.getElementById('confirm-title');
    const textEl = document.getElementById('confirm-text');
    const ok = document.getElementById('confirm-ok');
    const cancel = document.getElementById('confirm-cancel');
    titleEl.textContent = title;
    textEl.textContent = text;
    modal.hidden = false;
    const finish = value => {
      modal.hidden = true;
      ok.onclick = cancel.onclick = null;
      resolve(value);
    };
    ok.onclick = () => finish(true);
    cancel.onclick = () => finish(false);
    modal.onclick = e => { if (e.target === modal) finish(false); };
  });
}

// ── markdown ─────────────────────────────────────────────────────────────────

function fixMarkdown(md) {
  return md.replace(/^```[^\n]*\n([\s\S]*?)^```/gm, (match, inner) => {
    const lines = inner.trim().split('\n');
    const isTable = lines.length >= 2
      && lines[0].trim().startsWith('|')
      && /^\|[\s|:\-]+\|$/.test(lines[1].trim());
    return isTable ? inner.trim() : match;
  });
}

function _normativeRowFillColor(tr) {
  const cls = (tr && tr.classList) ? tr.classList : null;
  if (!cls) return null;
  if (cls.contains('row-active')) return '#dcfce7';   // green
  if (cls.contains('row-canceled')) return '#fee2e2'; // red
  if (cls.contains('row-replaced')) return '#fef3c7'; // yellow
  return null;
}

function _cellBold(td) {
  return !!(td && td.querySelector && td.querySelector('strong'));
}

function _cellText(td) {
  const t = (td && td.textContent) ? td.textContent : '';
  return String(t || '').replace(/\s+/g, ' ').trim() || '—';
}

function buildNormativeTablePdfDocDefinition(workspaceEl) {
  const tableEl = workspaceEl ? workspaceEl.querySelector('.normative-table-container table') : null;
  if (!tableEl) return null;

  const metaEl = workspaceEl.querySelector('.normative-workspace-meta');
  const metaText = metaEl ? metaEl.textContent.replace(/\s+/g, ' ').trim() : '';

  const headCells = [...tableEl.querySelectorAll('thead th')].map(th => th.textContent.trim() || '—');
  const headerRow = headCells.map(label => ({
    text: label,
    bold: true,
    fontSize: 9,
    fillColor: '#f3f4f6',
    color: '#111827',
  }));

  const rows = [...tableEl.querySelectorAll('tbody tr')];
  const bodyRows = rows.map(tr => {
    const fill = _normativeRowFillColor(tr);
    const tds = [...tr.querySelectorAll('td')];
    return tds.map((td, colIdx) => {
      const a = td.querySelector('a.stn-link[href]');
      const isBold = _cellBold(td);
      if (a && a.href) {
        return {
          text: a.textContent.trim() || 'Открыть',
          link: a.href,
          color: '#2563eb',
          decoration: 'underline',
          bold: isBold,
          fontSize: 9,
          fillColor: fill || undefined,
        };
      }
      return {
        text: _cellText(td),
        bold: isBold,
        fontSize: 9,
        fillColor: fill || undefined,
      };
    });
  });

  const docDefinition = {
    pageSize: 'A4',
    pageOrientation: 'portrait',
    pageMargins: [18, 18, 18, 18],
    defaultStyle: {
      font: 'Roboto',
      fontSize: 9,
      lineHeight: 1.2,
    },
    content: [
      { text: 'Таблица нормативов (ГОСТ/СП/СН и др.)', style: 'title', margin: [0, 0, 0, 8] },
      ...(metaText ? [{ text: metaText, margin: [0, 0, 0, 10] }] : []),
      {
        table: {
          headerRows: 1,
          widths: [40, '*', 55, 60, 60, '*'],
          body: [headerRow, ...bodyRows],
        },
        layout: {
          hLineWidth: () => 0.4,
          vLineWidth: () => 0.4,
          hLineColor: () => '#e5e7eb',
          vLineColor: () => '#e5e7eb',
          paddingLeft: () => 4,
          paddingRight: () => 4,
          paddingTop: () => 3,
          paddingBottom: () => 3,
        },
      },
    ],
    styles: {
      title: { fontSize: 12, bold: true },
    },
  };
  return docDefinition;
}

function buildNormativeTablePdfPayload(workspaceEl) {
  const tableEl = workspaceEl ? workspaceEl.querySelector('.normative-table-container table') : null;
  if (!tableEl) return null;
  const metaEl = workspaceEl.querySelector('.normative-workspace-meta');
  const filenameEl = metaEl && [...metaEl.querySelectorAll('p')].find(p => /Файл:/i.test(p.textContent || ''));
  const filenameText = filenameEl ? filenameEl.textContent.replace(/^.*Файл:\s*/i, '').trim() : 'belener-gost-table';
  const headers = [...tableEl.querySelectorAll('thead th')].map(th => th.textContent.trim() || '—');
  const rows = [...tableEl.querySelectorAll('tbody tr')].map(tr => ({
    fill: tr.classList.contains('row-active')
      ? 'active'
      : tr.classList.contains('row-canceled')
        ? 'canceled'
        : tr.classList.contains('row-replaced')
          ? 'replaced'
          : '',
    cells: [...tr.querySelectorAll('td')].map(td => {
      const a = td.querySelector('a.stn-link[href]');
      return {
        text: a ? (a.textContent.trim() || 'Открыть') : _cellText(td),
        href: a ? a.href : '',
        bold: _cellBold(td),
      };
    }),
  }));
  const meta = metaEl
    ? [...metaEl.querySelectorAll('p')].map(p => p.textContent.replace(/\s+/g, ' ').trim()).filter(Boolean)
    : [];
  const listEl = workspaceEl.querySelector('.normative-workspace-list');
  const summaryEl =
    workspaceEl.querySelector('.normative-table-summary')
    || (listEl && [...listEl.querySelectorAll('p')].find(p => /Всего в документе:/i.test(p.textContent || '')));
  let summary = summaryEl ? summaryEl.textContent.replace(/\s+/g, ' ').trim() : '';
  if (!summary) {
    const m = (workspaceEl.innerText || '').match(
      /Всего в документе:\s*\d+;\s*найдено в ИПС:\s*\d+;\s*актуально:\s*\d+/i
    );
    if (m) summary = m[0].replace(/\s+/g, ' ').trim();
  }
  if (!summary && rows.length) {
    const found = rows.filter(r => r.cells[2] && r.cells[2].href).length;
    const active = rows.filter(r => /актуален/i.test((r.cells[5] && r.cells[5].text) || '')).length;
    summary = `Всего в документе: ${rows.length}; найдено в ИПС: ${found}; актуально: ${active}`;
  }
  return {
    title: 'Таблица нормативов (ГОСТ/СП/СН и др.)',
    filename: `${filenameText.replace(/\.[^.]+$/, '')}-normatives.pdf`,
    meta,
    summary,
    headers,
    rows,
    widths: [14, 66, 18, 20, 20, 28],
  };
}

async function downloadNormativeTablePdf(btn) {
  const workspaceEl = btn && btn.closest('.normative-workspace');
  if (!workspaceEl || btn.disabled) return;
  try {
    const payload = buildNormativeTablePdfPayload(workspaceEl);
    if (!payload) {
      showToast('PDF: таблица не найдена', true);
      return;
    }
    btn.disabled = true;
    btn.textContent = 'Готовлю PDF...';
    const resp = await fetch('/api/export-normative-pdf', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = payload.filename || 'belener-gost-table.pdf';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    showToast('PDF скачан');
  } catch (e) {
    console.error(e);
    showToast('PDF: ошибка экспорта', true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Скачать таблицу в PDF';
  }
}

function ensureNormativeTablePdfDownloads(root) {
  if (!root) return;

  for (const workspaceEl of root.querySelectorAll('.normative-workspace')) {
    if (workspaceEl.querySelector('.normative-table-pdf-btn')) continue;
    const tableEl = workspaceEl.querySelector('.normative-table-container table');
    if (!tableEl) continue;

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-primary btn-sm normative-table-pdf-btn';
    btn.style.marginTop = '10px';
    btn.textContent = 'Скачать таблицу в PDF';

    const container = workspaceEl.querySelector('.normative-table-container');
    if (container && container.parentNode) {
      container.parentNode.insertBefore(btn, container.nextSibling);
    } else {
      workspaceEl.appendChild(btn);
    }
  }
}

/** marked не парсит ** внутри HTML — починка уже отрисованного ответа (текущий чат). */
function beautifyNormativeHtml(root) {
  if (!root) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const t = node.nodeValue;
    if (!t || t.indexOf('**') < 0 && t.indexOf('*') < 0) continue;
    if (!node.parentElement || node.parentElement.closest('code, pre, a, script')) continue;
    let html = t
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
    if (html === t) continue;
    const wrap = document.createElement('span');
    wrap.innerHTML = html;
    node.parentNode.replaceChild(wrap, node);
  }
  root.querySelectorAll('.normative-preview-shell').forEach(shell => {
    const group = shell.dataset.previewGroup;
    if (!group) return;
    const pages = [...shell.querySelectorAll(`.normative-preview-page[data-group="${group}"]`)];
    const active = pages.find(p => p.classList.contains('is-active')) || pages[0];
    const label = root.querySelector(`.preview-page-label[data-group="${group}"]`);
    if (active && label) {
      const idx = pages.indexOf(active) + 1;
      label.textContent = `${idx} / ${pages.length} · лист ${active.dataset.page}`;
    }
  });

  ensureNormativeTablePdfDownloads(root);
}

function renderAssistantMarkdown(md) {
  // marked ломает вложенный HTML workspace (кнопки зума → «-100%+», таблица в ASCII).
  const src = String(md || '');
  const marker = '<div class="normative-workspace">';
  const parts = [];
  let cursor = 0;
  while (true) {
    const idx = src.indexOf(marker, cursor);
    if (idx < 0) {
      const tail = src.slice(cursor);
      if (tail.trim()) parts.push({ html: false, text: tail });
      break;
    }
    const head = src.slice(cursor, idx);
    if (head.trim()) parts.push({ html: false, text: head });
    // Закрывающий корень workspace — три </div> в конце блока.
    let end = idx + marker.length;
    let depth = 1;
    while (end < src.length && depth > 0) {
      const nextOpen = src.indexOf('<div', end);
      const nextClose = src.indexOf('</div>', end);
      if (nextClose < 0) { end = src.length; break; }
      if (nextOpen >= 0 && nextOpen < nextClose) {
        depth += 1;
        end = nextOpen + 4;
      } else {
        depth -= 1;
        end = nextClose + 6;
      }
    }
    parts.push({ html: true, text: src.slice(idx, end) });
    cursor = end;
  }
  let html = '';
  for (const p of parts) {
    html += p.html ? p.text : marked.parse(fixMarkdown(p.text));
  }
  if (!parts.length) html = marked.parse(fixMarkdown(src));
  const box = document.createElement('div');
  box.innerHTML = html;
  beautifyNormativeHtml(box);
  return box.innerHTML;
}

// ── conversations ─────────────────────────────────────────────────────────────

async function loadConversations() {
  try {
    const data = await fetch('/api/conversations').then(r => r.json());
    renderConvList(data.conversations || []);
  } catch(e) {
    console.error(e);
  }
}

function renderConvList(convs) {
  const list = document.getElementById('chats-list');
  if (!convs.length) {
    list.innerHTML = '<div class="chats-empty">Нет чатов</div>';
    return;
  }
  list.innerHTML = convs.map(c => `
    <div class="chat-item ${c.id === currentConvId ? 'active' : ''}" data-id="${c.id}" onclick="openChat('${c.id}')">
      <div class="chat-item-title">${esc(c.title)}</div>
      <button class="chat-item-del" onclick="deleteChat(event,'${c.id}')" title="Удалить">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
  `).join('');
}

async function newChat() {
  const model = getSelectedModel();
  const data = await fetch('/api/conversations', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({model})
  }).then(r => r.json());

  currentConvId = data.id;
  clearFeed();
  await loadConversations();
  highlightActiveChat();
}

async function openChat(id) {
  currentConvId = id;
  clearFeed();
  highlightActiveChat();

  const data = await fetch(`/api/conversations/${id}/messages`).then(r => r.json());
  const msgs = data.messages || [];
  if (!msgs.length) return;

  hideEmpty();
  for (const m of msgs) {
    const ac = addMessage(m.role, m.role === 'user' ? m.content : '', m.file_name || null, m.file_url || null);
    if (m.role === 'assistant') {
      ac.innerHTML = renderAssistantMarkdown(m.content);
      addCodeBtns(ac);
      addMsgCopy(ac, ac.innerHTML);
      addReportActions(ac, ac.innerHTML);
    }
  }
  userScrolled = false;
  scrollEnd(true);
}

async function deleteChat(e, id) {
  e.stopPropagation();
  await fetch(`/api/conversations/${id}`, {method: 'DELETE'});
  if (currentConvId === id) {
    currentConvId = null;
    clearFeed();
    document.getElementById('empty-state').style.display = '';
  }
  loadConversations();
}

function highlightActiveChat() {
  document.querySelectorAll('.chat-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id === currentConvId);
  });
}

function clearFeed() {
  const inner = document.getElementById('chat-inner');
  inner.innerHTML = '';
  const welcome = document.createElement('div');
  welcome.className = 'welcome';
  welcome.id = 'empty-state';
  welcome.innerHTML = `
    <div class="welcome-hero">
      <div class="welcome-badge">Локальная обработка · данные не уходят в облако</div>
      <h1 class="welcome-title">Проверка ГОСТ<br>на чертеже</h1>
      <p class="welcome-sub">Загрузите PDF или скан — система найдёт ГОСТ, ОСТ, СТП, РД, СНиП, ТУ, ТКП, СТБ и др. на всех листах и проверит актуальность на normy.stn.by. Ответ приходит сразу по готовности; лимит по времени — до ~10 мин на 8 листов, не более 40 мин на запрос.</p>
    </div>
    <div class="steps">
      <div class="step-card"><div class="step-num">1</div><div class="step-body"><div class="step-title">Загрузите файл</div><div class="step-desc">PDF, скан или изображение листа</div></div></div>
      <div class="step-card"><div class="step-num">2</div><div class="step-body"><div class="step-title">OCR находит нормативы</div><div class="step-desc">Поиск по всему листу, без подстановок</div></div></div>
      <div class="step-card"><div class="step-num">3</div><div class="step-body"><div class="step-title">Проверка актуальности</div><div class="step-desc">Сверка с базой normy.stn.by</div></div></div>
    </div>
    <div class="welcome-actions">
      <button type="button" class="action-card action-card-primary" onclick="document.getElementById('file-input').click()">
        <span class="action-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg></span>
        <span class="action-text"><strong>Загрузить чертёж</strong><small>PDF, PNG, JPG и другие форматы</small></span>
      </button>
      <button type="button" class="action-card" onclick="setPrompt('Проверка ГОСТ на листе')">
        <span class="action-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg></span>
        <span class="action-text"><strong>Все ГОСТ на листе</strong><small>Быстрый запрос без ввода текста</small></span>
      </button>
    </div>`;
  inner.appendChild(welcome);
}

// ── models ────────────────────────────────────────────────────────────────────

async function loadModels() {
  // Model selector is intentionally hidden in UI now.
  if (!document.getElementById('model-select')) return;
  try {
    const d = await fetch('/api/models').then(r => r.json());
    const models = d.models && d.models.length ? d.models : [{id: 'gemma3:4b', label: 'Базовая'}];
    _modelMap = {};
    models.forEach(m => {
      const id = typeof m === 'string' ? m : m.id;
      const label = typeof m === 'string' ? m : m.label;
      _modelMap[id] = label;
    });
    renderModelDrop(models);
    if (models.length) {
      const first = typeof models[0] === 'string' ? models[0] : models[0].id;
      selectModel(first);
    }
  } catch {
    const statusText = document.getElementById('stat-status');
    const statusDot = document.getElementById('status-dot');
    if (statusText) statusText.textContent = 'Нет соединения';
    if (statusDot) statusDot.classList.add('off');
  }
}

async function loadSystemStatus() {
  try {
    const data = await fetch('/api/status').then(r => r.json());
    const banner = document.getElementById('system-banner');
    const modelSection = document.getElementById('model-section');
    if (data.gost_only && modelSection) modelSection.hidden = true;
    if (!banner) return;

    const stn = data.stn || {};
    if (!stn.enabled) {
      banner.hidden = false;
      banner.className = 'system-banner warn';
      banner.textContent = 'Проверка актуальности на normy.stn.by отключена. Будет показан только список нормативов с листа.';
    } else if (!stn.configured) {
      banner.hidden = false;
      banner.className = 'system-banner warn';
      banner.textContent = 'STN включён, но логин и пароль не указаны в .env. Список нормативов будет найден, актуальность может быть недоступна.';
    } else {
      banner.hidden = false;
      banner.className = 'system-banner ok';
      banner.textContent = 'STN подключён: актуальность нормативов будет проверяться на normy.stn.by.';
    }
  } catch (e) {
    const banner = document.getElementById('system-banner');
    if (banner) {
      banner.hidden = false;
      banner.className = 'system-banner warn';
      banner.textContent = 'Не удалось получить состояние сервера. Проверьте подключение.';
    }
  }
}

function renderModelDrop(models) {
  const drop = document.getElementById('model-sel-drop');
  if (!drop) return;
  drop.innerHTML = '';
  models.forEach(m => {
    const id = typeof m === 'string' ? m : m.id;
    const label = typeof m === 'string' ? m : m.label;
    const opt = document.createElement('div');
    opt.className = 'csel-opt';
    opt.dataset.id = id;
    opt.textContent = label;
    opt.addEventListener('click', () => { selectModel(id, true); closeDrop(); });
    drop.appendChild(opt);
  });
}

function selectModel(id, userChose = false) {
  const hidden = document.getElementById('model-select');
  const valueEl = document.getElementById('model-sel-val');
  if (hidden) hidden.value = id;
  if (valueEl) valueEl.textContent = _modelMap[id] || id;
  document.querySelectorAll('.csel-opt').forEach(opt => {
    opt.classList.toggle('active', opt.dataset.id === id);
  });
  if (userChose) {
    userOverrodeModel = true;
    clearAutoModelBadge();
  }
}

function toggleDrop() {
  const drop = document.getElementById('model-sel-drop');
  const trigger = document.getElementById('model-sel-trigger');
  if (!drop || !trigger) return;
  const isOpen = drop.classList.toggle('open');
  trigger.classList.toggle('open', isOpen);
}

function closeDrop() {
  const drop = document.getElementById('model-sel-drop');
  const trigger = document.getElementById('model-sel-trigger');
  if (drop) drop.classList.remove('open');
  if (trigger) trigger.classList.remove('open');
}

document.addEventListener('click', e => {
  if (!e.target.closest('#model-sel-wrap')) closeDrop();
});

// ── textarea ──────────────────────────────────────────────────────────────────

const ta = document.getElementById('input-field');
ta.addEventListener('input', () => {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 180) + 'px';
});
ta.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

// ── file handling ─────────────────────────────────────────────────────────────

document.getElementById('file-input').addEventListener('change', e => {
  if (e.target.files[0]) setFile(e.target.files[0]);
});

document.addEventListener('paste', e => {
  const items = (e.clipboardData || e.originalEvent?.clipboardData)?.items;
  if (!items) return;
  for (const item of items) {
    if (item.kind === 'file') {
      const f = item.getAsFile();
      if (f) { setFile(f); showToast('Файл вставлен: ' + f.name); }
      break;
    }
  }
});

function setFile(f, options = {}) {
  currentFile = f;
  document.getElementById('file-preview-name').textContent = f.name;
  document.getElementById('file-preview-size').textContent = fmtSize(f.size);
  document.getElementById('file-preview').classList.add('show');
  renderFilePreview(f);
  if (autoModelEnabled && !userOverrodeModel) {
    detectFileAndSelectModel(f);
  }
}

function isDrawingFile(f) {
  return Boolean(f && DRAWING_FILE_RE.test(f.name));
}

function renderFilePreview(f) {
  const box = document.getElementById('file-preview-render');
  if (!box) return;
  if (currentPreviewUrl) {
    URL.revokeObjectURL(currentPreviewUrl);
    currentPreviewUrl = '';
  }
  box.innerHTML = '';
  if (!isDrawingFile(f)) return;
  currentPreviewUrl = URL.createObjectURL(f);
  if (/\.(png|jpe?g|bmp|gif|webp)$/i.test(f.name)) {
    box.innerHTML = `<img src="${currentPreviewUrl}" alt="Предпросмотр файла">`;
  } else if (/\.pdf$/i.test(f.name)) {
    box.innerHTML = `<iframe src="${currentPreviewUrl}#page=1&view=FitH" title="Предпросмотр PDF"></iframe>`;
  } else {
    box.innerHTML = '<div class="preview-note">Предпросмотр для этого формата недоступен, файл будет обработан как изображение.</div>';
  }
}

async function detectFileAndSelectModel(f) {
  const ext = '.' + f.name.split('.').pop().toLowerCase();
  if (ext !== '.pdf') {
    await autoSelectModel(ext, document.getElementById('input-field').value.trim());
    return;
  }
  // PDF: отправляем на сервер — он проверит скан это или текст
  try {
    const fd = new FormData();
    fd.append('file', f);
    const data = await fetch('/api/detect-file', { method: 'POST', body: fd }).then(r => r.json());
    if (data.model && _modelMap[data.model]) {
      selectModel(data.model);
      showAutoModelBadge(data.model, data.reason);
    }
    if (data.page_count > 1 && data.budget_human) {
      const sizeEl = document.getElementById('file-preview-size');
      if (sizeEl) {
        sizeEl.textContent = `${fmtSize(f.size)} · ${data.page_count} лист. · ${data.budget_human}`;
      }
    }
  } catch(e) {
    await autoSelectModel(ext, document.getElementById('input-field').value.trim());
  }
}

function removeFile(options = {}) {
  const preservePreviewUrl = Boolean(options.preservePreviewUrl);
  currentFile = null;
  document.getElementById('file-input').value = '';
  document.getElementById('file-preview').classList.remove('show');
  const box = document.getElementById('file-preview-render');
  if (box) box.innerHTML = '';
  if (currentPreviewUrl && !preservePreviewUrl) {
    URL.revokeObjectURL(currentPreviewUrl);
    currentPreviewUrl = '';
  }
  clearAutoModelBadge();
}

function fmtSize(b) {
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b/1024).toFixed(1) + ' KB';
  return (b/1048576).toFixed(1) + ' MB';
}

// ── drag & drop ───────────────────────────────────────────────────────────────

let dragN = 0;
document.addEventListener('dragenter', e => { e.preventDefault(); dragN++; document.getElementById('drop-overlay').classList.add('show'); });
document.addEventListener('dragleave', e => { dragN--; if (dragN <= 0) { dragN = 0; document.getElementById('drop-overlay').classList.remove('show'); } });
document.addEventListener('dragover', e => e.preventDefault());
document.addEventListener('drop', e => {
  e.preventDefault(); dragN = 0;
  document.getElementById('drop-overlay').classList.remove('show');
  const f = e.dataTransfer.files[0];
  if (f) { setFile(f); showToast('Файл прикреплён: ' + f.name); }
});

// ── messages ──────────────────────────────────────────────────────────────────

function hideEmpty() {
  const el = document.getElementById('empty-state');
  if (el) el.style.display = 'none';
}

function escAttr(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function addMessage(role, content, badge, fileUrl) {
  hideEmpty();
  const wrap = document.getElementById('chat-inner');
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  const av = role === 'user'
    ? `<div class="msg-avatar">↑</div>`
    : `<div class="msg-avatar"><img src="/ico.png" alt="" style="width:20px;height:20px;object-fit:contain;border-radius:6px;"></div>`;
  const bd = badge
    ? `<div class="msg-user-stack"><div class="msg-file-badge"><span class="msg-file-name">${esc(badge)}</span>${fileUrl ? `<a class="msg-file-open" href="${escAttr(fileUrl)}" target="_blank" rel="noopener">Открыть</a>` : ''}</div><div class="msg-user-text">${esc(content)}</div></div>`
    : esc(content);
  div.innerHTML = `${av}<div class="msg-body"><div class="msg-role">${role === 'user' ? 'Вы' : 'БелнипиAI'}</div><div class="msg-content">${role === 'user' ? bd : ''}</div></div>`;
  wrap.appendChild(div);
  return div.querySelector('.msg-content');
}

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function scrollEnd(force = false) {
  const a = document.getElementById('chat-area');
  if (force || !userScrolled) {
    a.scrollTop = a.scrollHeight;
  }
}

// ── stop ──────────────────────────────────────────────────────────────────────

function stopStreaming() {
  if (abortController) { abortController.abort(); abortController = null; }
}

// ── send ──────────────────────────────────────────────────────────────────────

async function sendMessage() {
  if (isStreaming) return;
  const qRaw = ta.value.trim();
  const isDrawing = isDrawingFile(currentFile);
  const q = qRaw || (isDrawing ? GOST_DEFAULT_QUESTION : '');
  if (!q) return;

  // Авто-выбор для вопроса без файла (сложные запросы)
  if (autoModelEnabled && !userOverrodeModel && !currentFile) {
    await autoSelectModel('', q);
  }

  const model = getSelectedModel();
  const modelWasOverridden = userOverrodeModel;
  userOverrodeModel = false;
  clearAutoModelBadge();

  // Создаём чат если нет
  if (!currentConvId) {
    const data = await fetch('/api/conversations', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({model})
    }).then(r => r.json());
    currentConvId = data.id;
    await loadConversations();
    highlightActiveChat();
  }

  isStreaming = true;
  abortController = new AbortController();
  const btn = document.getElementById('send-btn');
  btn.classList.add('stop');
  btn.innerHTML = stopIcon;
  btn.onclick = stopStreaming;
  ta.value = ''; ta.style.height = 'auto';

  const fc = currentFile, fn = fc ? fc.name : null;
  const localFileUrl = currentPreviewUrl || null;
  lastRetryRequest = {question: q, file: fc, filename: fn};
  if (fc) removeFile({preservePreviewUrl: !!localFileUrl});

  userScrolled = false;
  addMessage('user', q, fn, localFileUrl); scrollEnd(true);
  if (localFileUrl && localFileUrl.startsWith('blob:')) {
    setTimeout(() => {
      try { URL.revokeObjectURL(localFileUrl); } catch (e) {}
    }, 10 * 60 * 1000);
  }
  const ac = addMessage('assistant', '');
  const progress = createProgress();
  ac.appendChild(progress);
  scrollEnd(true);

  const fd = new FormData();
  fd.append('question', q); fd.append('model', model);
  const isGostCheck = !/извлеч|весь текст|прочитай/i.test(q);
  if (isGostCheck) fd.append('mode', 'gost');
  const checkDateEl = document.getElementById('check-date');
  if (checkDateEl && checkDateEl.value) fd.append('check_date', checkDateEl.value);
  if (modelWasOverridden) fd.append('model_override', '1');
  if (fc) fd.append('file', fc);

  let raw = '', first = true;
  try {
    const res = await fetch(`/api/conversations/${currentConvId}/chat`, {
      method: 'POST', body: fd, signal: abortController.signal
    });
    const reader = res.body.getReader(), dec = new TextDecoder();
    while (true) {
      const {done, value} = await reader.read(); if (done) break;
      for (const line of dec.decode(value).split('\n')) {
        if (!line.startsWith('data: ')) continue;
        const p = line.slice(6); if (p === '[DONE]') break;
        try {
          const o = JSON.parse(p);
          if (o.error) {
            showToast(o.error, true);
            showInlineError(ac, o.error);
            first = false;
            break;
          }
          if (o.title) {
            updateChatTitle(currentConvId, o.title);
          }
          if (o.status) {
            updateProgress(progress, o);
            scrollEnd();
          }
          if (o.text) {
            if (first) { progress.remove(); first = false; }
            raw += o.text;
            ac.innerHTML = renderAssistantMarkdown(raw) + '<span class="cursor"></span>';
            addCodeBtns(ac); scrollEnd();
          }
        } catch(e) {}
      }
    }
  } catch(err) {
    if (err.name !== 'AbortError') {
      showInlineError(ac, 'Ошибка соединения с сервером');
      showToast('Ошибка соединения', true);
    }
  }

  const cur = ac.querySelector('.cursor'); if (cur) cur.remove();
  if (first) { progress.remove(); }
  if (raw) {
    ac.innerHTML = renderAssistantMarkdown(raw);
    addCodeBtns(ac);
    addMsgCopy(ac, ac.innerHTML);
    addReportActions(ac, ac.innerHTML);
  }

  abortController = null;
  isStreaming = false;
  btn.classList.remove('stop');
  btn.innerHTML = sendIcon;
  btn.onclick = sendMessage;
  ta.focus(); scrollEnd();
}

function showInlineError(el, message) {
  el.innerHTML = `
    <div class="inline-error">
      <strong>${esc(message)}</strong>
      <button class="report-btn retry-btn" onclick="retryLastMessage()">Повторить</button>
    </div>`;
}

function updateChatTitle(id, title) {
  const item = document.querySelector(`.chat-item[data-id="${id}"] .chat-item-title`);
  if (item) item.textContent = title;
}

// ── auto model ────────────────────────────────────────────────────────────────

async function autoSelectModel(fileExt, question) {
  if (!autoModelEnabled) return;
  try {
    const data = await fetch('/api/suggest-model', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({file_ext: fileExt, question})
    }).then(r => r.json());

    if (data.model && _modelMap[data.model]) {
      selectModel(data.model);
      showAutoModelBadge(data.model, data.reason);
    }
  } catch(e) {}
}

function showAutoModelBadge(model, reason) {
  const label = _modelMap[model] || model;

  let badge = document.getElementById('auto-model-badge');
  if (!badge) {
    badge = document.createElement('span');
    badge.id = 'auto-model-badge';
    const anchor = document.getElementById('model-sel-wrap') || document.querySelector('.field-label');
    anchor.appendChild(badge);
  }
  badge.className = 'auto-model-badge';
  badge.title = reason;
  badge.innerHTML = `<span class="auto-model-label">авто</span><span class="auto-model-name">${esc(label)}</span>`;
  badge.style.display = 'inline-flex';
}

function clearAutoModelBadge() {
  const badge = document.getElementById('auto-model-badge');
  if (badge) badge.style.display = 'none';
}

// ── helpers ───────────────────────────────────────────────────────────────────

function setPrompt(t) { ta.value = t; ta.focus(); ta.dispatchEvent(new Event('input')); }

function showToast(msg, err) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (err ? ' error' : '');
  setTimeout(() => t.classList.remove('show'), 3000);
}

async function loadCurrentUser() {
  try {
    const data = await fetch('/api/me').then(r => r.json());
    const login = (data.username || '').trim();
    const display = (data.display_name || '').trim();
    const userEl = document.getElementById('current-user');
    if (!userEl) return;
    if (login || display) {
      userEl.textContent = login ? `@${login}` : display;
      userEl.title = display && login ? `${display} (${login})` : (display || login);
    } else {
      userEl.textContent = 'Гость';
      userEl.title = 'Гость';
    }
  } catch (e) {
    const userEl = document.getElementById('current-user');
    if (userEl) {
      userEl.textContent = 'Гость';
      userEl.title = 'Гость';
    }
  }
}

// ── nav collapse ──────────────────────────────────────────────────────────────

function collapseNav() {
  document.querySelector('.shell').classList.add('nav-collapsed');
  document.querySelector('.nav')?.classList.add('collapsed');
  localStorage.setItem('navCollapsed', 'true');
}

function expandNav() {
  document.querySelector('.shell').classList.remove('nav-collapsed');
  document.querySelector('.nav')?.classList.remove('collapsed');
  localStorage.setItem('navCollapsed', 'false');
}

function toggleNav() {
  const shell = document.querySelector('.shell');
  const nav = document.querySelector('.nav');
  const isNowCollapsed = shell.classList.toggle('nav-collapsed');
  nav?.classList.toggle('collapsed', isNowCollapsed);
  localStorage.setItem('navCollapsed', isNowCollapsed ? 'true' : 'false');
}

function initNavState() {
  const saved = localStorage.getItem('navCollapsed');
  const isSmall = window.innerWidth < 900;
  if (isSmall) {
    if (saved === 'false') expandNav();
    else collapseNav();
    return;
  }
  if (saved === 'true') collapseNav();
  else expandNav();
}

let _wasSmallViewport = window.innerWidth < 900;
window.addEventListener('resize', () => {
  const isSmall = window.innerWidth < 900;
  if (isSmall !== _wasSmallViewport) {
    if (isSmall) collapseNav();
    else {
      const saved = localStorage.getItem('navCollapsed');
      if (saved === 'true') collapseNav();
      else expandNav();
    }
    _wasSmallViewport = isSmall;
  }
});

// ── delete all chats ──────────────────────────────────────────────────────────

async function deleteAllChats() {
  const items = document.querySelectorAll('.chat-item');
  if (!items.length) return;
  const confirmed = await confirmDialog('Удалить все чаты? Это действие нельзя отменить.', 'Удаление истории');
  if (!confirmed) return;
  const ids = Array.from(items).map(el => el.dataset.id);
  await Promise.all(ids.map(id => fetch(`/api/conversations/${id}`, { method: 'DELETE' })));
  currentConvId = null;
  clearFeed();
  loadConversations();
  showToast('Все чаты удалены');
}

function initCheckDate() {
  const el = document.getElementById('check-date');
  if (!el) return;
  const today = new Date();
  const y = today.getFullYear();
  const m = String(today.getMonth() + 1).padStart(2, '0');
  const d = String(today.getDate()).padStart(2, '0');
  el.value = `${y}-${m}-${d}`;
}

// ── init ──────────────────────────────────────────────────────────────────────

marked.use({ gfm: true });
initNavState();
initCheckDate();
loadModels();
loadConversations();
loadCurrentUser();
loadSystemStatus();
ta.focus();
// Починить уже открытый ответ (жирный текст, подпись листа) без нового скана.
document.querySelectorAll('.msg.assistant .msg-content').forEach(beautifyNormativeHtml);

// Останавливаем автоскролл если пользователь уходит вверх
document.getElementById('chat-area').addEventListener('scroll', () => {
  const a = document.getElementById('chat-area');
  userScrolled = a.scrollHeight - a.scrollTop - a.clientHeight > 80;
});

function showNormativePreviewPage(groupId, pageNo) {
  const pages = [...document.querySelectorAll(`.normative-preview-page[data-group="${groupId}"]`)];
  if (!pages.length) return false;
  let target = pages.find(p => String(p.dataset.page) === String(pageNo));
  if (!target) return false;
  pages.forEach(p => {
    const on = p === target;
    p.classList.toggle('is-active', on);
    p.hidden = !on;
  });
  const label = document.querySelector(`.preview-page-label[data-group="${groupId}"]`);
  if (label) {
    const idx = pages.indexOf(target) + 1;
    label.textContent = `${idx} / ${pages.length} · лист ${target.dataset.page}`;
  }
  return true;
}

function shiftNormativePreview(groupId, delta) {
  const pages = [...document.querySelectorAll(`.normative-preview-page[data-group="${groupId}"]`)];
  if (!pages.length) return;
  const cur = pages.findIndex(p => p.classList.contains('is-active'));
  const next = (cur + delta + pages.length) % pages.length;
  showNormativePreviewPage(groupId, pages[next].dataset.page);
}

document.addEventListener('click', (e) => {
  const zoomBtn = e.target.closest('.preview-zoom-btn');
  if (zoomBtn) {
    const targetId = zoomBtn.dataset.target;
    const img = document.getElementById(targetId);
    if (!img) return;
    const action = zoomBtn.dataset.action;
    const current = Number(img.dataset.scale || '1');
    let next = current;
    if (action === 'in') next = Math.min(4, current + 0.2);
    if (action === 'out') next = Math.max(0.5, current - 0.2);
    if (action === 'reset') next = 1;
    img.dataset.scale = String(next);
    img.style.transform = `scale(${next})`;
    return;
  }

  const pageBtn = e.target.closest('.preview-page-btn');
  if (pageBtn) {
    const group = pageBtn.dataset.group;
    if (!group) return;
    shiftNormativePreview(group, pageBtn.dataset.action === 'prev' ? -1 : 1);
    return;
  }

  const pdfBtn = e.target.closest('.normative-table-pdf-btn');
  if (pdfBtn) {
    e.preventDefault();
    downloadNormativeTablePdf(pdfBtn);
    return;
  }

  const row = e.target.closest('.normative-table-container tr[data-preview-page]');
  if (row && !e.target.closest('a')) {
    const pageNo = row.dataset.previewPage;
    const workspace = row.closest('.normative-workspace');
    const shell = workspace && workspace.querySelector('.normative-preview-shell');
    const group = shell && shell.dataset.previewGroup;
    if (group && pageNo) {
      const ok = showNormativePreviewPage(group, pageNo);
      if (ok) {
        workspace.querySelectorAll('.normative-table-container tr.row-preview-focus')
          .forEach(tr => tr.classList.remove('row-preview-focus'));
        row.classList.add('row-preview-focus');
      }
    }
  }
});

