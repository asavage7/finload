#!/usr/bin/env node
// Sets the release version in the three places that own one.
//
// package.json is the single source of truth: src-tauri/tauri.conf.json reads
// its version straight from there ("version": "../package.json"), so bundles and
// the app itself follow automatically. The other two can't reference it:
// src-tauri/Cargo.toml because Cargo has no way to read a version out of a JSON
// file, and src-backend/config.py because APP_VERSION goes into the User-Agent
// and the Jellyfin client version, which the frozen sidecar has to know without
// the repo around it. Both are rewritten here so they can't drift.
//
//   node scripts/set-version.mjs 0.2.0
//
// The release workflow runs this from the pushed tag, so a tagged build can
// never produce a bundle stamped with a stale version.

import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const version = process.argv[2]?.replace(/^v/, '');

if (!version || !/^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$/.test(version)) {
  console.error(`Usage: node scripts/set-version.mjs <semver>   (got: ${process.argv[2] ?? 'nothing'})`);
  process.exit(1);
}

const pkgPath = join(root, 'package.json');
const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'));
pkg.version = version;
writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n');

// Anchored to the [package] table's own `version` key so it can't match the
// version of a dependency further down the file.
const cargoPath = join(root, 'src-tauri', 'Cargo.toml');
const cargo = readFileSync(cargoPath, 'utf8');
const patched = cargo.replace(
  /(\[package\][^[]*?\nversion\s*=\s*")[^"]*(")/,
  `$1${version}$2`,
);
if (patched === cargo) {
  console.error('Could not find the [package] version key in src-tauri/Cargo.toml');
  process.exit(1);
}
writeFileSync(cargoPath, patched);

const configPath = join(root, 'src-backend', 'config.py');
const config = readFileSync(configPath, 'utf8');
const configPatched = config.replace(
  /(^APP_VERSION\s*=\s*")[^"]*(")/m,
  `$1${version}$2`,
);
if (configPatched === config) {
  console.error('Could not find APP_VERSION in src-backend/config.py');
  process.exit(1);
}
writeFileSync(configPath, configPatched);

console.log(
  `Version set to ${version} in package.json, src-tauri/Cargo.toml and src-backend/config.py`,
);
