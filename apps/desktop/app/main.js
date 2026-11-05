const { app, BrowserWindow, Menu, Tray, clipboard, globalShortcut, ipcMain, screen, dialog } = require('electron');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const dependencies = require('./dependencies');

// The window stays on top and goes where it was left, which a native Wayland window
// can't ask KDE for, so it runs as an X11 client (XWayland). The flag has to be on the
// command line, so a development run restarts itself with it. ARTY_WAYLAND=1 opts out.
const insideAppImage = /\/\.mount_/.test(process.execPath);
if (app.commandLine.getSwitchValue('ozone-platform') !== 'x11' && !process.env.ARTY_WAYLAND && !insideAppImage) {
  app.relaunch({ args: [...process.argv.slice(1), '--ozone-platform=x11'] });
  app.exit(0);
}

const BRIDGE_URL = 'http://127.0.0.1:8765';
const WINDOW_SIZE = { width: 400, height: 640 };
const SCREEN_MARGIN = 24;
const RESTART_DELAYS_MS = [1000, 2000, 5000, 10000, 30000];
const HEALTH_CHECK_MS = 5000;
const STUCK_AFTER_MS = 45000;
const LOG_MAX_BYTES = 5 * 1024 * 1024;
// Tray menu > Transparency: how solid the window's background is (the words stay solid).
const TRANSPARENCY_CHOICES = [
  { label: 'Solid', solidity: 1 },
  { label: 'Slightly see-through', solidity: 0.85 },
  { label: 'See-through', solidity: 0.7 },
  { label: 'Very see-through', solidity: 0.5 },
];
const DEFAULT_SOLIDITY = 0.85;
const EXPLAIN_MAX_CHARS = 6000;
const INSTALL_OUTPUT_CHARS = 1500;
const PKEXEC_CANCELLED = new Set([126, 127]);

let win = null;
let tray = null;
let bridge = null;
let quitting = false;
let restartCount = 0;
let restartTimer = null;
let lastHealthy = Date.now();
let solidity = DEFAULT_SOLIDITY;

// ---------- the local service (server/arty_server.py) ----------

function backend() {
  if (app.isPackaged) {
    const root = path.join(process.resourcesPath, 'backend');
    return { root, data: app.getPath('userData'), python: path.join(root, 'python', 'bin', 'python3'), script: path.join(root, 'server', 'arty_server.py') };
  }
  const root = path.resolve(__dirname, '..');
  return { root, data: root, python: path.join(root, '.venv', 'bin', 'python'), script: path.join(root, 'server', 'arty_server.py') };
}

/** The local service's access token (see only_local_callers in server/arty_server.py). */
function serviceToken() {
  try {
    return fs.readFileSync(path.join(backend().data, '.arty_token'), 'utf8').trim();
  } catch {
    return ''; // not started yet
  }
}

async function bridgeAlive() {
  try {
    const response = await fetch(`${BRIDGE_URL}/health`, { signal: AbortSignal.timeout(2000), headers: { 'X-Arty-Token': serviceToken() } });
    return response.ok;
  } catch {
    return false;
  }
}

async function startBridge() {
  if (await bridgeAlive()) return; // a dev run or an earlier copy already has it
  const { root, data, python, script } = backend();
  if (!fs.existsSync(python) || !fs.existsSync(script)) {
    dialog.showErrorBox('Arty can’t find its local service', `Expected:\n  ${python}\n  ${script}\n\nRun ./setup-linux.sh in the project folder.`);
    return;
  }
  const logPath = path.join(app.getPath('userData'), 'service.log');
  let output = 'ignore';
  try {
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    // Keep it from growing forever: start afresh past LOG_MAX_BYTES, keeping one older copy.
    if (fs.existsSync(logPath) && fs.statSync(logPath).size > LOG_MAX_BYTES) fs.renameSync(logPath, `${logPath}.1`);
    output = fs.openSync(logPath, 'a');
    fs.writeSync(output, `\n--- started ${new Date().toISOString()} ---\n`);
  } catch {
    output = 'ignore';
  }
  bridge = spawn(python, [script], {
    cwd: root,
    stdio: ['ignore', output, output],
    env: { ...process.env, PYTHONUNBUFFERED: '1', ARTY_ROOT: root, ARTY_DATA: data },
  });
  bridge.on('exit', (code) => {
    bridge = null;
    if (!quitting) scheduleRestart(code);
  });
}

function run(command, args, options = {}) {
  return new Promise((resolve) => {
    let output = '';
    let child;
    try {
      child = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'], ...options });
    } catch (error) {
      resolve({ code: -1, output: String(error) });
      return;
    }
    child.stdout.on('data', (chunk) => { output += chunk; });
    child.stderr.on('data', (chunk) => { output += chunk; });
    child.on('error', (error) => resolve({ code: -1, output: output + String(error) }));
    child.on('close', (code) => resolve({ code, output }));
  });
}

async function updatePythonPackages() {
  if (app.isPackaged) return;
  const { root, python } = backend();
  const requirements = path.join(root, 'server', 'requirements-linux.txt');
  const stamp = path.join(root, '.venv', '.arty-requirements');
  if (!fs.existsSync(python) || !fs.existsSync(requirements) || !dependencies.requirementsChanged(requirements, stamp)) return;
  console.log('Arty: installing the local service\'s Python packages (requirements changed)');
  const result = await run(python, ['-m', 'pip', 'install', '--quiet', '-r', requirements], { cwd: root });
  if (result.code !== 0) {
    dialog.showErrorBox('Arty couldn’t update its Python packages', `pip said:\n${result.output.slice(-INSTALL_OUTPUT_CHARS)}`);
    return;
  }
  fs.writeFileSync(stamp, dependencies.requirementsStamp(requirements));
}

const skippedFile = () => path.join(app.getPath('userData'), 'skipped-dependencies.json');

function skippedDependencies() {
  try {
    return JSON.parse(fs.readFileSync(skippedFile(), 'utf8'));
  } catch {
    return [];
  }
}

function skipDependencies(items) {
  const skipped = new Set([...skippedDependencies(), ...items.map((item) => item.id)]);
  fs.writeFileSync(skippedFile(), JSON.stringify([...skipped]));
}

async function installMissingDependencies() {
  if (process.platform !== 'linux') return;
  const skipped = skippedDependencies();
  const needed = dependencies.missing().filter((item) => !skipped.includes(item.id));
  if (!needed.length) return;
  const manager = dependencies.packageManager();
  const list = needed.map((item) => `• ${item.package}, ${item.purpose}`).join('\n');
  if (!manager || !dependencies.systemProbe().has('pkexec')) {
    const typed = manager ? dependencies.manualCommand(manager, needed) : `Install with your package manager: ${needed.map((item) => item.package).join(', ')}`;
    const { response } = await dialog.showMessageBox({
      type: 'warning', buttons: ['OK', 'Don’t ask again'], defaultId: 0, cancelId: 0, title: 'Arty needs a few things',
      message: 'Arty is missing some things it needs', detail: `${list}\n\nRun this in a terminal:\n${typed}`,
    });
    if (response === 1) skipDependencies(needed);
    return;
  }
  const { response } = await dialog.showMessageBox({
    type: 'question', buttons: ['Install', 'Not now', 'Don’t ask again'], defaultId: 0, cancelId: 1,
    title: 'Arty needs a few things', message: 'Arty is missing some things it needs',
    detail: `${list}\n\nInstall them now? Your computer will ask for your password.`,
  });
  if (response === 2) skipDependencies(needed);
  if (response !== 0) return;
  const [command, ...args] = dependencies.installCommand(manager, needed);
  const result = await run(command, args);
  if (PKEXEC_CANCELLED.has(result.code)) return;
  if (result.code !== 0) {
    dialog.showErrorBox('Arty couldn’t install them', `${result.output.slice(-INSTALL_OUTPUT_CHARS)}\n\nTry it in a terminal:\n${dependencies.manualCommand(manager, needed)}`);
    return;
  }
  if (bridge) bridge.kill('SIGTERM');
  dialog.showMessageBox({ type: 'info', buttons: ['OK'], title: 'Arty', message: 'Installed. Arty has restarted its local service to use them.' });
}

function scheduleRestart(code) {
  if (restartTimer) return;
  if (Date.now() - lastHealthy > 60000) restartCount = 0;
  if (restartCount >= RESTART_DELAYS_MS.length) {
    console.error(`Arty's local service keeps stopping (last exit ${code}). See ${path.join(app.getPath('userData'), 'service.log')}`);
    return;
  }
  restartTimer = setTimeout(async () => {
    restartTimer = null;
    await startBridge();
  }, RESTART_DELAYS_MS[restartCount++]);
}

function watchBridge() {
  setInterval(async () => {
    if (quitting) return;
    if (await bridgeAlive()) {
      restartCount = 0;
      lastHealthy = Date.now();
    } else if (!bridge && !restartTimer) {
      // It wasn't started by this window (it was already running), and now it's gone.
      await startBridge();
    } else if (bridge && Date.now() - lastHealthy > STUCK_AFTER_MS) {
      lastHealthy = Date.now();
      try { bridge.kill('SIGKILL'); } catch { /* already gone */ }
    }
  }, HEALTH_CHECK_MS);
}

// ---------- the window ----------

const stateFile = () => path.join(app.getPath('userData'), 'window-state.json');

// ---------- start with the PC (a freedesktop autostart entry, which KDE runs at login) ----------

const autostartFile = () => path.join(app.getPath('home'), '.config', 'autostart', 'arty-desktop.desktop');

function autostartCommand() {
  if (process.env.APPIMAGE) return `"${process.env.APPIMAGE}"`;
  if (app.isPackaged) return `"${process.execPath}"`;
  return `"${path.join(__dirname, 'node_modules', '.bin', 'electron')}" "${__dirname}" --ozone-platform=x11`;
}

function setAutostart(enabled) {
  try {
    if (!enabled) {
      fs.rmSync(autostartFile(), { force: true });
      return;
    }
    fs.mkdirSync(path.dirname(autostartFile()), { recursive: true });
    const entry = [
      '[Desktop Entry]', 'Type=Application', 'Name=Arty', 'Comment=Arty, your assistant',
      `Exec=${autostartCommand()}`, `Icon=${path.join(__dirname, 'assets', 'icon.png')}`,
      'X-GNOME-Autostart-enabled=true', 'X-KDE-autostart-after=panel',
    ];
    fs.writeFileSync(autostartFile(), `${entry.join('\n')}\n`);
  } catch (error) {
    dialog.showErrorBox('Couldn’t change “Start with the PC”', String(error));
  }
}

function savedBounds() {
  try {
    const saved = JSON.parse(fs.readFileSync(stateFile(), 'utf8'));
    const visible = screen.getAllDisplays().some(({ workArea: area }) =>
      saved.x >= area.x - 50 && saved.y >= area.y - 50 && saved.x < area.x + area.width - 50 && saved.y < area.y + area.height - 50);
    if (typeof saved.solidity === 'number') solidity = saved.solidity;
    if (visible) return saved;
  } catch { /* first run */ }
  const area = screen.getPrimaryDisplay().workArea;
  return {
    x: area.x + area.width - WINDOW_SIZE.width - SCREEN_MARGIN,
    y: area.y + area.height - WINDOW_SIZE.height - SCREEN_MARGIN,
    ...WINDOW_SIZE,
  };
}

function saveBounds() {
  if (!win || win.isDestroyed()) return;
  try {
    fs.writeFileSync(stateFile(), JSON.stringify({ ...win.getBounds(), onTop: win.isAlwaysOnTop(), solidity }));
  } catch { /* not worth failing over */ }
}

function createWindow() {
  const bounds = savedBounds();
  win = new BrowserWindow({
    x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height,
    minWidth: 320,
    minHeight: 420,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    alwaysOnTop: bounds.onTop !== false,
    title: 'Arty',
    icon: path.join(__dirname, 'assets', 'icon.png'),
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false },
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  win.webContents.on('did-finish-load', () => win.webContents.send('transparency', solidity));
  win.on('moved', saveBounds);
  win.on('resized', saveBounds);
  win.on('close', (event) => {
    if (quitting) return;
    event.preventDefault(); // closing hides it; quit from the tray
    win.hide();
  });
}

function showAndListen() {
  if (!win) return;
  win.show();
  win.focus();
  win.webContents.send('listen');
}

function showAndLook() {
  if (!win) return;
  win.show();
  win.focus();
  win.webContents.send('look');
}

/** Ctrl+Alt+E: Arty explains what was copied (or, failing that, what is highlighted). Only read on request. */
function showAndExplain() {
  if (!win) return;
  const copied = clipboard.readText().trim() || clipboard.readText('selection').trim();
  win.show();
  win.focus();
  win.webContents.send('explain', copied.slice(0, EXPLAIN_MAX_CHARS));
}

function toggleWindow() {
  if (!win) return;
  if (win.isVisible() && win.isFocused()) win.hide();
  else { win.show(); win.focus(); }
}

function buildTray() {
  tray = new Tray(path.join(__dirname, 'assets', 'tray.png'));
  tray.setToolTip('Arty');
  const menu = () => Menu.buildFromTemplate([
    { label: 'Show Arty', accelerator: 'CommandOrControl+Shift+A', click: () => { win.show(); win.focus(); } },
    { label: 'Talk to Arty', accelerator: 'CommandOrControl+Shift+Space', click: showAndListen },
    { label: 'Ask about my screen', accelerator: 'CommandOrControl+Alt+Space', click: showAndLook },
    { label: 'Explain what I copied', accelerator: 'CommandOrControl+Alt+E', click: showAndExplain },
    { type: 'separator' },
    {
      label: 'Keep on top', type: 'checkbox', checked: win.isAlwaysOnTop(),
      click: (item) => { win.setAlwaysOnTop(item.checked); saveBounds(); },
    },
    {
      label: 'Transparency',
      submenu: TRANSPARENCY_CHOICES.map((choice) => ({
        label: choice.label, type: 'radio', checked: choice.solidity === solidity,
        click: () => setSolidity(choice.solidity),
      })),
    },
    { label: 'Start with the PC', type: 'checkbox', checked: fs.existsSync(autostartFile()), click: (item) => setAutostart(item.checked) },
    { label: 'Connect to another Arty…', click: () => { win.show(); win.focus(); win.webContents.send('connect'); } },
    { type: 'separator' },
    { label: 'Quit Arty', click: () => app.quit() },
  ]);
  tray.setContextMenu(menu());
  tray.on('click', toggleWindow);
  win.on('always-on-top-changed', () => tray.setContextMenu(menu()));
}

function setSolidity(value) {
  solidity = value;
  win?.webContents.send('transparency', solidity);
  saveBounds();
}

ipcMain.on('hide', () => win?.hide());
ipcMain.on('show', () => { win?.show(); win?.focus(); });
ipcMain.on('token', (event) => { event.returnValue = serviceToken(); });

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => { win?.show(); win?.focus(); });
  app.whenReady().then(async () => {
    await updatePythonPackages();
    await startBridge();
    watchBridge();
    createWindow();
    installMissingDependencies().catch((error) => console.error(`Arty: checking for missing packages failed: ${error}`));
    buildTray();
    globalShortcut.register('CommandOrControl+Shift+Space', showAndListen);
    globalShortcut.register('CommandOrControl+Shift+A', toggleWindow);
    globalShortcut.register('CommandOrControl+Alt+Space', showAndLook);
    if (!globalShortcut.register('CommandOrControl+Alt+E', showAndExplain)) {
      console.error('Ctrl+Alt+E is taken by another app; "Explain what I copied" is still in the tray menu.');
    }
  });
}

app.on('before-quit', () => {
  quitting = true;
  saveBounds();
  globalShortcut.unregisterAll();
  if (bridge) bridge.kill('SIGTERM');
});
app.on('window-all-closed', () => { /* lives in the tray */ });
