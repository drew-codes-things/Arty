const fs = require('fs');
const path = require('path');

// Electron picks X11 or Wayland before any script runs, and a copy of the app
// that restarts itself would, inside an AppImage, be run from a mount that
// disappears when the first copy exits. So the program people start is a small
// launcher that gives the real one the flag. ARTY_WAYLAND=1 leaves it out.
exports.default = async function addX11Launcher(context) {
  if (context.electronPlatformName !== 'linux') return;
  const name = context.packager.executableName || 'arty';
  const launcher = path.join(context.appOutDir, name);
  const real = `${launcher}.bin`;
  fs.renameSync(launcher, real);
  fs.writeFileSync(launcher, `#!/bin/sh
here=$(dirname "$(readlink -f "$0")")
if [ -n "$ARTY_WAYLAND" ]; then exec "$here/${name}.bin" "$@"; fi
exec "$here/${name}.bin" --ozone-platform=x11 "$@"
`, { mode: 0o755 });
};
