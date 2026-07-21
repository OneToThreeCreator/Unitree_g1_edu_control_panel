'use strict';
const _modelCache = {};

async function fetchModels(backend) {
  if (_modelCache[backend]) return _modelCache[backend];
  try {
    const r = await fetch('/api/companion/models/' + backend);
    const d = await r.json();
    _modelCache[backend] = d.models || [];
  } catch (e) { _modelCache[backend] = []; }
  return _modelCache[backend];
}
