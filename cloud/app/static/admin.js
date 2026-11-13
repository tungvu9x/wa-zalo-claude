'use strict';
// Trang quan tri wa-zalo-daily. Moi noi dung tu tin nhan/file chen bang textContent (qua h()),
// khong dung innerHTML voi du lieu, tranh XSS tu tin nhan cua nguoi la.

const app = document.getElementById('app');
const timers = [];
let route = '';

// ------------------------------------------------------------------ tien ich DOM

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k === 'style') el.style.cssText = v;
    else if (k === 'value') el.value = v;
    else if (k === 'checked') el.checked = !!v;
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of kids.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// replaceChildren bien null thanh chu "null": loc truoc khi chen
function put(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));
}

const svgIcon = (d) => {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('width', '16'); s.setAttribute('height', '16'); s.setAttribute('viewBox', '0 0 24 24');
  s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', '2');
  s.setAttribute('stroke-linecap', 'round'); s.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS('http://www.w3.org/2000/svg', 'path'); p.setAttribute('d', d); s.append(p);
  return s;
};

async function api(path, opts = {}) {
  const init = { credentials: 'same-origin', method: opts.method || 'GET', headers: { 'x-wa-admin': '1' } };
  if (opts.body !== undefined) {
    init.headers['content-type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch('/admin/api' + path, init);
  if (res.status === 401) { location.href = '/admin/login'; throw new Error('Phiên đăng nhập hết hạn'); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map((x) => x.msg).join('; ') : res.statusText);
  }
  return data;
}

function toast(msg, err = false) {
  const t = h('div', { class: 'toast' + (err ? ' err' : ''), role: 'status' }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), err ? 6000 : 3000);
}

async function copy(text, label) {
  try { await navigator.clipboard.writeText(text); toast(`Đã sao chép ${label}`); }
  catch { window.prompt(`Sao chép ${label}:`, text); }
}

function every(ms, fn) { timers.push(setInterval(fn, ms)); }
function clearTimers() { while (timers.length) clearInterval(timers.pop()); }

const chLabel = (c) => (c === 'whatsapp' ? 'WhatsApp' : c === 'zalo' ? 'Zalo' : c);
const chBadge = (c) => h('span', { class: 'ch ch-' + c }, chLabel(c));
const fmtBytes = (n) => (n > 1e9 ? (n / 1e9).toFixed(1) + ' GB' : n > 1e6 ? (n / 1e6).toFixed(1) + ' MB' : Math.round(n / 1e3) + ' KB');
const todayISO = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);

const STATE = {
  ready: ['Đã kết nối', 'b-ok'], connecting: ['Đang kết nối', 'b-info'], starting: ['Đang khởi động', 'b-info'],
  authenticated: ['Đang kết nối', 'b-info'], qr_needed: ['Chờ quét QR', 'b-warn'],
  qr_scanned: ['Đã quét, chờ xác nhận', 'b-warn'], pair_code: ['Chờ nhập mã', 'b-warn'],
  disconnected: ['Mất kết nối', 'b-bad'], closed: ['Mất kết nối', 'b-bad'], auth_failure: ['Lỗi đăng nhập', 'b-bad'],
  login_failed: ['Lỗi đăng nhập', 'b-bad'], init_failed: ['Lỗi khởi động', 'b-bad'],
  logged_out: ['Đã đăng xuất', 'b-muted'], unknown: ['Chưa có dữ liệu', 'b-muted'],
};
const stateBadge = (s) => { const [t, c] = STATE[s] || [s, 'b-muted']; return h('span', { class: 'badge ' + c }, t); };

const DOCTYPE = { quote: 'Báo giá', price_table: 'Bảng giá', po: 'PO', invoice: 'Hóa đơn', other: 'Khác' };
const READ = {
  ok: ['Đã đọc', 'b-ok'], empty: ['PDF scan, cần OCR', 'b-warn'], failed: ['Lỗi đọc', 'b-bad'],
  unsupported: ['Không đọc được', 'b-bad'], pending: ['Đang chờ đọc', 'b-info'],
};
const extClass = (name) => {
  const e = (name || '').split('.').pop().toLowerCase();
  return [e === 'pdf' ? 'ext-pdf' : ['xlsx', 'xls', 'xlsm', 'csv'].includes(e) ? 'ext-xls' : 'ext-other', (e || '?').slice(0, 4).toUpperCase()];
};

// ------------------------------------------------------------------ khung xem hoi thoai

async function openChat(channel, chatId, title) {
  const msgs = h('div', { class: 'msgs' }, h('p', { class: 'muted' }, 'Đang tải…'));
  const close = () => back.remove();
  const back = h('div', { class: 'modal-back', onclick: (e) => { if (e.target === back) close(); } },
    h('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Hội thoại' },
      h('div', { class: 'head' }, h('div', {}, h('h2', {}, title || chatId), h('div', { class: 'muted small' }, chLabel(channel))),
        h('button', { class: 'btn', type: 'button', onclick: close }, 'Đóng')),
      msgs));
  document.body.append(back);
  document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); } });
  let before = null;
  const load = async () => {
    const q = new URLSearchParams({ channel, chat_id: chatId });
    if (before) q.set('before_id', before);
    const d = await api('/chat?' + q);
    const nodes = d.messages.map((m) => h('div', { class: 'msg' + (m.from === 'Toi' ? ' out' : '') },
      h('div', { class: 'meta' }, `${m.from === 'Toi' ? 'Mình' : m.from} · ${m.t}`),
      m.text || '',
      (m.files || []).map((f) => h('div', {}, h('a', { href: `/admin/files/${f.id}/download` }, '📎 ' + (f.name || 'file')))),
    ));
    if (!before) put(msgs, ...nodes); else msgs.prepend(...nodes);
    if (d.next_before_id) {
      before = d.next_before_id;
      msgs.prepend(h('button', { class: 'btn', type: 'button', onclick: (e) => { e.target.remove(); load(); } }, 'Tải tin cũ hơn'));
    }
    if (!d.messages.length) put(msgs, h('p', { class: 'muted' }, 'Không có tin nhắn.'));
  };
  load().then(() => { msgs.scrollTop = msgs.scrollHeight; }).catch((e) => put(msgs, h('p', { class: 'error' }, e.message)));
}

// ------------------------------------------------------------------ TONG QUAN

function loginCard(name, c, agentOnline, rerender) {
  const isWa = name === 'whatsapp';
  const needs = ['qr_needed', 'qr_scanned', 'pair_code', 'disconnected', 'closed', 'auth_failure', 'login_failed', 'logged_out', 'init_failed'].includes(c.state);
  const cmd = async (action, body = {}, confirmMsg) => {
    if (confirmMsg && !confirm(confirmMsg)) return;
    try { await api(`/auth/${name}/${action}`, { method: 'POST', body }); toast('Đã gửi lệnh tới agent'); rerender(); }
    catch (e) { toast(e.message, true); }
  };
  let box;
  if (c.qr) box = h('div', { class: 'qrbox' }, h('img', { src: c.qr, alt: `Mã QR đăng nhập ${chLabel(name)}` }));
  else if (c.pairing_code) box = h('div', { class: 'qrbox' }, h('div', {}, h('div', { class: 'muted small' }, 'Mã ghép nối'), h('div', { class: 'paircode' }, c.pairing_code)));
  else box = h('div', { class: 'qrbox muted small' }, c.state === 'ready' ? 'Đang kết nối, không cần quét mã' : 'Mã QR sẽ hiện ở đây khi cần đăng nhập');

  const steps = isWa
    ? (c.pairing_code
      ? ['Mở WhatsApp trên điện thoại', 'Thông báo "Nhập mã" hiện lên, hoặc: Thiết bị đã liên kết › Liên kết bằng số điện thoại', 'Nhập mã bên cạnh']
      : ['Mở WhatsApp trên điện thoại số cần thu tin', 'Cài đặt › Thiết bị đã liên kết › Liên kết thiết bị', 'Quét mã bên cạnh'])
    : ['Mở app Zalo trên điện thoại', 'Bấm biểu tượng QR ở thanh tìm kiếm', 'Quét mã và xác nhận đăng nhập'];

  const pairForm = h('form', { class: 'inline-form', style: 'display:none', onsubmit: (e) => {
    e.preventDefault(); cmd('pair', { phone: e.target.phone.value }, null);
  } },
  h('label', { class: 'muted small', for: 'pair-phone' }, 'Số điện thoại'),
  h('input', { id: 'pair-phone', name: 'phone', type: 'text', inputmode: 'tel', placeholder: '0901234567', required: true, style: 'width:160px' }),
  h('button', { class: 'btn primary', type: 'submit' }, 'Lấy mã'));

  const lc = c.last_command;
  const lcText = lc && lc.age < 900
    ? `Lệnh gần nhất: ${lc.action} · ${{ pending: 'chờ agent nhận', sent: 'agent đang xử lý', done: 'xong', failed: 'lỗi', expired: 'hết hạn (agent không chạy?)' }[lc.status] || lc.status}${lc.result ? ' · ' + lc.result : ''}`
    : null;

  return h('div', { class: 'card login-card' + (needs ? ' attn' : '') },
    box,
    h('div', { class: 'login-info' },
      h('div', { style: 'display:flex;justify-content:space-between;align-items:center;gap:8px' }, h('span', { class: 'name' }, chLabel(name)), stateBadge(c.state)),
      c.account ? h('div', {}, h('span', { class: 'muted' }, 'Tài khoản: '), c.account) : null,
      c.detail ? h('div', { class: 'muted small' }, c.detail) : null,
      needs || c.qr ? h('ol', {}, steps.map((s) => h('li', {}, s))) : null,
      !agentOnline ? h('div', { class: 'banner warn small' }, 'Agent chưa chạy: lệnh sẽ chờ tối đa 10 phút.') : null,
      lcText ? h('div', { class: 'muted small' }, lcText) : null,
      pairForm,
      h('div', { class: 'actions', style: 'margin-top:auto' },
        isWa ? [
          h('button', { class: 'btn primary', type: 'button', onclick: () => cmd('relogin', {}, c.state === 'ready' ? 'Gỡ liên kết hiện tại và tạo mã QR mới?' : null) }, c.state === 'ready' ? 'Liên kết lại' : 'Lấy mã QR mới'),
          h('button', { class: 'btn', type: 'button', onclick: () => { pairForm.style.display = pairForm.style.display === 'none' ? 'flex' : 'none'; } }, 'Đăng nhập bằng mã số'),
        ] : h('button', { class: 'btn primary', type: 'button', onclick: () => cmd('relogin', {}, c.state === 'ready' ? 'Đăng xuất Zalo trên máy này và quét lại QR?' : null) }, c.state === 'ready' ? 'Đăng nhập lại' : 'Lấy mã QR mới'),
        h('button', { class: 'btn danger', type: 'button', onclick: () => cmd('logout', {}, `Đăng xuất ${chLabel(name)} trên máy này? Agent sẽ ngừng thu tin kênh này.`) }, 'Đăng xuất'),
      )));
}

async function renderOverview() {
  const d = await api('/overview');
  document.getElementById('devname').textContent = 'Quản trị · ' + (d.device ? d.device.name : 'chưa có agent');
  const g = d.digest;
  const auth = d.auth;
  const loginWrap = h('div', { class: 'row' });
  let lastAuth = '';
  const drawLogin = (a, force = false) => {
    const key = JSON.stringify(a.connectors) + a.agent_online;
    // khong ve lai khi khong doi, hoac khi dang nhap so dien thoai
    if (!force && (key === lastAuth || loginWrap.contains(document.activeElement))) return;
    lastAuth = key;
    put(loginWrap, ...['whatsapp', 'zalo'].map((n) => loginCard(n, a.connectors[n], a.agent_online, refreshAuth)));
  };
  async function refreshAuth() {
    try { drawLogin(await api('/auth')); } catch {}
  }
  drawLogin(auth);
  every(3000, () => { if (route === 'overview') refreshAuth(); });

  const docs = d.stats.docs_by_status || {};
  const agentAge = auth.agent_seen_seconds_ago;
  const status = [
    ['Cloud', h('span', { class: 'badge b-ok' }, 'Đang chạy'), `PostgreSQL OK · ${d.timezone}`],
    ['Agent', auth.agent_online ? h('span', { class: 'badge b-ok' }, 'Online') : h('span', { class: 'badge b-bad' }, 'Offline'),
      agentAge == null ? 'Chưa kết nối lần nào' : `Heartbeat ${agentAge < 120 ? Math.round(agentAge) + ' giây' : Math.round(agentAge / 60) + ' phút'} trước`],
    ['WhatsApp', stateBadge(auth.connectors.whatsapp.state), auth.connectors.whatsapp.account || auth.connectors.whatsapp.detail || ''],
    ['Zalo', stateBadge(auth.connectors.zalo.state), auth.connectors.zalo.account || auth.connectors.zalo.detail || ''],
    ['Đọc file', (docs.empty || docs.failed || d.stats.docs_pending)
      ? h('span', { class: 'badge b-warn' }, `${(docs.empty || 0) + (docs.failed || 0)} cần xem`) : h('span', { class: 'badge b-ok' }, 'OK'),
    `${docs.ok || 0} đã đọc · ${d.stats.docs_pending} đang chờ · ${docs.empty || 0} cần OCR`],
  ];

  const quotes = g.conversations.flatMap((c) => c.files).filter((f) => f.doc_type === 'quote' || f.doc_type === 'price_table').length;
  const docFiles = g.conversations.flatMap((c) => c.files).filter((f) => f.kind).length;
  const kpis = [
    ['Hội thoại', g.chats, `${g.conversations.filter((c) => c.channel === 'zalo').length} Zalo · ${g.conversations.filter((c) => c.channel === 'whatsapp').length} WhatsApp`],
    ['Tin đến', g.messages_in, 'từ khách / đối tác'], ['Tin đi', g.messages_out, 'do mình gửi'],
    ['File', g.files, `${docFiles} PDF/Excel/CSV`], ['Báo giá', quotes, 'theo phân loại tự động'],
  ];

  const prompt = 'Dùng wa-zalo-daily: gọi daily_digest cho hôm nay, tổng hợp theo từng khách: khách hỏi gì, mình đã trả lời chưa, báo giá nào đã gửi/nhận (đọc nội dung bằng read_file nếu cần), và liệt kê việc cần làm tiếp.';

  put(app, 
    h('div', { class: 'pagehead' },
      h('div', {}, h('h1', {}, 'Tổng quan hôm nay'), h('p', {}, `${d.now} · ${d.timezone}`)),
      h('div', { class: 'actions noprint' },
        h('a', { class: 'btn lg', href: '#dang-nhap', onclick: (e) => { e.preventDefault(); document.getElementById('dang-nhap').scrollIntoView({ behavior: 'smooth' }); } }, 'Đăng nhập tài khoản'),
        h('button', { class: 'btn lg primary', type: 'button', onclick: () => copy(prompt, 'lệnh cho Claude') }, 'Tổng hợp ngày bằng Claude'))),
    !auth.agent_online ? h('div', { class: 'banner bad' }, 'Agent không chạy. Mở shortcut "WA-Zalo Daily" trên Desktop để bật lại; tin nhắn trong lúc agent tắt sẽ không được thu.') : null,
    h('section', { 'aria-labelledby': 'h-st' }, h('h2', { id: 'h-st', class: 'section-title' }, 'Trạng thái hệ thống'),
      h('div', { class: 'grid status' }, status.map(([n, b, t]) => h('div', { class: 'card stat' }, h('div', { class: 'top' }, n, b), h('span', { class: 'muted small' }, t))))),
    h('section', { id: 'dang-nhap', 'aria-labelledby': 'h-login' }, h('h2', { id: 'h-login', class: 'section-title' }, 'Đăng nhập tài khoản'), loginWrap),
    h('section', { 'aria-labelledby': 'h-kpi' }, h('h2', { id: 'h-kpi', class: 'section-title' }, 'Hôm nay'),
      h('div', { class: 'grid kpi' }, kpis.map(([l, v, s]) => h('div', { class: 'card stat kpi' }, h('div', { class: 'label' }, l), h('div', { class: 'value' }, v), h('div', { class: 'sub' }, s))))),
    h('div', { class: 'row' },
      h('section', { class: 'card grow', 'aria-labelledby': 'h-chats' },
        h('div', { class: 'head' }, h('h2', { id: 'h-chats' }, 'Hội thoại hôm nay')),
        g.conversations.length ? h('div', { class: 'scroll' }, h('table', {},
          h('thead', {}, h('tr', {}, h('th', {}, 'Chat'), h('th', {}, 'Kênh'), h('th', { class: 'num' }, 'Đến / đi'), h('th', { class: 'num' }, 'File'), h('th', { class: 'num' }, 'Tin cuối'), h('th', {}, h('span', { class: 'muted' }, '')))),
          h('tbody', {}, g.conversations.map((c) => {
            const last = c.messages[c.messages.length - 1];
            return h('tr', {},
              h('td', {}, h('div', { style: 'font-weight:600' }, c.chat_name || c.chat_id),
                last ? h('div', { class: 'muted small ellipsis' }, `${last.from === 'Toi' ? 'Mình' : last.from}: ${last.text || (last.files || []).join(', ')}`) : null),
              h('td', {}, chBadge(c.channel)), h('td', { class: 'num' }, `${c.in} / ${c.out}`),
              h('td', { class: 'num' }, c.files.length), h('td', { class: 'num muted' }, c.last),
              h('td', {}, h('button', { class: 'btn', type: 'button', onclick: () => openChat(c.channel, c.chat_id, c.chat_name) }, 'Xem')));
          })))) : h('div', { class: 'empty' }, 'Chưa có tin nhắn hôm nay.')),
      h('section', { class: 'card side', 'aria-labelledby': 'h-iss' },
        h('div', { class: 'head' }, h('h2', { id: 'h-iss' }, `Cần xử lý (${d.issues.length})`), h('span', { class: 'muted small' }, '7 ngày')),
        d.issues.length ? h('div', { class: 'list' }, d.issues.map((i) => {
          const [title, color, act] = i.upload === 'failed'
            ? [`Agent không tải được: ${i.upload_error || ''}`, '#B42318', null]
            : i.read === 'empty' ? ['PDF dạng ảnh scan, chưa có OCR', '#C26A00', h('a', { class: 'btn', href: `/admin/files/${i.id}/download` }, 'Tải gốc')]
              : [`Không đọc được: ${i.read_error || 'định dạng lạ hoặc file hỏng'}`, '#C26A00',
                h('button', { class: 'btn', type: 'button', onclick: async () => { try { await api(`/files/${i.id}/reextract`, { method: 'POST' }); toast('Đã xếp lịch đọc lại'); } catch (e) { toast(e.message, true); } } }, 'Đọc lại')];
          return h('div', {}, h('span', { class: 'dot', style: `background:${color}` }),
            h('div', { style: 'flex:1 1 auto;min-width:0' }, h('div', { style: 'font-weight:600;overflow-wrap:anywhere' }, i.file_name || 'file'),
              h('div', { class: 'muted small' }, `${title} · ${chLabel(i.channel)} · ${i.chat_name || ''} · ${i.t}`)), act);
        })) : h('div', { class: 'empty' }, 'Không có việc cần xử lý.'))),
    h('div', { class: 'row' },
      h('section', { class: 'card grow', 'aria-labelledby': 'h-mcp' },
        h('div', { class: 'head' }, h('h2', { id: 'h-mcp' }, 'Kết nối Claude (MCP)')),
        h('div', { class: 'body' }, h('div', { class: 'kv' },
          'Endpoint', h('span', { class: 'mono' }, d.mcp.endpoint),
          'Token', h('div', { class: 'actions' }, h('span', { class: 'mono' }, d.mcp.token_hint || 'chưa đặt MCP_TOKEN'),
            d.mcp.enabled ? h('button', { class: 'btn', type: 'button', onclick: async () => copy((await api('/mcp-token')).token, 'token') }, 'Sao chép') : null),
          'Claude Desktop', d.mcp.claude_desktop
            ? (d.mcp.claude_desktop.configured ? h('span', {}, 'Đã cấu hình · ', h('span', { class: 'muted small' }, d.mcp.claude_desktop.path))
              : h('span', { class: 'error' }, 'Chưa có cấu hình wa-zalo-daily, chạy lại install-windows.ps1'))
            : h('span', { class: 'muted' }, 'Cloud không chạy cùng máy Claude Desktop'),
          'claude.ai', h('span', { class: 'muted' }, 'Chưa bật (cần Cloudflare Tunnel + Access)')))),
      h('section', { class: 'card side', 'aria-labelledby': 'h-store' },
        h('div', { class: 'head' }, h('h2', { id: 'h-store' }, 'Lưu trữ')),
        h('div', { class: 'body' }, h('div', { class: 'kv' },
          'Dữ liệu', `${d.stats.messages} tin · ${d.stats.files} file (${fmtBytes(d.stats.bytes)})`,
          'Tin cũ nhất', d.stats.oldest || '—',
          'Giữ lại', `${d.stats.retention_days} ngày`),
        h('div', { class: 'actions' }, h('a', { class: 'btn', href: '#/settings' }, 'Cài đặt')))),
    ),
  );
  every(60000, () => {
    const busy = app.contains(document.activeElement) && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
    if (route === 'overview' && !busy && !document.querySelector('.modal-back')) render();
  });
}

// ------------------------------------------------------------------ FILE

const fileFilter = { q: '', since: '30d', channel: 'all', kind: 'documents', doc_type: 'any', selected: null };

async function renderFiles() {
  const list = h('section', { class: 'card grow', 'aria-label': 'Danh sách file' });
  const side = h('aside', { class: 'card side', 'aria-label': 'Nội dung file' }, h('div', { class: 'empty' }, 'Chọn một file để xem nội dung.'));
  let searchTimer;
  const sel = (name, opts) => h('select', { name, onchange: (e) => { fileFilter[name] = e.target.value; load(); } },
    opts.map(([v, t]) => h('option', { value: v, selected: fileFilter[name] === v }, t)));
  const chips = h('div', { class: 'chips' });
  const drawChips = () => put(chips, ...[['any', 'Tất cả'], ['quote', 'Báo giá'], ['po', 'PO'], ['invoice', 'Hóa đơn'], ['other', 'Khác']].map(([v, t]) =>
    h('button', { type: 'button', class: 'chip', 'aria-pressed': String(fileFilter.doc_type === v), onclick: () => { fileFilter.doc_type = v; drawChips(); load(); } }, t)));
  drawChips();

  put(app, 
    h('div', { class: 'pagehead' }, h('div', {}, h('h1', {}, 'File đính kèm'),
      h('p', {}, 'File từ WhatsApp và Zalo, đã đọc nội dung PDF/Excel. Phân loại báo giá đoán theo từ khóa, có thể sửa tay.'))),
    h('form', { class: 'card filters', onsubmit: (e) => e.preventDefault() },
      h('label', { class: 'field wide' }, 'Tìm trong tên và nội dung file',
        h('input', { type: 'search', value: fileFilter.q, placeholder: 'vd: MTN6005, DALI-2, Thảo Điền',
          oninput: (e) => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { fileFilter.q = e.target.value.trim(); load(); }, 350); } })),
      h('label', { class: 'field' }, 'Thời gian', sel('since', [['today', 'Hôm nay'], ['7d', '7 ngày'], ['30d', '30 ngày'], ['90d', '90 ngày'], ['365d', '1 năm']])),
      h('label', { class: 'field' }, 'Kênh', sel('channel', [['all', 'Tất cả'], ['whatsapp', 'WhatsApp'], ['zalo', 'Zalo']])),
      h('label', { class: 'field' }, 'Loại file', sel('kind', [['documents', 'PDF + Excel'], ['pdf', 'PDF'], ['excel', 'Excel/CSV'], ['all', 'Tất cả (cả ảnh)']])),
      h('fieldset', { style: 'border:0;padding:0;margin:0;flex:2 1 320px' }, h('legend', { class: 'muted', style: 'font-size:13px;margin-bottom:6px' }, 'Phân loại'), chips)),
    h('div', { class: 'row' }, list, side),
  );

  async function showFile(id) {
    fileFilter.selected = id;
    list.querySelectorAll('tr[data-id]').forEach((tr) => tr.classList.toggle('sel', tr.dataset.id === String(id)));
    put(side, h('div', { class: 'empty' }, 'Đang tải…'));
    try {
      const f = await api(`/files/${id}`);
      const meta = [f.kind && f.kind.toUpperCase(), f.pages && `${f.pages} trang`,
        f.sheets && `${f.sheets.length} sheet`, f.size && fmtBytes(f.size), f.hints && f.hints.length && `phân loại: ${f.hints.join(', ')}`].filter(Boolean).join(' · ');
      const typeSel = h('select', { 'aria-label': 'Phân loại' }, Object.entries(DOCTYPE).map(([v, t]) => h('option', { value: v, selected: f.doc_type === v }, t)));
      put(side, 
        h('div', { class: 'head', style: 'flex-direction:column;align-items:flex-start' },
          h('h2', { style: 'overflow-wrap:anywhere' }, f.file_name || `file #${id}`),
          h('div', { class: 'muted small' }, meta),
          h('div', { class: 'muted small' }, `${f.from === 'Toi' ? 'Mình' : f.from} · ${chLabel(f.channel)} · ${f.chat_name || f.chat_id} · ${f.t}`),
          f.note ? h('div', { class: 'banner warn small' }, f.note) : null),
        f.content ? h('pre', { class: 'content' }, f.content) : null,
        f.next_offset ? h('div', { class: 'muted small', style: 'padding:8px 18px' }, `Đang hiện ${f.content.length.toLocaleString('vi')} / ${f.total_chars.toLocaleString('vi')} ký tự. Tải file gốc để xem đủ.`) : null,
        h('div', { class: 'body' },
          h('div', { class: 'actions' },
            h('a', { class: 'btn primary', href: `/admin/files/${id}/download` }, 'Tải file gốc'),
            h('button', { class: 'btn', type: 'button', onclick: () => openChat(f.channel, f.chat_id, f.chat_name) }, 'Mở hội thoại'),
            f.kind ? h('button', { class: 'btn', type: 'button', onclick: async () => { try { await api(`/files/${id}/reextract`, { method: 'POST' }); toast('Đã xếp lịch đọc lại, xem lại sau ~30 giây'); } catch (e) { toast(e.message, true); } } }, 'Đọc lại') : null),
          f.read ? h('div', { class: 'inline-form' }, h('span', { class: 'muted small' }, 'Phân loại'), typeSel,
            h('button', { class: 'btn', type: 'button', onclick: async () => {
              try { await api(`/files/${id}/doc-type`, { method: 'POST', body: { doc_type: typeSel.value } }); toast('Đã lưu phân loại'); load(); } catch (e) { toast(e.message, true); }
            } }, 'Lưu')) : null));
    } catch (e) { put(side, h('div', { class: 'empty error' }, e.message)); }
  }

  async function load() {
    const q = new URLSearchParams({ since: fileFilter.since, channel: fileFilter.channel, kind: fileFilter.kind, doc_type: fileFilter.doc_type });
    if (fileFilter.q) q.set('q', fileFilter.q);
    put(list, h('div', { class: 'empty' }, 'Đang tải…'));
    try {
      const d = await api('/files?' + q);
      put(list, 
        h('div', { class: 'head' }, h('span', { class: 'muted' }, `${d.count} file${d.count >= 100 ? ' (tối đa 100, thu hẹp bộ lọc)' : ''}`)),
        d.files.length ? h('div', { class: 'scroll' }, h('table', { style: 'min-width:720px' },
          h('thead', {}, h('tr', {}, h('th', {}, 'File'), h('th', {}, 'Từ / chat'), h('th', {}, 'Thời gian'), h('th', {}, 'Đọc nội dung'), h('th', {}, 'Phân loại'))),
          h('tbody', {}, d.files.map((f) => {
            const [ec, et] = extClass(f.file_name);
            const rd = f.upload === 'failed' ? ['Không tải được', 'b-bad'] : READ[f.read] || ['—', 'b-muted'];
            return h('tr', { class: 'click' + (fileFilter.selected === f.id ? ' sel' : ''), 'data-id': f.id, onclick: () => showFile(f.id) },
              h('td', {}, h('div', { style: 'display:flex;gap:10px;align-items:center' }, h('span', { class: 'ext ' + ec }, et),
                h('button', { type: 'button', style: 'border:0;background:none;padding:0;font-weight:600;text-align:left;cursor:pointer;overflow-wrap:anywhere', onclick: (e) => { e.stopPropagation(); showFile(f.id); } }, f.file_name || `file #${f.id}`))),
              h('td', {}, h('div', {}, f.from === 'Toi' ? 'Mình' : f.from), h('div', { class: 'muted small' }, `${chLabel(f.channel)} · ${f.chat_name || f.chat_id}`)),
              h('td', { class: 'muted', style: 'white-space:nowrap' }, f.t),
              h('td', {}, h('span', { class: 'badge plain ' + rd[1] }, rd[0])),
              h('td', {}, f.doc_type ? h('span', { style: `font-weight:600;color:${f.doc_type === 'quote' || f.doc_type === 'price_table' ? 'var(--blue)' : 'var(--muted)'}` }, DOCTYPE[f.doc_type] || f.doc_type) : '—'));
          })))) : h('div', { class: 'empty' }, 'Không có file khớp bộ lọc.'));
    } catch (e) { put(list, h('div', { class: 'empty error' }, e.message)); }
  }
  await load();
  if (fileFilter.selected) showFile(fileFilter.selected);
}

// ------------------------------------------------------------------ BAO CAO

const rep = { kind: 'week', date: null, editing: false };
const DAYNAME = ['CN', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7'];

function shiftDate(iso, kind, dir) {
  const d = new Date(iso + 'T00:00:00');
  if (kind === 'week') d.setDate(d.getDate() + 7 * dir); else d.setMonth(d.getMonth() + dir, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function delta(cur, prev, { lowerBetter = false, unit = '' } = {}) {
  if (cur == null || prev == null) return ['', ''];
  const diff = cur - prev;
  if (Math.abs(diff) < 1e-9) return ['bằng kỳ trước', ''];
  const good = lowerBetter ? diff < 0 : diff > 0;
  const n = unit === 'phút' ? Math.round(Math.abs(diff) / 60) : Math.abs(diff);
  return [`${diff > 0 ? '+' : '−'}${n}${unit ? ' ' + unit : ''} so với kỳ trước`, good ? 'up' : 'down'];
}

async function renderReport() {
  const q = new URLSearchParams({ kind: rep.kind });
  if (rep.date) q.set('date', rep.date);
  const r = await api('/report?' + q);
  rep.date = r.start;
  const k = r.kpi, p = r.previous.kpi;
  const fmtD = (iso) => iso.split('-').reverse().slice(0, 2).join('/');
  const kpis = [
    ['Hội thoại', k.chats, delta(k.chats, p.chats)],
    ['Khách mới', k.new_chats, delta(k.new_chats, p.new_chats)],
    ['Tin đến / đi', `${k.messages_in} / ${k.messages_out}`, [k.reply_rate != null ? `tỉ lệ được trả lời ${Math.round(k.reply_rate * 100)}%` : 'chưa có tin đến', '']],
    ['Phản hồi trung vị', k.median_response || '—', delta(k.median_response_seconds, p.median_response_seconds, { lowerBetter: true, unit: 'phút' })],
    ['Báo giá gửi đi', k.quotes_out, delta(k.quotes_out, p.quotes_out)],
    ['Báo giá nhận về', k.quotes_in, delta(k.quotes_in, p.quotes_in)],
  ];
  const max = Math.max(1, ...r.daily.map((x) => x.in + x.out));
  const label = (iso) => { const d = new Date(iso + 'T00:00:00'); return r.kind === 'week' ? `${DAYNAME[d.getDay()]} ${d.getDate()}` : String(d.getDate()); };
  const wh = r.work_hours;
  const whText = `${wh.start}–${wh.end}, ${wh.days.map((d) => (d === 7 ? 'CN' : 'T' + (d + 1))).join(', ')}`;
  const prompt = `Dùng wa-zalo-daily: gọi period_report(period="${r.kind}", date="${r.start}"), đọc thêm hội thoại và báo giá cần thiết (get_chat_messages, read_file), viết nhận xét 4–8 gạch đầu dòng bằng tiếng Việt: khách/nhóm sản phẩm được hỏi nhiều, báo giá cần follow-up, khách chờ trả lời lâu, điểm bất thường. Sau đó lưu bằng save_report_note(period="${r.kind}", date="${r.start}").`;

  const noteBox = h('div', { class: 'body' });
  const drawNote = () => {
    if (rep.editing) {
      const ta = h('textarea', { 'aria-label': 'Nhận xét' }, r.note ? r.note.text : '');
      put(noteBox, ta, h('div', { class: 'actions' },
        h('button', { class: 'btn primary', type: 'button', onclick: async () => {
          try { await api('/report/note', { method: 'PUT', body: { kind: r.kind, date: r.start, text: ta.value } }); rep.editing = false; toast('Đã lưu nhận xét'); render(); } catch (e) { toast(e.message, true); }
        } }, 'Lưu'),
        h('button', { class: 'btn', type: 'button', onclick: () => { rep.editing = false; drawNote(); } }, 'Hủy')));
      ta.focus();
    } else {
      put(noteBox, 
        r.note ? h('div', { class: 'note' }, r.note.text) : h('p', { class: 'muted', style: 'margin:0' }, 'Chưa có nhận xét. Bấm "Sao chép lệnh cho Claude", dán vào Claude Desktop; Claude sẽ tự lưu nhận xét vào đây.'),
        r.note ? h('div', { class: 'muted small' }, `${r.note.author} · ${r.note.updated_at}`) : null,
        h('div', { class: 'actions noprint' },
          h('button', { class: 'btn', type: 'button', onclick: () => copy(prompt, 'lệnh cho Claude') }, 'Sao chép lệnh cho Claude'),
          h('button', { class: 'btn', type: 'button', onclick: () => { rep.editing = true; drawNote(); } }, r.note ? 'Sửa nhận xét' : 'Tự viết')));
    }
  };
  drawNote();

  put(app, 
    h('div', { class: 'pagehead' },
      h('div', {}, h('h1', {}, `Báo cáo ${r.title.toLowerCase()}`),
        h('p', {}, `${fmtD(r.start)} – ${fmtD(r.end)}/${r.end.slice(0, 4)} · so với ${fmtD(r.previous.start)} – ${fmtD(r.previous.end)} · giờ làm việc ${whText} `,
          h('a', { href: '#/settings', class: 'noprint' }, 'Sửa'))),
      h('div', { class: 'actions' },
        h('div', { class: 'seg', role: 'group', 'aria-label': 'Kỳ báo cáo' },
          [['week', 'Tuần'], ['month', 'Tháng']].map(([v, t]) => h('button', { type: 'button', 'aria-pressed': String(rep.kind === v), onclick: () => { rep.kind = v; render(); } }, t))),
        h('button', { class: 'btn lg', type: 'button', 'aria-label': 'Kỳ trước', onclick: () => { rep.date = shiftDate(r.start, r.kind, -1); render(); } }, '‹'),
        h('button', { class: 'btn lg', type: 'button', 'aria-label': 'Kỳ sau', onclick: () => { rep.date = shiftDate(r.start, r.kind, 1); render(); } }, '›'),
        h('button', { class: 'btn lg', type: 'button', onclick: () => { rep.date = todayISO(); render(); } }, 'Hiện tại'),
        h('button', { class: 'btn lg primary', type: 'button', onclick: () => window.print() }, 'Xuất PDF'))),
    h('section', { class: 'grid kpi', 'aria-label': 'Chỉ số chính' }, kpis.map(([l, v, [dt, dc]]) =>
      h('div', { class: 'card stat kpi' }, h('div', { class: 'label' }, l), h('div', { class: 'value' }, v), h('div', { class: 'sub ' + dc }, dt)))),
    h('div', { class: 'row' },
      h('section', { class: 'card grow', 'aria-labelledby': 'h-chart' },
        h('div', { class: 'head' }, h('h2', { id: 'h-chart' }, 'Tin nhắn theo ngày'),
          h('div', { class: 'legend' }, h('span', {}, h('i', { style: 'background:var(--blue)' }), 'Tin đến'), h('span', {}, h('i', { style: 'background:#E9A25F' }), 'Tin đi'))),
        h('div', { class: 'body scroll' },
          h('div', { class: 'chart', role: 'img', 'aria-label': 'Biểu đồ cột tin đến và tin đi theo ngày: ' + r.daily.map((x) => `${label(x.date)}: ${x.in} đến, ${x.out} đi`).join('; ') },
            r.daily.map((x) => h('div', { class: 'col' }, h('span', { class: 'n' }, x.in + x.out || ''),
              h('div', { class: 'out', style: `height:${Math.round(x.out / max * 180)}px` }), h('div', { class: 'in', style: `height:${Math.round(x.in / max * 180)}px` })))),
          h('div', { class: 'chart-x' }, r.daily.map((x) => h('span', {}, label(x.date)))))),
      h('section', { class: 'card side', 'aria-labelledby': 'h-note' },
        h('div', { class: 'head' }, h('h2', { id: 'h-note' }, 'Nhận xét')), noteBox)),
    h('div', { class: 'row' },
      h('section', { class: 'card grow', 'aria-labelledby': 'h-top' },
        h('div', { class: 'head' }, h('h2', { id: 'h-top' }, 'Hội thoại nhiều nhất')),
        r.top_chats.length ? h('div', { class: 'scroll' }, h('table', { style: 'min-width:620px' },
          h('thead', {}, h('tr', {}, h('th', {}, 'Chat'), h('th', {}, 'Kênh'), h('th', { class: 'num' }, 'Đến / đi'), h('th', { class: 'num' }, 'Báo giá'), h('th', { class: 'num' }, 'Phản hồi trung vị'))),
          h('tbody', {}, r.top_chats.map((c) => h('tr', { class: 'click', onclick: () => openChat(c.channel, c.chat_id, c.chat_name) },
            h('td', { style: 'font-weight:600' }, c.chat_name || c.chat_id), h('td', {}, chBadge(c.channel)),
            h('td', { class: 'num' }, `${c.messages_in} / ${c.messages_out}`), h('td', { class: 'num' }, c.quotes),
            h('td', { class: 'num' + (c.median_response_seconds > 3600 ? ' down' : '') }, c.median_response || '—')))))) : h('div', { class: 'empty' }, 'Không có hội thoại trong kỳ.')),
      h('section', { class: 'card side', 'aria-labelledby': 'h-wait' },
        h('div', { class: 'head' }, h('h2', { id: 'h-wait' }, `Chưa trả lời quá 24 giờ (${r.unanswered.length})`)),
        r.unanswered.length ? h('div', { class: 'list' }, r.unanswered.map((w) => h('div', { class: 'click', style: 'cursor:pointer', onclick: () => openChat(w.channel, w.chat_id, w.chat_name) },
          h('div', { style: 'flex:1 1 auto;min-width:0' }, h('div', { style: 'font-weight:600' }, w.chat_name || w.chat_id), h('div', { class: 'muted small ellipsis' }, w.last_message)),
          h('span', { class: 'down', style: 'font-weight:600;white-space:nowrap' }, w.waiting)))) : h('div', { class: 'empty' }, 'Không có.'))),
    h('section', { class: 'card', 'aria-labelledby': 'h-q' },
      h('div', { class: 'head' }, h('h2', { id: 'h-q' }, 'Báo giá trong kỳ'), h('a', { href: '#/files', class: 'noprint', onclick: () => { fileFilter.doc_type = 'quote'; } }, 'Xem tất cả file')),
      r.quotes.length ? h('div', { class: 'scroll' }, h('table', { style: 'min-width:720px' },
        h('thead', {}, h('tr', {}, h('th', {}, 'Ngày'), h('th', {}, 'Chiều'), h('th', {}, 'File'), h('th', {}, 'Chat'), h('th', {}, 'Phản hồi sau đó'))),
        h('tbody', {}, r.quotes.map((x) => h('tr', {},
          h('td', { class: 'muted', style: 'white-space:nowrap' }, x.date),
          h('td', {}, h('span', { class: 'ch ' + (x.direction === 'out' ? 'b-warn' : 'b-info') }, x.direction === 'out' ? 'Gửi đi' : 'Nhận về')),
          h('td', { style: 'font-weight:600;overflow-wrap:anywhere' }, h('a', { href: `/admin/files/${x.attachment_id}/download` }, x.file_name || 'file')),
          h('td', {}, x.chat_name || x.chat_id), h('td', { class: x.late ? 'down' : '' }, x.follow)))))) : h('div', { class: 'empty' }, 'Không có file được phân loại là báo giá trong kỳ.')),
  );
}

// ------------------------------------------------------------------ CAI DAT

async function renderSettings() {
  const [s, o] = await Promise.all([api('/settings'), api('/overview')]);
  const days = [[1, 'T2'], [2, 'T3'], [3, 'T4'], [4, 'T5'], [5, 'T6'], [6, 'T7'], [7, 'CN']];
  const form = h('form', { class: 'body', onsubmit: async (e) => {
    e.preventDefault();
    const f = e.target;
    const body = {
      work_days: days.map(([v]) => v).filter((v) => f['d' + v].checked),
      work_start: f.work_start.value, work_end: f.work_end.value, retention_days: Number(f.retention_days.value),
    };
    try { await api('/settings', { method: 'PUT', body }); toast('Đã lưu cài đặt'); } catch (err) { toast(err.message, true); }
  } },
  h('fieldset', { style: 'border:0;padding:0;margin:0' }, h('legend', { style: 'font-weight:600;margin-bottom:8px' }, 'Ngày làm việc'),
    h('div', { class: 'chips' }, days.map(([v, t]) => h('label', { class: 'chip', style: 'display:inline-flex;gap:6px;align-items:center' },
      h('input', { type: 'checkbox', name: 'd' + v, checked: s.work_days.includes(v) }), t)))),
  h('div', { class: 'actions' },
    h('label', { class: 'field' }, 'Bắt đầu', h('input', { type: 'time', name: 'work_start', value: s.work_start, required: true })),
    h('label', { class: 'field' }, 'Kết thúc', h('input', { type: 'time', name: 'work_end', value: s.work_end, required: true })),
    h('label', { class: 'field' }, 'Giữ dữ liệu (ngày)', h('input', { type: 'number', name: 'retention_days', min: 7, max: 3650, value: s.retention_days, required: true, style: 'width:140px' }))),
  h('p', { class: 'muted small', style: 'margin:0' }, `Thời gian phản hồi trong báo cáo chỉ tính phần nằm trong giờ làm việc. Múi giờ: ${s.timezone}. Tin cũ hơn số ngày lưu sẽ bị xóa mỗi giờ.`),
  h('div', {}, h('button', { class: 'btn primary', type: 'submit' }, 'Lưu cài đặt')));

  put(app, 
    h('div', { class: 'pagehead' }, h('h1', {}, 'Cài đặt')),
    h('section', { class: 'card', 'aria-labelledby': 'h-wh' }, h('div', { class: 'head' }, h('h2', { id: 'h-wh' }, 'Giờ làm việc & lưu trữ')), form),
    h('section', { class: 'card', 'aria-labelledby': 'h-dev' }, h('div', { class: 'head' }, h('h2', { id: 'h-dev' }, 'Thiết bị (agent)')),
      h('div', { class: 'scroll' }, h('table', {},
        h('thead', {}, h('tr', {}, h('th', {}, 'Tên'), h('th', {}, 'Phiên bản'), h('th', {}, 'Lần cuối thấy'), h('th', {}, 'Trạng thái'), h('th', {}, ''))),
        h('tbody', {}, o.devices.map((d) => h('tr', {},
          h('td', { style: 'font-weight:600' }, d.name), h('td', {}, d.agent_version || '—'), h('td', { class: 'muted' }, d.last_seen || 'chưa kết nối'),
          h('td', {}, d.revoked ? h('span', { class: 'badge b-muted' }, 'Đã thu hồi') : h('span', { class: 'badge b-ok' }, 'Đang dùng')),
          h('td', {}, d.revoked ? null : h('button', { class: 'btn danger', type: 'button', onclick: async () => {
            if (!confirm(`Thu hồi thiết bị ${d.name}? Agent trên máy đó sẽ không gửi được tin nữa cho tới khi cài lại.`)) return;
            try { await api(`/devices/${d.id}/revoke`, { method: 'POST' }); toast('Đã thu hồi'); render(); } catch (e) { toast(e.message, true); }
          } }, 'Thu hồi')))))))),
  );
}

// ------------------------------------------------------------------ router

const PAGES = { overview: renderOverview, files: renderFiles, report: renderReport, settings: renderSettings };

async function render() {
  clearTimers();
  route = (location.hash.match(/^#\/(\w+)/) || [])[1] || 'overview';
  if (!PAGES[route]) route = 'overview';
  document.querySelectorAll('nav.tabs a').forEach((a) => {
    const on = a.dataset.tab === route;
    a.classList.toggle('active', on);
    if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  try {
    await PAGES[route]();
  } catch (e) {
    put(app, h('div', { class: 'banner bad' }, 'Lỗi tải trang: ' + e.message),
      h('button', { class: 'btn', type: 'button', onclick: render }, 'Thử lại'));
  }
}
window.addEventListener('hashchange', render);
render();
