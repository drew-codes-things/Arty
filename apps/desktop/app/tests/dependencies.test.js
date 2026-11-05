const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const dependencies = require('../dependencies');

function probe(env, commands = [], libraries = []) {
  return { env, has: (name) => commands.includes(name), hasLibrary: (prefix) => libraries.some((lib) => lib.startsWith(prefix)) };
}

const EVERYTHING = ['xdg-open', 'wl-paste', 'xprop', 'spectacle', 'xdotool', 'apt-get', 'pkexec'];

test('nothing is missing on a complete KDE Wayland desktop', () => {
  const kde = probe({ WAYLAND_DISPLAY: 'wayland-0', XDG_CURRENT_DESKTOP: 'KDE' }, EVERYTHING, ['libportaudio.so.2']);
  assert.deepStrictEqual(dependencies.missing(kde), []);
});

test('a bare KDE Wayland desktop needs PortAudio, links, clipboard, xprop and spectacle', () => {
  const bare = probe({ WAYLAND_DISPLAY: 'wayland-0', XDG_CURRENT_DESKTOP: 'KDE' });
  assert.deepStrictEqual(dependencies.missing(bare).map((item) => item.package),
    ['portaudio', 'xdg-utils', 'wl-clipboard', 'xprop', 'spectacle']);
});

test('a bare X11 desktop needs xclip, scrot and xdotool, not xprop', () => {
  const bare = probe({ DISPLAY: ':0', XDG_CURRENT_DESKTOP: 'XFCE' }, ['xdg-open'], ['libportaudio.so.2']);
  assert.deepStrictEqual(dependencies.missing(bare).map((item) => item.package), ['xclip', 'scrot', 'xdotool']);
});

test('without a graphical session only the session-free things are checked', () => {
  assert.deepStrictEqual(dependencies.missing(probe({})).map((item) => item.id), ['portaudio', 'xdg-open']);
});

test('install commands use each package manager with its own package names', () => {
  const items = [{ package: 'portaudio' }, { package: 'xprop' }, { package: 'xdotool' }];
  assert.deepStrictEqual(dependencies.installCommand('apt-get', items),
    ['pkexec', 'apt-get', 'install', '-y', 'libportaudio2', 'x11-utils', 'xdotool']);
  assert.deepStrictEqual(dependencies.installCommand('pacman', items),
    ['pkexec', 'pacman', '-S', '--needed', '--noconfirm', 'portaudio', 'xorg-xprop', 'xdotool']);
  assert.strictEqual(dependencies.manualCommand('dnf', items), 'sudo dnf install -y portaudio xprop xdotool');
  assert.throws(() => dependencies.installCommand('brew', items), /unsupported package manager/);
});

test('the package manager is the first one found', () => {
  assert.strictEqual(dependencies.packageManager(probe({}, ['zypper', 'dnf'])), 'dnf');
  assert.strictEqual(dependencies.packageManager(probe({}, [])), null);
});

test('python packages are reinstalled only when the requirements change', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'arty-deps-'));
  const requirements = path.join(dir, 'requirements.txt');
  const stamp = path.join(dir, 'stamp');
  fs.writeFileSync(requirements, 'sounddevice>=0.4.6\n');
  assert.ok(dependencies.requirementsChanged(requirements, stamp));
  fs.writeFileSync(stamp, dependencies.requirementsStamp(requirements));
  assert.ok(!dependencies.requirementsChanged(requirements, stamp));
  fs.appendFileSync(requirements, 'numpy\n');
  assert.ok(dependencies.requirementsChanged(requirements, stamp));
  fs.rmSync(dir, { recursive: true });
});

test('the real probe finds this machine\'s commands and libraries', () => {
  const real = dependencies.systemProbe();
  assert.ok(real.has('sh'));
  assert.ok(!real.has('definitely-not-a-command-arty'));
  assert.ok(real.hasLibrary('libc.so'));
});
