/**
 * East Bay Play — Pool Rec-Swim Finder
 * Plain ES2020, no dependencies, no build step.
 * Works under any subpath (relative URLs only).
 */

// ─── Time helpers (America/Los_Angeles, via Intl) ────────────────────────────

/**
 * Parse a ?now=YYYY-MM-DDTHH:MM override (interpreted as LA wall time).
 * Returns a Date object whose UTC value corresponds to that LA wall time,
 * or null if the param is absent / malformed.
 */
function parseNowOverride() {
  const params = new URLSearchParams(window.location.search);
  const raw = params.get('now');
  if (!raw) return null;
  // raw is like "2026-06-13T14:00" — treat it as LA wall time.
  // Strategy: use Intl to find the UTC offset for that instant in LA, then
  // construct the correct UTC epoch.
  const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/);
  if (!m) return null;
  const [, yr, mo, dy, hr, mn, sc = '00'] = m;
  // First approximation: parse as UTC, then ask Intl what LA time that is.
  const approxUtc = new Date(`${yr}-${mo}-${dy}T${hr}:${mn}:${sc}Z`);
  const laStr = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Los_Angeles',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false,
  }).format(approxUtc);
  // laStr looks like "06/13/2026, 07:00:00" — parse it
  const laParts = laStr.match(/(\d+)\/(\d+)\/(\d+),\s*(\d+):(\d+):(\d+)/);
  if (!laParts) return null;
  const [, laMo, laDy, laYr, laHr, laMn, laSc] = laParts.map(Number);
  const utcOffsetMs = approxUtc.getTime() - Date.UTC(laYr, laMo - 1, laDy, laHr, laMn, laSc);
  // Adjust: the wall time we want is (yr/mo/dy hr:mn:sc) in LA, so:
  const targetUtc = Date.UTC(+yr, +mo - 1, +dy, +hr, +mn, +sc) + utcOffsetMs;
  return new Date(targetUtc);
}

/**
 * Given a Date, return an object describing the current LA wall clock:
 *   { day: 'saturday', minutes: 810 }  (minutes since midnight, LA time)
 */
function getLATime(date) {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Los_Angeles',
    weekday: 'long',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
  const parts = fmt.formatToParts(date);
  const get = (type) => parts.find(p => p.type === type)?.value ?? '';
  const day = get('weekday').toLowerCase();
  let hour = parseInt(get('hour'), 10);
  const minute = parseInt(get('minute'), 10);
  // Intl sometimes returns 24 for midnight
  if (hour === 24) hour = 0;
  const minutes = hour * 60 + minute;
  return { day, minutes };
}

/**
 * Parse an "HH:MM" string into minutes since midnight.
 */
function toMinutes(hhmm) {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
}

/**
 * Format minutes-since-midnight to "H:MM AM/PM".
 */
function fmtMinutes(mins) {
  const h24 = Math.floor(mins / 60) % 24;
  const m = mins % 60;
  const ampm = h24 < 12 ? 'AM' : 'PM';
  const h12 = h24 % 12 || 12;
  return `${h12}:${String(m).padStart(2, '0')} ${ampm}`;
}

/**
 * Format a session's start–end as "H:MM–H:MM AM/PM".
 */
function fmtSession(session) {
  const startMins = toMinutes(session.start);
  const endMins = toMinutes(session.end);
  const startH24 = Math.floor(startMins / 60);
  const endH24 = Math.floor(endMins / 60);
  const startAmpm = startH24 < 12 ? 'AM' : 'PM';
  const endAmpm = endH24 < 12 ? 'AM' : 'PM';
  const startH12 = startH24 % 12 || 12;
  const endH12 = endH24 % 12 || 12;
  const startM = String(startMins % 60).padStart(2, '0');
  const endM = String(endMins % 60).padStart(2, '0');
  // Omit AM/PM on start if same as end
  const startStr = startAmpm === endAmpm
    ? `${startH12}:${startM}`
    : `${startH12}:${startM} ${startAmpm}`;
  return `${startStr}–${endH12}:${endM} ${endAmpm}`;
}

// ─── Status logic ─────────────────────────────────────────────────────────────

/**
 * Determine badge info for a pool given current LA time.
 *
 * Returns one of:
 *   { type: 'open-now',   label, nextEnd }
 *   { type: 'today',      label, nextStart }
 *   { type: 'no-rec',     label }
 *   { type: 'stale',      label }
 *   { type: 'closed',     label }
 *   { type: 'unknown',    label }
 *
 * @param {object} pool        - from pools.json
 * @param {object|null} sched  - from schedules.json, or null if missing
 * @param {{ day: string, minutes: number }} laTime
 */
function getBadge(pool, sched, laTime) {
  if (!sched) return { type: 'unknown', label: 'No schedule data' };

  if (sched.status === 'closed') return { type: 'closed', label: 'Closed' };

  // unknown with no sessions → goes in the collapsed "no data" section
  if (sched.status === 'unknown' && (sched.sessions || []).length === 0) {
    return { type: 'unknown', label: 'No schedule data' };
  }

  // stale means scrape failed but we have old session data; unknown with sessions
  // is treated like stale (show data with warning).
  const isStale = sched.status === 'stale' || sched.status === 'unknown';

  // Gather today's rec sessions
  const todayRec = (sched.sessions || []).filter(
    s => s.day === laTime.day && s.category === 'rec'
  );

  // Check open-now
  const openNow = todayRec.find(
    s => toMinutes(s.start) <= laTime.minutes && laTime.minutes < toMinutes(s.end)
  );
  if (openNow) {
    const badge = { type: 'open-now', label: '🟢 Open now', nextEnd: toMinutes(openNow.end) };
    if (isStale) badge.stale = true;
    return badge;
  }

  // Check later today
  const upcoming = todayRec
    .filter(s => toMinutes(s.start) > laTime.minutes)
    .sort((a, b) => toMinutes(a.start) - toMinutes(b.start));
  if (upcoming.length > 0) {
    const next = upcoming[0];
    const badge = {
      type: 'today',
      label: `🕐 Today at ${fmtMinutes(toMinutes(next.start))}`,
      nextStart: toMinutes(next.start),
    };
    if (isStale) badge.stale = true;
    return badge;
  }

  // No rec today
  if (isStale) {
    return { type: 'stale', label: '⚠️ Schedule may be outdated' };
  }
  return { type: 'no-rec', label: '⚪ No rec swim today' };
}

/**
 * Sort pools:
 *   1. open-now (sorted by nextEnd desc — closes latest = most time left)
 *   2. today (soonest first)
 *   3. no-rec, stale, closed — alphabetical by name
 *   4. unknown (no data) — goes to collapsed section
 */
function sortPools(poolBadges) {
  const order = { 'open-now': 0, 'today': 1, 'no-rec': 2, 'stale': 2, 'closed': 2, 'unknown': 3 };
  return [...poolBadges].sort((a, b) => {
    const oa = order[a.badge.type] ?? 3;
    const ob = order[b.badge.type] ?? 3;
    if (oa !== ob) return oa - ob;
    if (oa === 0) {
      // Both open-now: sort by who closes latest (most time remaining)
      return (b.badge.nextEnd ?? 0) - (a.badge.nextEnd ?? 0);
    }
    if (oa === 1) {
      // Both today: soonest first
      return (a.badge.nextStart ?? 0) - (b.badge.nextStart ?? 0);
    }
    // Alphabetical by name
    return a.pool.name.localeCompare(b.pool.name);
  });
}

// ─── Rendering helpers ────────────────────────────────────────────────────────

const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
const DAY_LABELS = {
  monday: 'Monday', tuesday: 'Tuesday', wednesday: 'Wednesday',
  thursday: 'Thursday', friday: 'Friday', saturday: 'Saturday', sunday: 'Sunday',
};

function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderWeeklySchedule(sched) {
  if (!sched || !sched.sessions || sched.sessions.length === 0) {
    return '<p class="no-sessions">No session data available.</p>';
  }

  let html = '<div class="weekly-grid">';
  for (const day of DAYS) {
    const sessions = sched.sessions.filter(s => s.day === day);
    if (sessions.length === 0) continue;
    html += `<div class="day-block">
      <div class="day-name">${DAY_LABELS[day]}</div>
      <ul class="session-list">`;
    for (const s of sessions) {
      const timeStr = fmtSession(s);
      if (s.category === 'rec') {
        html += `<li class="session rec-session">
          <span class="session-time">${escHtml(timeStr)}</span>
          <span class="session-label">${escHtml(s.label)}</span>
        </li>`;
      } else {
        html += `<li class="session other-session">
          <span class="session-time">${escHtml(timeStr)}</span>
          <span class="session-label">${escHtml(s.label)}</span>
        </li>`;
      }
    }
    html += '</ul></div>';
  }
  html += '</div>';
  return html;
}

function renderPoolCard(pool, sched, badge, laTime) {
  const badgeClass = `badge badge-${badge.type}`;
  const staleNote = badge.stale
    ? '<span class="stale-note">⚠️ Schedule may be outdated</span>'
    : '';

  // Today's rec sessions for the card body
  const todayRec = sched
    ? (sched.sessions || []).filter(s => s.day === laTime.day && s.category === 'rec')
    : [];

  let todayHtml = '';
  if (todayRec.length > 0) {
    todayHtml = '<ul class="today-sessions">' +
      todayRec.map(s =>
        `<li>${escHtml(fmtSession(s))} <span class="session-label-inline">${escHtml(s.label)}</span></li>`
      ).join('') +
      '</ul>';
  }

  // Source links
  const sources = pool.schedule_sources || [];
  const sourceLinks = sources.map((src, i) =>
    `<a href="${escHtml(src.url)}" target="_blank" rel="noopener">Schedule source${sources.length > 1 ? ` ${i + 1}` : ''}</a>`
  ).join(' · ');
  const infoLink = `<a href="${escHtml(pool.info_url)}" target="_blank" rel="noopener">Pool info</a>`;

  const seasonNote = sched?.season_note
    ? `<p class="season-note">${escHtml(sched.season_note)}</p>`
    : '';
  const lastVerified = sched?.last_verified
    ? `<p class="last-verified">Last verified: ${escHtml(sched.last_verified)}</p>`
    : '';

  const weekly = sched ? renderWeeklySchedule(sched) : '<p class="no-sessions">No schedule data.</p>';

  const indoorBadge = pool.indoor
    ? '<span class="indoor-tag">Indoor</span>'
    : '';

  return `<details class="pool-card" id="pool-${escHtml(pool.id)}">
  <summary class="pool-summary">
    <div class="summary-top">
      <span class="pool-name">${escHtml(pool.name)}</span>
      <span class="${badgeClass}">${badge.label}</span>
    </div>
    <div class="summary-sub">
      <span class="pool-city">${escHtml(pool.city)}</span>
      ${indoorBadge}
      ${staleNote}
    </div>
    ${todayHtml}
  </summary>
  <div class="pool-detail">
    ${seasonNote}
    <h3 class="weekly-heading">Weekly schedule</h3>
    ${weekly}
    <div class="pool-links">
      ${infoLink} · ${sourceLinks}
    </div>
    ${lastVerified}
  </div>
</details>`;
}

// ─── LA date formatting for header ───────────────────────────────────────────

function formatLADate(date) {
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Los_Angeles',
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  }).format(date);
}

function formatGeneratedAt(isoStr) {
  // e.g. "2026-06-11T00:00:00Z" → "Jun 11"
  try {
    const d = new Date(isoStr);
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/Los_Angeles',
      month: 'short',
      day: 'numeric',
    }).format(d);
  } catch {
    return isoStr;
  }
}

// ─── Main render ──────────────────────────────────────────────────────────────

function renderError(msg) {
  document.getElementById('main-content').innerHTML = `
    <div class="error-box">
      <p>😕 ${escHtml(msg)}</p>
      <button onclick="location.reload()">Retry</button>
    </div>`;
}

function render(poolsData, schedulesData, now) {
  const laTime = getLATime(now);
  const schedMap = schedulesData.schedules || {};

  // Build badge for each pool
  const poolBadges = poolsData.pools.map(pool => {
    const sched = schedMap[pool.id] ?? null;
    const badge = getBadge(pool, sched, laTime);
    return { pool, sched, badge };
  });

  // Separate unknown/no-data from the rest
  const mainList = poolBadges.filter(pb => pb.badge.type !== 'unknown');
  const noDataList = poolBadges.filter(pb => pb.badge.type === 'unknown');

  const sorted = sortPools(mainList);

  // Update header
  document.getElementById('current-date').textContent = formatLADate(now);
  const genAt = schedulesData.generated_at
    ? `Schedules updated ${formatGeneratedAt(schedulesData.generated_at)}`
    : '';
  document.getElementById('generated-at').textContent = genAt;

  // Render main cards
  let html = sorted.map(({ pool, sched, badge }) =>
    renderPoolCard(pool, sched, badge, laTime)
  ).join('\n');

  // Collapsed no-data section
  if (noDataList.length > 0) {
    const noDataCards = noDataList
      .sort((a, b) => a.pool.name.localeCompare(b.pool.name))
      .map(({ pool, sched, badge }) => renderPoolCard(pool, sched, badge, laTime))
      .join('\n');

    html += `
<details class="no-data-section">
  <summary class="no-data-summary">No schedule data yet (${noDataList.length} pool${noDataList.length !== 1 ? 's' : ''})</summary>
  <div class="no-data-cards">${noDataCards}</div>
</details>`;
  }

  document.getElementById('main-content').innerHTML = html;
}

// ─── Boot ─────────────────────────────────────────────────────────────────────

async function boot() {
  const now = parseNowOverride() ?? new Date();

  try {
    const [poolsResp, schedResp] = await Promise.all([
      fetch('data/pools.json'),
      fetch('data/schedules.json'),
    ]);

    if (!poolsResp.ok) throw new Error(`pools.json: HTTP ${poolsResp.status}`);
    if (!schedResp.ok) throw new Error(`schedules.json: HTTP ${schedResp.status}`);

    const [poolsData, schedulesData] = await Promise.all([
      poolsResp.json(),
      schedResp.json(),
    ]);

    render(poolsData, schedulesData, now);
  } catch (err) {
    console.error('East Bay Play load error:', err);
    renderError(`Couldn't load pool data (${err.message}). Check your connection and retry.`);
  }
}

// Only boot in a browser — under Node (unit tests) there is no document/fetch.
if (typeof document !== 'undefined') {
  boot();
}

// ─── Exports for unit testing (scripts/test_app.mjs) ─────────────────────────
// These are no-ops in the browser; the test file imports this module via
// a thin wrapper. We attach to globalThis so the browser ignores them.
if (typeof module !== 'undefined') {
  module.exports = { getLATime, toMinutes, fmtMinutes, fmtSession, getBadge, sortPools, parseNowOverride };
}
