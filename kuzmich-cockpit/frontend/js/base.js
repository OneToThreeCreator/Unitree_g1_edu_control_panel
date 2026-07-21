'use strict';
const $ = s => document.querySelector(s);

function showStatus(el, msg, type) {
  type = type || 'ok';
  el.textContent = msg;
  el.className = 'status ' + type;
  setTimeout(() => { el.className = 'status hidden'; }, 3000);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
}

function throttle(fn, ms) {
  let last = 0, timer = null;
  return v => {
    const now = Date.now();
    if (now - last >= ms) { last = now; fn(v); }
    else { clearTimeout(timer); timer = setTimeout(() => { last = Date.now(); fn(v); }, ms - (now - last)); }
  };
}

function bindCtrlSSave(saveBtn) {
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); saveBtn.click(); }
  });
}
