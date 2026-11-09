const path = require('path');

function pythonExecutable(root, packaged, platform = process.platform) {
  if (platform === 'win32') {
    return packaged ? path.join(root, 'python', 'python.exe') : path.join(root, '.venv', 'Scripts', 'python.exe');
  }
  return packaged ? path.join(root, 'python', 'bin', 'python3') : path.join(root, '.venv', 'bin', 'python');
}

function requirementsName(platform = process.platform) {
  return platform === 'win32' ? 'requirements-windows.txt' : 'requirements-linux.txt';
}

function setupCommand(platform = process.platform) {
  return platform === 'win32' ? 'setup-windows.ps1' : './setup-linux.sh';
}

function usesLoginItems(platform = process.platform) {
  return platform !== 'linux';
}

module.exports = { pythonExecutable, requirementsName, setupCommand, usesLoginItems };
