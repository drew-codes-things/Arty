'use strict';

const BRIDGE = 'http://127.0.0.1:8765';

async function api(path, options = {}) {
  const token = window.arty?.token() || '';
  if (!token) throw new Error('Arty’s local service hasn’t proven itself yet, so nothing was sent');
  return fetch(`${BRIDGE}${path}`, { ...options, headers: { ...(options.headers || {}), 'X-Arty-Token': token } });
}
