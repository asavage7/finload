#!/usr/bin/env node
// Sets the release version in the three places that own one.
//
// package.json is the single source of truth: src-tauri/tauri.conf.json reads
// its version straight from there ("version": "../package.json"), so bundles and
// the app itself follow automatically. The other two can't reference it:
// src-tauri/Cargo.toml because Cargo has no way to read a version out of a JSON
// file, and src-backend/core/config.py because APP_VERSION goes into the User-Agent
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

// Each of these is checked with .test() before substituting, rather than by
// comparing the result against the original: re-stamping the version a file
// already carries is a no-op, and treating "content did not change" as "pattern
// not found" would fail every release built from a tag matching the committed
// version.
function replaceIn(path, pattern, label) {
  const text = readFileSync(path, 'utf8');
  if (!pattern.test(text)) {
    console.error(`Could not find ${label} in ${path}`);
    process.exit(1);
  }
  writeFileSync(path, text.replace(pattern, `$1${version}$2`));
}

// Anchored to the [package] table's own `version` key so it can't match the
// version of a dependency further down the file.
replaceIn(
  join(root, 'src-tauri', 'Cargo.toml'),
  /(\[package\][^[]*?\nversion\s*=\s*")[^"]*(")/,
  'the [package] version key',
);

replaceIn(
  join(root, 'src-backend', 'core', 'config.py'),
  /(^APP_VERSION\s*=\s*")[^"]*(")/m,
  'APP_VERSION',
);

console.log(
  `Version set to ${version} in package.json, src-tauri/Cargo.toml and src-backend/core/config.py`,
);
