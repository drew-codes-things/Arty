const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const LIBRARY_DIRS = [
  '/usr/lib/x86_64-linux-gnu', '/usr/lib/aarch64-linux-gnu', '/usr/lib64', '/usr/lib',
  '/lib/x86_64-linux-gnu', '/lib/aarch64-linux-gnu', '/usr/local/lib',
];
const MANAGERS = ['apt-get', 'dnf', 'pacman', 'zypper'];
const SCREENSHOT_TOOLS = ['spectacle', 'gnome-screenshot', 'grim', 'scrot'];
const CLIPBOARD_TOOLS = ['wl-paste', 'xclip', 'xsel'];

const PACKAGES = {
  portaudio: { 'apt-get': 'libportaudio2', dnf: 'portaudio', pacman: 'portaudio', zypper: 'libportaudio2' },
  'xdg-utils': 'xdg-utils',
  'wl-clipboard': 'wl-clipboard',
  xclip: 'xclip',
  xprop: { 'apt-get': 'x11-utils', dnf: 'xprop', pacman: 'xorg-xprop', zypper: 'xprop' },
  spectacle: { 'apt-get': 'kde-spectacle', dnf: 'spectacle', pacman: 'spectacle', zypper: 'spectacle' },
  'gnome-screenshot': 'gnome-screenshot',
  grim: 'grim',
  scrot: 'scrot',
  xdotool: 'xdotool',
};

function systemProbe(env = process.env) {
  const dirs = (env.PATH || '').split(':').filter(Boolean);
  return {
    env,
    has: (command) => dirs.some((dir) => {
      try {
        fs.accessSync(path.join(dir, command), fs.constants.X_OK);
        return true;
      } catch {
        return false;
      }
    }),
    hasLibrary: (prefix) => LIBRARY_DIRS.some((dir) => {
      try {
        return fs.readdirSync(dir).some((name) => name.startsWith(prefix));
      } catch {
        return false;
      }
    }),
  };
}

function sessionKind(env) {
  if (env.WAYLAND_DISPLAY || env.XDG_SESSION_TYPE === 'wayland') return 'wayland';
  if (env.DISPLAY || env.XDG_SESSION_TYPE === 'x11') return 'x11';
  return '';
}

function desktopName(env) {
  const desktop = (env.XDG_CURRENT_DESKTOP || '').toUpperCase();
  if (desktop.includes('KDE')) return 'kde';
  if (desktop.includes('GNOME')) return 'gnome';
  return '';
}

function screenshotPackage(kind, desktop) {
  if (desktop === 'kde') return 'spectacle';
  if (desktop === 'gnome') return 'gnome-screenshot';
  return kind === 'wayland' ? 'grim' : 'scrot';
}

function missing(probe = systemProbe()) {
  const kind = sessionKind(probe.env);
  const desktop = desktopName(probe.env);
  const found = [];
  if (!probe.hasLibrary('libportaudio.so')) {
    found.push({ id: 'portaudio', purpose: 'so Arty can hear you through the microphone', package: 'portaudio' });
  }
  if (!probe.has('xdg-open')) {
    found.push({ id: 'xdg-open', purpose: 'so Arty can open links and files for you', package: 'xdg-utils' });
  }
  if (kind && !CLIPBOARD_TOOLS.some(probe.has)) {
    found.push({ id: 'clipboard', purpose: 'so Arty can read what you copied',
      package: kind === 'wayland' ? 'wl-clipboard' : 'xclip' });
  }
  if (desktop === 'kde' && !probe.has('xprop')) {
    found.push({ id: 'xprop', purpose: 'so Arty stays above full-screen apps', package: 'xprop' });
  }
  if (kind && !SCREENSHOT_TOOLS.some(probe.has)) {
    found.push({ id: 'screenshot', purpose: 'so Arty can see your screen', package: screenshotPackage(kind, desktop) });
  }
  if (kind === 'x11' && !probe.has('xdotool')) {
    found.push({ id: 'xdotool', purpose: 'so Arty can click and type for you', package: 'xdotool' });
  }
  return found;
}

function packageManager(probe = systemProbe()) {
  return MANAGERS.find(probe.has) || null;
}

function packageNames(manager, items) {
  return [...new Set(items.map((item) => {
    const names = PACKAGES[item.package];
    return typeof names === 'string' ? names : names[manager];
  }))];
}

function installCommand(manager, items) {
  const names = packageNames(manager, items);
  const args = {
    'apt-get': ['apt-get', 'install', '-y', ...names],
    dnf: ['dnf', 'install', '-y', ...names],
    pacman: ['pacman', '-S', '--needed', '--noconfirm', ...names],
    zypper: ['zypper', '--non-interactive', 'install', ...names],
  }[manager];
  if (!args) throw new Error(`unsupported package manager: ${manager}`);
  return ['pkexec', ...args];
}

function manualCommand(manager, items) {
  return `sudo ${installCommand(manager, items).slice(1).join(' ')}`;
}

function requirementsStamp(requirementsFile) {
  return crypto.createHash('sha256').update(fs.readFileSync(requirementsFile)).digest('hex');
}

function requirementsChanged(requirementsFile, stampFile) {
  try {
    return fs.readFileSync(stampFile, 'utf8').trim() !== requirementsStamp(requirementsFile);
  } catch {
    return true;
  }
}

module.exports = {
  missing, packageManager, installCommand, manualCommand, systemProbe, requirementsStamp, requirementsChanged, PACKAGES,
};
