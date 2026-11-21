'use strict';

const hotkey = new URLSearchParams(location.search).get('hotkey') || '';
document.getElementById('hotkey').textContent = hotkey ? `or ${hotkey}` : '';
document.getElementById('stop').addEventListener('click', () => window.artyControl?.stop());
