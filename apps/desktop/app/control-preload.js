const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('artyControl', {
  stop: () => ipcRenderer.send('control-stop'),
});
