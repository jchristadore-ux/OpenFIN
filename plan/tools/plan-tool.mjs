#!/usr/bin/env node
// Maintains plan/index.html without ever writing plaintext into the repo.
//
//   node plan-tool.mjs export <passphrase> [out.json]   decrypt payload to a local file
//   node plan-tool.mjs import <passphrase> <in.json>    re-encrypt a payload back in
//   node plan-tool.mjs rekey  <old> <new>               change the passphrase in place
//
// Exported JSON is plaintext household finances. tools/.gitignore keeps it out
// of git; delete it when you're done rather than leaving it on disk.

import fs from 'node:fs';
import path from 'node:path';
import {webcrypto as crypto} from 'node:crypto';
import {fileURLToPath} from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PAGE = path.join(HERE, '..', 'index.html');
const enc = new TextEncoder(), dec = new TextDecoder();

const b64e = u => Buffer.from(u).toString('base64');
const b64d = s => new Uint8Array(Buffer.from(s, 'base64'));

function readPage() {
  const html = fs.readFileSync(PAGE, 'utf8');
  const get = re => { const m = html.match(re); if (!m) throw new Error(`could not find ${re}`); return m[1]; };
  return {
    html,
    payload:  get(/var PAYLOAD = "([^"]+)"/),
    salt:     get(/var SALT    = "([^"]+)"/),
    roomSalt: get(/var ROOMSALT= "([^"]+)"/),
    iter:     Number(get(/var ITER    = (\d+)/)),
  };
}

async function key(pw, saltB64, iter, usage) {
  const base = await crypto.subtle.importKey('raw', enc.encode(pw), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey(
    {name:'PBKDF2', salt:b64d(saltB64), iterations:iter, hash:'SHA-256'},
    base, {name:'AES-GCM', length:256}, false, usage);
}
async function open(k, blobB64) {
  const raw = b64d(blobB64);
  const pt = await crypto.subtle.decrypt({name:'AES-GCM', iv:raw.slice(0,12)}, k, raw.slice(12));
  return dec.decode(pt);
}
async function seal(k, text) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt({name:'AES-GCM', iv}, k, enc.encode(text)));
  const out = new Uint8Array(12 + ct.length);
  out.set(iv,0); out.set(ct,12);
  return b64e(out);
}

const [cmd, a, b] = process.argv.slice(2);
const page = readPage();

try {
  if (cmd === 'export') {
    if (!a) throw new Error('usage: export <passphrase> [out.json]');
    const k = await key(a, page.salt, page.iter, ['decrypt']);
    const json = await open(k, page.payload);
    const out = b || path.join(HERE, 'payload.json');
    fs.writeFileSync(out, JSON.stringify(JSON.parse(json), null, 1));
    console.log(`exported → ${out}`);
    console.log('  This file is plaintext. Delete it when you are finished.');

  } else if (cmd === 'import') {
    if (!a || !b) throw new Error('usage: import <passphrase> <in.json>');
    const k = await key(a, page.salt, page.iter, ['decrypt']);
    await open(k, page.payload);                       // verify passphrase first
    const kw = await key(a, page.salt, page.iter, ['encrypt']);
    const body = JSON.stringify(JSON.parse(fs.readFileSync(b, 'utf8')));
    const blob = await seal(kw, body);
    fs.writeFileSync(PAGE, page.html.replace(/var PAYLOAD = "[^"]+"/, () => `var PAYLOAD = "${blob}"`));
    console.log(`imported ${b} → ${PAGE}`);

  } else if (cmd === 'rekey') {
    if (!a || !b) throw new Error('usage: rekey <old-passphrase> <new-passphrase>');
    if (b.length < 12) throw new Error('new passphrase must be at least 12 characters');
    const kOld = await key(a, page.salt, page.iter, ['decrypt']);
    const json = await open(kOld, page.payload);       // fails loudly on a wrong old passphrase

    const salt = crypto.getRandomValues(new Uint8Array(16));
    const roomSalt = crypto.getRandomValues(new Uint8Array(16));
    const kNew = await crypto.subtle.deriveKey(
      {name:'PBKDF2', salt, iterations:page.iter, hash:'SHA-256'},
      await crypto.subtle.importKey('raw', enc.encode(b), 'PBKDF2', false, ['deriveKey']),
      {name:'AES-GCM', length:256}, false, ['encrypt']);
    const blob = await seal(kNew, json);

    fs.writeFileSync(PAGE, page.html
      .replace(/var PAYLOAD = "[^"]+"/,  () => `var PAYLOAD = "${blob}"`)
      .replace(/var SALT    = "[^"]+"/,  () => `var SALT    = "${b64e(salt)}"`)
      .replace(/var ROOMSALT= "[^"]+"/,  () => `var ROOMSALT= "${b64e(roomSalt)}"`));
    console.log('passphrase changed.');
    console.log('  The sync room changed too, so existing progress will not carry over.');
    console.log('  Commit and push, then have everyone unlock again with the new passphrase.');

  } else {
    console.log(fs.readFileSync(fileURLToPath(import.meta.url), 'utf8')
      .split('\n').slice(1, 10).map(l => l.replace(/^\/\/ ?/, '')).join('\n'));
    process.exit(cmd ? 1 : 0);
  }
} catch (e) {
  console.error('error:', e.message.includes('operation-specific') || e.name === 'OperationError'
    ? 'wrong passphrase' : e.message);
  process.exit(1);
}
