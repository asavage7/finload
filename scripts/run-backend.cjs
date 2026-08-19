#!/usr/bin/env node
// Cross-platform dev backend launcher — uses the venv Python directly so the
// user doesn't need to manually activate the venv before running npm scripts.
const { spawn } = require('child_process');
const path = require('path');
const os = require('os');
const fs = require('fs');

const root = path.resolve(__dirname, '..');
const venvPython = os.platform() === 'win32'
    ? path.join(root, 'src-backend', '.venv', 'Scripts', 'python.exe')
    : path.join(root, 'src-backend', '.venv', 'bin', 'python');

if (!fs.existsSync(venvPython)) {
    console.error(`Python venv not found at: ${venvPython}`);
    console.error('Run the setup script first:');
    console.error('  Linux/macOS: bash scripts/setup-dev.sh');
    console.error('  Windows:     .\\scripts\\setup-dev.ps1');
    process.exit(1);
}

const proc = spawn(venvPython, [
    '-m', 'uvicorn', 'main:app',
    '--reload',
    '--reload-dir', path.join(root, 'src-backend'),
    '--app-dir', path.join(root, 'src-backend'),
    '--host', '0.0.0.0',
    '--port', '8000',
], { stdio: 'inherit' });

proc.on('close', (code) => process.exit(code ?? 0));
