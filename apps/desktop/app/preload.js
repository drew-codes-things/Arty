const { contextBridge, ipcRenderer } = require('electron');

// The little the window needs from Electron; everything else goes to the local service over HTTP.
contextBridge.exposeInMainWorld('arty', {
  hide: () => ipcRenderer.send('hide'),
  show: () => ipcRenderer.send('show'),
  // Read fresh each time: the local service makes a new token whenever it restarts.
  token: () => ipcRenderer.sendSync('token'),
  onListen: (callback) => ipcRenderer.on('listen', () => callback()),
  onLook: (callback) => ipcRenderer.on('look', () => callback()),
  onExplain: (callback) => ipcRenderer.on('explain', (_event, copied) => callback(copied)),
  onTransparency: (callback) => ipcRenderer.on('transparency', (_event, solidity) => callback(solidity)),
  onConnect: (callback) => ipcRenderer.on('connect', () => callback()),
});
