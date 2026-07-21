'use strict';
function parseOverride(text) {
  const result = {};
  let section = '';
  text.split('\n').forEach(line => {
    line = line.trim();
    if (!line || line.startsWith(';') || line.startsWith('#')) return;
    const m = line.match(/^\[(.+)\]$/);
    if (m) { section = m[1]; result[section] = {}; return; }
    const eq = line.indexOf('=');
    if (eq > 0 && section) {
      result[section][line.substring(0, eq).trim()] = line.substring(eq + 1).trim();
    }
  });
  return result;
}

function serializeOverride(ov) {
  const lines = [];
  Object.keys(ov).forEach(section => {
    const keys = ov[section];
    if (!keys || Object.keys(keys).length === 0) return;
    lines.push('[' + section + ']');
    Object.keys(keys).forEach(k => { lines.push(k + ' = ' + keys[k]); });
    lines.push('');
  });
  return lines.join('\n');
}
