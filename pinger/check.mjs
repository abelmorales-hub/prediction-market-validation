// Pre-deploy self-check: `node check.mjs`. No framework, no dependencies.
//
// The one failure mode worth catching before a deploy rather than at 03:20Z: a cron in
// wrangler.toml with no matching entry in DISPATCH, or the reverse. The Worker does throw
// on an unmapped cron, which is better than silence, but it throws at the moment the band
// was supposed to start, and by then the day is already lost.
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const worker = readFileSync(new URL('./src/index.js', import.meta.url), 'utf8');
const toml = readFileSync(new URL('./wrangler.toml', import.meta.url), 'utf8');

// Both sides are plain string literals; a 2-line regex beats a TOML parser as a dependency.
const mapped = [...worker.matchAll(/^ {2}'([-\d*/, ]+)':\s*\{/gm)].map((m) => m[1]);
const crons = [...toml.matchAll(/^\s*"([-\d*/, ]+)",/gm)].map((m) => m[1]);

assert.ok(mapped.length > 0, 'parsed no DISPATCH keys out of src/index.js');
assert.ok(crons.length > 0, 'parsed no crons out of wrangler.toml');
assert.deepEqual(
  [...crons].sort(),
  [...mapped].sort(),
  `wrangler.toml crons and DISPATCH keys disagree.\n  toml: ${crons}\n  code: ${mapped}`,
);

// A dispatch has to land before the deadline it is dispatching for. Pinned here because a
// transposed `until` -- shift A sent with 09:10 -- dispatches happily and quietly collects
// the wrong window, which is the same shape of fault as the band that ran after its own
// deadline and reported success.
for (const [cron, until] of [
  ['20 3 * * *', '06:50'],
  ['10 6 * * *', '09:10'],
]) {
  assert.ok(mapped.includes(cron), `band shift ${cron} is not in DISPATCH`);
  const re = new RegExp(`'${cron.replace(/\*/g, '\\*')}':[^\\n]*until: '${until}'`);
  assert.match(worker, re, `${cron} must dispatch until=${until}`);
  const [m, h] = cron.split(' ');
  const fires = Number(h) * 60 + Number(m);
  const ends = Number(until.slice(0, 2)) * 60 + Number(until.slice(3));
  assert.ok(ends > fires, `${cron} fires at ${h}:${m}Z, after its own deadline ${until}Z`);
}

// The fault that cost 2026-10-14 through -17: without this header Workers' fetch sends the
// body as text/plain, GitHub answers 400, and the Worker had never dispatched once. It is
// asserted here and not left to review because it is invisible in the code -- the bug is a
// line that is absent, and the symptom is identical to the dead token that masked it.
assert.match(
  worker,
  /'Content-Type':\s*'application\/json'/,
  "the dispatch must send Content-Type: application/json, or GitHub rejects the body with 400",
);

// The fault that cost 2026-10-14 through -19 in full: the GH_TOKEN secret held a single
// stray character, so every outbound request was malformed and the upstream returned a
// bodyless 400 that never came from GitHub's API at all. Asserted here for the same reason
// as the header above -- the bug was a check that did not exist, and its absence is
// invisible. A credential has to be rejected for its shape before it is used, or a bad
// paste is indistinguishable from a bad scope.
assert.match(
  worker,
  /\/\^\[A-Za-z0-9_\]\+\$\/\.test\(token\)/,
  'the dispatch must reject a GH_TOKEN that is not [A-Za-z0-9_] before sending it',
);

console.log(`ok: ${crons.length} crons, all mapped, both band shifts fire before their deadlines`);
