#!/usr/bin/env node
// Downloads and vendors a pinned mpv build for Windows.
//
// To bump the pinned version: pick a release from
// https://github.com/shinchiro/mpv-winbuild-cmake/releases and update
// RELEASE_TAG/ASSET_NAME/SHA256 below to match. Use "mpv-dev-x86_64"

import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import sevenZip from '7zip-min';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

const RELEASE_TAG = '20260610';
const ASSET_NAME = 'mpv-dev-x86_64-20260610-git-304426c.7z';
const SHA256 = '8cbb25ea784f01afbb3f904217cab1317430a8bcfd5680fd827a866367f71cc9';
const DOWNLOAD_URL = `https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/${RELEASE_TAG}/${ASSET_NAME}`;

const vendorDir = join(root, 'src-tauri', 'binaries', 'vendor');
const cacheDir = join(vendorDir, '.cache');
const archivePath = join(cacheDir, ASSET_NAME);
const extractDir = join(cacheDir, 'extract');
const mpvDir = join(vendorDir, 'mpv');
const destDll = join(mpvDir, 'libmpv-2.dll');

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

async function download() {
  mkdirSync(cacheDir, { recursive: true });
  if (existsSync(archivePath) && sha256(archivePath) === SHA256) {
    console.log(`Using cached ${ASSET_NAME}`);
    return;
  }

  console.log(`Downloading ${ASSET_NAME} from ${DOWNLOAD_URL} ...`);
  const res = await fetch(DOWNLOAD_URL);
  if (!res.ok) {
    throw new Error(`Download failed: ${res.status} ${res.statusText}`);
  }
  writeFileSync(archivePath, Buffer.from(await res.arrayBuffer()));

  const actual = sha256(archivePath);
  if (actual !== SHA256) {
    rmSync(archivePath);
    throw new Error(
      `Checksum mismatch for ${ASSET_NAME}\n  expected ${SHA256}\n  got      ${actual}\n` +
        'The pinned release asset may have been re-uploaded, or the download was ' +
        'corrupted/tampered with -- not proceeding.',
    );
  }
}

function extract() {
  return new Promise((resolve, reject) => {
    rmSync(extractDir, { recursive: true, force: true });
    sevenZip.unpack(archivePath, extractDir, (err) => (err ? reject(err) : resolve()));
  });
}

async function main() {
  if (existsSync(destDll)) {
    console.log(`Vendored mpv already present at ${destDll}`);
    return;
  }

  await download();
  await extract();

  const srcDll = join(extractDir, 'libmpv-2.dll');
  if (!existsSync(srcDll)) {
    throw new Error(`Expected libmpv-2.dll inside ${ASSET_NAME}, but it wasn't there.`);
  }

  mkdirSync(mpvDir, { recursive: true });
  writeFileSync(destDll, readFileSync(srcDll));
  rmSync(extractDir, { recursive: true, force: true });

  console.log(`Vendored mpv ready at ${destDll}`);
}

main().catch((err) => {
  console.error(err.message ?? err);
  process.exit(1);
});