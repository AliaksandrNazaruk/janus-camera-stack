'use strict';
/*
 * Unit test for the event-driven recovery decision in core/connection_policy.js.
 * No browser / no test framework — loads the pure core modules into a vm context
 * and asserts the MEDIA_SILENCE_TIMEOUT / VIDEO_STALLED decision matrix.
 *
 * Run:  node test/connection_policy.decide.test.js
 * Exits non-zero on any failed assertion.
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const ctx = { window: { AutonomousPlayer: { Core: {} } }, console };
vm.createContext(ctx);
for (const f of ['core/player_state.js', 'core/codes.js', 'core/domain_events.js',
                 'core/connection_policy.js']) {
  vm.runInContext(fs.readFileSync(path.join(ROOT, f), 'utf8'), ctx, { filename: f });
}
const C = ctx.window.AutonomousPlayer.Core;
const { ConnectionPolicy, DomainEventType: E, PolicyAction: A, RecoveryReason: R } = C;

let failed = 0;
function check(name, got, wantAction, wantReason) {
  const okAction = got.action === wantAction;
  const okReason = wantReason === undefined || got.reason === wantReason;
  if (okAction && okReason) { console.log('  ok  ', name); return; }
  failed++;
  console.error('  FAIL', name, '→ got', JSON.stringify(got),
                'want action=' + wantAction + (wantReason ? ' reason=' + wantReason : ''));
}
const base = { state: 'PLAYING', desiredPlaying: true, webrtcUp: true, firstFrameReceived: true };
const decide = (event, extra) => ConnectionPolicy.decide(event, Object.assign({}, base, extra));

// MEDIA_SILENCE_TIMEOUT — the core change
check('silence + packets advancing → degrade (no teardown)',
  decide(E.MEDIA_SILENCE_TIMEOUT, { packetsAdvancing: true, lastFrameAgeMs: 16000 }), A.MARK_DEGRADED);
check('silence + packets stopped → recover',
  decide(E.MEDIA_SILENCE_TIMEOUT, { packetsAdvancing: false, lastFrameAgeMs: 16000 }), A.REQUEST_RECOVERY, R.NO_FRAMES);
check('silence + signal unknown → recover (legacy fallback)',
  decide(E.MEDIA_SILENCE_TIMEOUT, { lastFrameAgeMs: 16000 }), A.REQUEST_RECOVERY, R.NO_FRAMES);
check('silence + packets advancing but decode wedged past hard cap → recover',
  decide(E.MEDIA_SILENCE_TIMEOUT, { packetsAdvancing: true, lastFrameAgeMs: 60000 }), A.REQUEST_RECOVERY, R.NO_FRAMES);

// VIDEO_STALLED — same guard
check('stalled + packets advancing → degrade',
  decide(E.VIDEO_STALLED, { packetsAdvancing: true }), A.MARK_DEGRADED);
check('stalled + packets stopped → recover',
  decide(E.VIDEO_STALLED, { packetsAdvancing: false }), A.REQUEST_RECOVERY, R.VIDEO_STALLED);

// FPS_DROP — low fps with packets flowing is degraded quality, not a dead connection
check('fps drop + packets advancing → degrade (reconnect would not raise fps)',
  decide(E.FPS_DROP, { packetsAdvancing: true }), A.MARK_DEGRADED);
check('fps drop + packets stopped → recover',
  decide(E.FPS_DROP, { packetsAdvancing: false }), A.REQUEST_RECOVERY, R.FPS_DROP);

// Regression: real failures still escalate regardless of packets
check('ICE_FAILED still hard-recovers',
  decide(E.ICE_FAILED, { packetsAdvancing: true }), A.REQUEST_RECOVERY, R.ICE_FAILED);

if (failed) { console.error(`\n${failed} assertion(s) FAILED`); process.exit(1); }
console.log('\nAll connection_policy decide() assertions passed.');
