'use strict';

// Единый API-клиент для kuzmich-cockpit.
//
// Принцип методов:
// GET    = чтение (без побочных эффектов)
// PUT    = замена состояния / ресурса (идемпотентно)
// POST   = выполнение действия (не идемпотентно)
// DELETE = удаление

const API = {
  async get(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || r.statusText);
    return r.json();
  },
  async put(url, body) {
    const r = await fetch(url, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body || {})
    });
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || r.statusText);
    return r.json();
  },
  async post(url, body) {
    const r = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body || {})
    });
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || r.statusText);
    return r.json();
  },
  async del(url) {
    const r = await fetch(url, { method: 'DELETE' });
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || r.statusText);
    return r.json();
  },

  // --- Prompts ---
  listPrompts:      ()       => API.get('/api/prompts/list'),
  getActivePrompt:  ()       => API.get('/api/prompts/active'),
  getPrompt:        (n)      => API.get('/api/prompts/' + encodeURIComponent(n)),
  savePrompt:       (n, c)   => API.put('/api/prompts/' + encodeURIComponent(n), {content: c}),
  renamePrompt:     (o, n)   => API.put('/api/prompts/' + encodeURIComponent(o), {new_name: n}),
  selectPrompt:     (n)      => API.put('/api/prompts/' + encodeURIComponent(n) + '/select'),
  deletePrompt:     (n)      => API.del('/api/prompts/' + encodeURIComponent(n)),

  // --- Companion ---
  companionStatus:      ()       => API.get('/api/companion/status'),
  companionSetMode:     (m, c)   => API.put('/api/companion/mode', {mode: m, config: c}),
  companionSelect:      (m, c)   => API.put('/api/companion/select', {mode: m, config: c}),
  companionListConfigs: (m)      => API.get('/api/companion/configs/' + m),
  companionGetConfig:   (m, n)   => API.get('/api/companion/config/' + m + '/' + encodeURIComponent(n)),
  companionSaveConfig:  (m, n, c)=> API.put('/api/companion/config/' + m + '/' + encodeURIComponent(n), {content: c}),
  companionRenameConfig:(m, o, n)=> API.put('/api/companion/config/' + m + '/' + encodeURIComponent(o), {new_name: n}),
  companionDeleteConfig:(m, n)   => API.del('/api/companion/config/' + m + '/' + encodeURIComponent(n)),
  companionBaseConfig:  ()       => API.get('/api/companion/base_config'),
  companionModels:      (b)      => API.get('/api/companion/models/' + b),

  // --- Files ---
  listFiles:    (p)      => API.get('/api/files/' + (p || '')),
  readFile:     (p)      => API.get('/api/files/' + p),
  saveFile:     (p, c)   => API.put('/api/files/' + p, {content: c}),
  renameFile:   (p, n)   => API.put('/api/files/' + p, {new_name: n}),
  deleteFile:   (p)      => API.del('/api/files/' + p),
  mkdir:        (p, n)   => API.put('/api/files/' + p + '/mkdir', {name: n}),
  runFile:      (p)      => API.post('/api/files/run', {path: p, args: []}),
  downloadUrl:  (p)      => '/api/files/download?path=' + encodeURIComponent(p),
  async uploadFile(p, fd) {
    const r = await fetch('/api/files/upload?path=' + encodeURIComponent(p), {method: 'POST', body: fd});
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || r.statusText);
    return r.json();
  },

  // --- State ---
  setMode:  (m) => API.put('/api/state/mode', {mode: m}),
  setEstop: (e) => API.put('/api/state/estop', {engaged: e}),

  // --- Commands ---
  armCommand:  (a)   => API.put('/api/command/arm', {action: a}),
  handCommand: (p)   => API.put('/api/command/hand', p),
  headCommand: (p)   => API.put('/api/command/head', {payload: p}),
  ttsCommand:  (t)   => API.post('/api/command/tts', {text: t}),
  aiCommand:   (t, s)=> API.post('/api/command/ai', {text: t, source: s}),
  stopMove:    ()    => API.post('/api/movement/stop'),

  // --- Camera ---
  cameraStart:  () => API.put('/api/camera/start'),
  cameraStop:   () => API.put('/api/camera/stop'),
  cameraStatus: () => API.get('/api/camera/status'),

  // --- Teleop ---
  teleopStart:  () => API.put('/api/teleop/start'),
  teleopStop:   () => API.put('/api/teleop/stop'),
  teleopStatus: () => API.get('/api/teleop/status'),

  // --- Config ---
  getConfig: () => API.get('/api/config'),
};
