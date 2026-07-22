'use strict';
const _modelCache = {};

async function fetchModels(backend) {
  if (_modelCache[backend]) return _modelCache[backend];
  try {
    _modelCache[backend] = await API.companionModels(backend);
  } catch (e) { _modelCache[backend] = []; }
  return _modelCache[backend];
}
