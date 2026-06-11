/**
 * Unit tests for app.js scheduling / badge logic.
 *
 * Run with:  node scripts/test_app.mjs
 *
 * app.js exposes pure functions via `module.exports` when required from Node.
 * We use a lightweight createRequire shim so we can load a CommonJS-style
 * export from an ES module context.
 */

import { createRequire } from 'module';
import { fileURLToPath } from 'url';
import path from 'path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

// app.js uses `window` only in parseNowOverride (which reads window.location).
// Stub it so the module loads cleanly in Node.
globalThis.window = { location: { search: '' } };
// app.js only calls boot() when `document` exists, so no DOM/fetch stubs needed.

const appPath = path.join(__dirname, '..', 'app.js');
const {
  getLATime,
  toMinutes,
  fmtMinutes,
  fmtSession,
  getBadge,
  sortPools,
  isDataStale,
} = require(appPath);

// ─── Tiny test harness ────────────────────────────────────────────────────────

let passed = 0;
let failed = 0;

function assert(condition, label) {
  if (condition) {
    console.log(`  ✓ ${label}`);
    passed++;
  } else {
    console.error(`  ✗ FAIL: ${label}`);
    failed++;
  }
}

function assertEqual(a, b, label) {
  if (a === b) {
    console.log(`  ✓ ${label}`);
    passed++;
  } else {
    console.error(`  ✗ FAIL: ${label} — expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
    failed++;
  }
}

// ─── Helper: build a Date from LA wall-clock time ─────────────────────────────
// (mirrors the logic in parseNowOverride)
function laWallToDate(yr, mo, dy, hr, mn) {
  const isoStr = `${yr}-${String(mo).padStart(2,'0')}-${String(dy).padStart(2,'0')}T${String(hr).padStart(2,'0')}:${String(mn).padStart(2,'0')}:00`;
  // Build approx UTC then correct for LA offset
  const approxUtc = new Date(isoStr + 'Z');
  const laStr = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Los_Angeles',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false,
  }).format(approxUtc);
  const laParts = laStr.match(/(\d+)\/(\d+)\/(\d+),\s*(\d+):(\d+):(\d+)/);
  const [, laMo, laDy, laYr, laHr, laMn, laSc] = laParts.map(Number);
  const utcOffsetMs = approxUtc.getTime() - Date.UTC(laYr, laMo - 1, laDy, laHr, laMn, laSc);
  const targetUtc = Date.UTC(yr, mo - 1, dy, hr, mn, 0) + utcOffsetMs;
  return new Date(targetUtc);
}

// ─── Sample data ──────────────────────────────────────────────────────────────

const POOL_FARRELLY = { id: 'sanleandro-farrelly', name: 'Farrelly Pool', city: 'San Leandro' };
const POOL_DEFREMERY = { id: 'oakland-defremery', name: 'deFremery Pool', city: 'Oakland' };
const POOL_LIONS = { id: 'oakland-lions', name: 'Lions Pool', city: 'Oakland' };
const POOL_UNKNOWN = { id: 'alameda-emma-hood', name: 'Emma Hood Swim Center', city: 'Alameda' };

const SCHED_FARRELLY = {
  status: 'ok',
  sessions: [
    { day: 'saturday', start: '13:30', end: '16:30', label: 'Recreational Swim', category: 'rec' },
    { day: 'sunday',   start: '13:30', end: '16:30', label: 'Recreational Swim', category: 'rec' },
    { day: 'monday',   start: '13:30', end: '15:00', label: 'Recreational Swim', category: 'rec' },
  ],
};

const SCHED_DEFREMERY = {
  status: 'ok',
  sessions: [
    { day: 'saturday', start: '12:00', end: '16:00', label: 'Recreational Swim', category: 'rec' },
    { day: 'sunday',   start: '12:00', end: '16:00', label: 'Recreational Swim', category: 'rec' },
  ],
};

const SCHED_LIONS = {
  status: 'ok',
  sessions: [
    { day: 'saturday', start: '11:00', end: '14:30', label: 'Recreational Swim', category: 'rec' },
    { day: 'sunday',   start: '11:00', end: '14:30', label: 'Recreational Swim', category: 'rec' },
    { day: 'monday',   start: '11:00', end: '14:30', label: 'Recreational Swim', category: 'rec' },
    { day: 'monday',   start: '18:00', end: '20:00', label: 'Recreational Swim', category: 'rec' },
  ],
};

const SCHED_UNKNOWN = { status: 'unknown', sessions: [] };
const SCHED_STALE   = { status: 'stale', sessions: [
  { day: 'saturday', start: '13:00', end: '15:00', label: 'Rec Swim', category: 'rec' },
]};

// ─── Tests ────────────────────────────────────────────────────────────────────

console.log('\n── toMinutes ──');
assertEqual(toMinutes('00:00'),   0,   '00:00 → 0');
assertEqual(toMinutes('13:30'), 810,   '13:30 → 810');
assertEqual(toMinutes('16:30'), 990,   '16:30 → 990');
assertEqual(toMinutes('23:59'), 1439,  '23:59 → 1439');

console.log('\n── fmtMinutes ──');
assertEqual(fmtMinutes(810), '1:30 PM', '810 → 1:30 PM');
assertEqual(fmtMinutes(0),   '12:00 AM', '0 → 12:00 AM');
assertEqual(fmtMinutes(720), '12:00 PM', '720 → 12:00 PM');
assertEqual(fmtMinutes(60),  '1:00 AM',  '60 → 1:00 AM');

console.log('\n── fmtSession ──');
{
  const s = { start: '13:30', end: '16:30' };
  assertEqual(fmtSession(s), '1:30–4:30 PM', '13:30–16:30 → 1:30–4:30 PM');
}
{
  const s = { start: '10:00', end: '12:00' };
  const result = fmtSession(s);
  assert(result.includes('AM') || result.includes('PM'), '10:00–12:00 has AM/PM');
}

console.log('\n── getLATime ──');
{
  // Saturday 2026-06-13 14:00 LA = Saturday 21:00 UTC (PDT = UTC-7)
  const date = laWallToDate(2026, 6, 13, 14, 0);
  const t = getLATime(date);
  assertEqual(t.day, 'saturday', 'Jun 13 2026 → saturday');
  assertEqual(t.minutes, 14 * 60, 'Jun 13 2026 14:00 LA → 840 minutes');
}
{
  // Saturday 2026-06-13 06:00 LA
  const date = laWallToDate(2026, 6, 13, 6, 0);
  const t = getLATime(date);
  assertEqual(t.day, 'saturday', 'Jun 13 2026 06:00 → saturday');
  assertEqual(t.minutes, 6 * 60, 'Jun 13 2026 06:00 LA → 360 minutes');
}

// ─── Case 1: Saturday 13:00 LA — several pools should be open-now ─────────────
console.log('\n── getBadge: Case 1 — Saturday 13:00 LA (should show several open-now) ──');
{
  const laTime = { day: 'saturday', minutes: 13 * 60 }; // 13:00 = 780 min

  // Farrelly: rec 13:30–16:30 → starts at 810, not yet open at 780
  const b1 = getBadge(POOL_FARRELLY, SCHED_FARRELLY, laTime);
  assertEqual(b1.type, 'today', 'Farrelly at 13:00 → today (starts 13:30)');
  assertEqual(b1.nextStart, 810, 'Farrelly nextStart = 810');

  // deFremery: rec 12:00–16:00 → 720–960, open at 780
  const b2 = getBadge(POOL_DEFREMERY, SCHED_DEFREMERY, laTime);
  assertEqual(b2.type, 'open-now', 'deFremery at 13:00 → open-now (12:00–16:00)');

  // Lions Pool: rec 11:00–14:30 → 660–870, open at 780
  const b3 = getBadge(POOL_LIONS, SCHED_LIONS, laTime);
  assertEqual(b3.type, 'open-now', 'Lions Pool at 13:00 → open-now (11:00–14:30)');

  console.log('  → At 13:00 Saturday: deFremery and Lions are open-now; Farrelly opens at 13:30 (today)');
}

// ─── Case 2: Saturday 06:00 LA — no pools should be open-now ─────────────────
console.log('\n── getBadge: Case 2 — Saturday 06:00 LA (nothing should be open) ──');
{
  const laTime = { day: 'saturday', minutes: 6 * 60 }; // 06:00 = 360 min

  // Farrelly: rec 13:30–16:30 → starts 810, not open, scheduled later
  const b1 = getBadge(POOL_FARRELLY, SCHED_FARRELLY, laTime);
  assertEqual(b1.type, 'today', 'Farrelly at 06:00 → today (later at 13:30)');

  // deFremery: rec 12:00–16:00 → starts 720
  const b2 = getBadge(POOL_DEFREMERY, SCHED_DEFREMERY, laTime);
  assertEqual(b2.type, 'today', 'deFremery at 06:00 → today (later at 12:00)');

  // Lions Pool: rec 11:00–14:30 → starts 660
  const b3 = getBadge(POOL_LIONS, SCHED_LIONS, laTime);
  assertEqual(b3.type, 'today', 'Lions Pool at 06:00 → today (later at 11:00)');

  console.log('  → At 06:00 Saturday: no pools open-now, all show "today at ..."');
}

// ─── getBadge: after closing time ────────────────────────────────────────────
console.log('\n── getBadge: evening after all sessions close ──');
{
  const laTime = { day: 'saturday', minutes: 21 * 60 }; // 21:00

  const b1 = getBadge(POOL_FARRELLY, SCHED_FARRELLY, laTime);
  assertEqual(b1.type, 'no-rec', 'Farrelly at 21:00 Saturday → no-rec');

  const b2 = getBadge(POOL_DEFREMERY, SCHED_DEFREMERY, laTime);
  assertEqual(b2.type, 'no-rec', 'deFremery at 21:00 Saturday → no-rec');
}

// ─── getBadge: unknown / stale ────────────────────────────────────────────────
console.log('\n── getBadge: unknown & stale statuses ──');
{
  const laTime = { day: 'saturday', minutes: 14 * 60 };

  const b1 = getBadge(POOL_UNKNOWN, SCHED_UNKNOWN, laTime);
  assertEqual(b1.type, 'unknown', 'unknown status → unknown badge');

  const b2 = getBadge(POOL_UNKNOWN, null, laTime);
  assertEqual(b2.type, 'unknown', 'null sched → unknown badge');

  const b3 = getBadge(POOL_FARRELLY, SCHED_STALE, laTime);
  // stale with a session at 13:00–15:00 on saturday → open-now at 14:00
  assertEqual(b3.type, 'open-now', 'stale but session matches → open-now');
  assert(b3.stale === true, 'stale open-now has stale flag');

  const b4 = getBadge(POOL_FARRELLY, { ...SCHED_STALE, sessions: [] }, laTime);
  assertEqual(b4.type, 'stale', 'stale with no sessions → stale badge');
}

// ─── getBadge: closed status ──────────────────────────────────────────────────
console.log('\n── getBadge: closed status ──');
{
  const laTime = { day: 'saturday', minutes: 14 * 60 };
  const b = getBadge(POOL_FARRELLY, { status: 'closed', sessions: [] }, laTime);
  assertEqual(b.type, 'closed', 'closed status → closed badge');
}

// ─── sortPools ────────────────────────────────────────────────────────────────
console.log('\n── sortPools ──');
{
  const laTime = { day: 'saturday', minutes: 13 * 60 };

  const items = [
    { pool: POOL_FARRELLY,  sched: SCHED_FARRELLY,  badge: getBadge(POOL_FARRELLY,  SCHED_FARRELLY,  laTime) }, // today
    { pool: POOL_DEFREMERY, sched: SCHED_DEFREMERY, badge: getBadge(POOL_DEFREMERY, SCHED_DEFREMERY, laTime) }, // open-now
    { pool: POOL_LIONS,     sched: SCHED_LIONS,     badge: getBadge(POOL_LIONS,     SCHED_LIONS,     laTime) }, // open-now
  ];

  const sorted = sortPools(items);
  assertEqual(sorted[0].badge.type, 'open-now', 'first item is open-now');
  assertEqual(sorted[1].badge.type, 'open-now', 'second item is open-now');
  assertEqual(sorted[2].badge.type, 'today',    'third item is today');

  // Among open-now: Lions closes at 14:30 (870 min), deFremery at 16:00 (960 min)
  // deFremery closes later → should appear first
  assertEqual(sorted[0].pool.id, 'oakland-defremery', 'open-now sorted by latest close: deFremery first');
  assertEqual(sorted[1].pool.id, 'oakland-lions',     'open-now sorted by latest close: Lions second');
}

// ─── isDataStale ──────────────────────────────────────────────────────────────
console.log('\n── isDataStale ──');
{
  // Helper: build a Date that is exactly N days before a reference point
  function daysAgo(referenceDate, n) {
    return new Date(referenceDate.getTime() - n * 24 * 60 * 60 * 1000);
  }

  const now = new Date('2026-06-11T12:00:00Z');

  // Exactly 14 days ago — NOT stale (boundary: > 14, not >= 14)
  const exactly14 = daysAgo(now, 14);
  assert(
    !isDataStale(exactly14.toISOString(), now),
    'generated_at exactly 14 days ago is NOT stale (boundary)',
  );

  // 15 days ago — stale
  const fifteenDaysAgo = daysAgo(now, 15);
  assert(
    isDataStale(fifteenDaysAgo.toISOString(), now),
    'generated_at 15 days ago IS stale',
  );

  // 1 day ago — fresh
  const yesterday = daysAgo(now, 1);
  assert(
    !isDataStale(yesterday.toISOString(), now),
    'generated_at 1 day ago is NOT stale',
  );

  // Missing / empty string — not stale (safe default)
  assert(
    !isDataStale('', now),
    'empty generated_at is NOT stale (safe default)',
  );

  // Malformed string — not stale (safe default)
  assert(
    !isDataStale('not-a-date', now),
    'malformed generated_at is NOT stale (safe default)',
  );

  // Far future ?now= override: today is 2026-08-01, generated_at is 2026-06-11
  // 2026-08-01 minus 2026-06-11 = 51 days → stale
  const futureNow = new Date('2026-08-01T00:00:00Z');
  const oldGenAt  = '2026-06-11T00:00:00Z';
  assert(
    isDataStale(oldGenAt, futureNow),
    'isDataStale respects ?now= override: 51-day-old data is stale',
  );
}

// ─── Summary ──────────────────────────────────────────────────────────────────
console.log(`\n── Results: ${passed} passed, ${failed} failed ──\n`);
if (failed > 0) process.exit(1);
