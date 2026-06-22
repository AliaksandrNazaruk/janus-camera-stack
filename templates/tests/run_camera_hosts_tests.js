/* Unit tests for camera_hosts.js — pure helpers + card rendering (Node, no browser).
   Suppresses the IIFE auto-init (document.readyState='loading') so loading the
   script performs no network, then exercises the window.CameraHosts surface. */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

let passed = 0;
let failed = 0;

function assert(cond, msg) {
  if (!cond) {
    failed++;
    console.error('  FAIL:', msg);
    throw new Error('Assertion failed: ' + (msg || ''));
  }
  passed++;
}

function makeSandbox() {
  const doc = {
    readyState: 'loading',   // → IIFE defers init to DOMContentLoaded (never fired)
    body: { dataset: { gatewayLanIp: '192.168.1.10', camType: 'color_camera' } },
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    createElement: () => ({ setAttribute() {}, appendChild() {}, style: {} }),
  };
  const sandbox = {
    window: { addEventListener: () => {} },
    document: doc,
    console,
    location: { hash: '' },
    sessionStorage: { _s: {}, getItem(k) { return this._s[k] || null; }, setItem(k, v) { this._s[k] = v; }, removeItem(k) { delete this._s[k]; } },
    prompt: () => '',
    fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }),
    setTimeout: () => 0,
    clearTimeout: () => {},
  };
  vm.createContext(sandbox);
  return sandbox;
}

function loadScript(sandbox, rel) {
  const p = path.resolve(__dirname, rel);
  vm.runInContext(fs.readFileSync(p, 'utf8'), sandbox, { filename: p });
}

function main() {
  const sb = makeSandbox();
  loadScript(sb, '../../static/js/console_lib.js');
  loadScript(sb, '../../static/js/camera_hosts.js');
  const CH = sb.window.CameraHosts;
  assert(CH, 'window.CameraHosts exposed');

  // ── isLocalAddr (review L1: recognise the gateway's own address) ──
  assert(CH.isLocalAddr('192.168.1.10') === true, 'gateway LAN IP is local');
  assert(CH.isLocalAddr('127.0.0.1') === true, 'loopback is local');
  assert(CH.isLocalAddr(' 192.168.1.10 ') === true, 'trims whitespace');
  assert(CH.isLocalAddr('192.168.1.42') === false, 'remote IP is not local');

  // ── nextStep state machine (review M2) ──
  assert(CH.nextStep({ node_id: 'cam10', reachability: 'local' }) === 'activate', 'local → activate');
  assert(CH.nextStep({ node_id: 'n1', host_key_pinned: false }) === 'confirm_key', 'no key → confirm_key');
  assert(CH.nextStep({ node_id: 'n1', host_key_pinned: true, provision_state: null }) === 'provision', 'pinned+unprovisioned → provision');
  assert(CH.nextStep({ node_id: 'n1', host_key_pinned: true, provision_state: 'probing' }) === 'provision', 'probing → provision');
  assert(CH.nextStep({ node_id: 'n1', host_key_pinned: true, provision_state: 'ready' }) === 'activate', 'ready → activate');
  assert(CH.nextStep({ node_id: 'n1', host_key_pinned: true, provision_state: 'no_camera' }) === 'no_camera', 'no_camera state surfaced');
  assert(CH.nextStep({ node_id: 'n1', host_key_pinned: true, provision_state: 'failed' }) === 'failed', 'failed state surfaced');

  // ── statusPill ──
  assert(/pill online/.test(CH.statusPill('online')), 'online pill');
  assert(/pill offline/.test(CH.statusPill('configured_offline')), 'offline pill');
  assert(/pill waiting/.test(CH.statusPill('waiting_for_rtp')), 'waiting pill');

  // ── reachBadge (reachability ⟂ provision_state, review M2) ──
  assert(/badge local/.test(CH.reachBadge({ reachability: 'local' })), 'local badge');
  const rb = CH.reachBadge({ reachability: 'reachable', provision_state: 'ready' });
  assert(/reachable/.test(rb) && /ready/.test(rb), 'remote badge shows reachability AND provision_state independently');

  // ── hostCard: local shows sensor grid + activate ──
  CH._setState(
    [{ node_id: 'cam10', host: '127.0.0.1', reachability: 'local' }],
    { cam10: [{ node_id: 'cam10', sensor: 'color', status: 'online', mountpoint_id: 1305 }] });
  const localCard = CH.hostCard({ node_id: 'cam10', host: '127.0.0.1', reachability: 'local' });
  assert(/Activate selected/.test(localCard), 'local card has activate button');
  assert(/sensor-chip/.test(localCard), 'local card has sensor chips');

  // ── hostCard: remote without pinned key shows confirm step, not activate ──
  const remoteCard = CH.hostCard({ node_id: 'n1', host: '192.168.1.42', reachability: 'reachable', host_key_pinned: false });
  assert(/Confirm SSH host key/.test(remoteCard), 'remote unpinned card shows confirm-key step');
  assert(!/Activate selected/.test(remoteCard), 'remote unpinned card has NO activate button yet');

  // ── hostCard: remote no_camera shows the re-provision affordance ──
  const noCam = CH.hostCard({ node_id: 'n2', host: '192.168.1.43', reachability: 'reachable', host_key_pinned: true, provision_state: 'no_camera' });
  assert(/No camera found/.test(noCam) && /Re-provision/.test(noCam), 'no_camera card offers re-provision');

  // ── operator console (P0): display helpers ──
  const now = 1_000_000_000_000;  // fixed nowMs for determinism
  assert(CH.relTime(null) === '—', 'relTime null → dash');
  assert(CH.relTime((now / 1000) - 8, now) === '8s ago', 'relTime seconds');
  assert(CH.relTime((now / 1000) - 120, now) === '2m ago', 'relTime minutes');
  assert(CH.fmtAge(null) === '', 'fmtAge null → empty');
  assert(CH.fmtAge(120) === '120ms', 'fmtAge ms');
  assert(CH.fmtAge(1400) === '1.4s', 'fmtAge seconds');
  assert(CH.viewerUrl({ mountpoint_id: 2000 }) === '/preview/2000', 'viewerUrl by mountpoint');

  // ── nodeOps: remote has full op set; local has none ──
  const ops = CH.nodeOps({ node_id: 'n1', reachability: 'reachable' });
  assert(/check-node/.test(ops) && /rotate-token/.test(ops) && /remove-host/.test(ops) && /toggle-maint/.test(ops),
    'remote nodeOps has check/rotate/remove/maintenance');
  assert(CH.nodeOps({ node_id: 'cam10', reachability: 'local' }) === '', 'local node has no node-ops');
  // maintenance label flips with state
  assert(/End maintenance/.test(CH.nodeOps({ node_id: 'n1', reachability: 'reachable', maintenance: true })),
    'maintenance-on shows End maintenance');

  // ── diagLine: maintenance badge + last_error + last_seen ──
  const diag = CH.diagLine({ node_id: 'n1', reachability: 'reachable', maintenance: true,
    last_error: 'pyrealsense2 not installed', last_checked_at: (now / 1000) - 12, serial: 'ABC123' });
  assert(/maintenance/.test(diag) && /pyrealsense2 not installed/.test(diag) && /last seen/.test(diag) && /ABC123/.test(diag),
    'diagLine shows maintenance, last_error, last_seen, serial');
  assert(CH.diagLine({ node_id: 'cam10', reachability: 'local' }) === '', 'local has no diag line');

  // ── sensorChip: bound REMOTE stream → full lifecycle controls ──
  CH._setState(
    [{ node_id: 'n1', host: '192.168.1.42', reachability: 'reachable', host_key_pinned: true, provision_state: 'ready' }],
    { n1: [{ node_id: 'n1', sensor: 'color', status: 'online', mountpoint_id: 2000, binding_id: 'SER:color', rtp_age_ms: 120, fdir_enabled: true }] });
  const remoteChip = CH.sensorChip({ node_id: 'n1', reachability: 'reachable', provision_state: 'ready' }, 'color');
  assert(/Open/.test(remoteChip) && /restart-stream/.test(remoteChip) && /stop-stream/.test(remoteChip) &&
    /remove-binding/.test(remoteChip) && /toggle-fdir/.test(remoteChip), 'bound remote chip has full lifecycle controls');
  assert(/120ms/.test(remoteChip), 'bound chip shows rtp age');
  assert(/\/preview\/2000/.test(remoteChip), 'Open links to the mountpoint viewer');
  // not-yet-bound sensor on a ready remote → activate checkbox, no lifecycle buttons
  const unboundChip = CH.sensorChip({ node_id: 'n1', reachability: 'reachable', provision_state: 'ready' }, 'depth');
  assert(/type="checkbox"/.test(unboundChip) && !/restart-stream/.test(unboundChip), 'unbound ready sensor shows activate checkbox only');

  // ── sensorChip: bound LOCAL stream → Open/Restart/Stop but NOT Remove/FDIR ──
  CH._setState(
    [{ node_id: 'cam10', host: '127.0.0.1', reachability: 'local' }],
    { cam10: [{ node_id: 'cam10', sensor: 'color', status: 'online', mountpoint_id: 1305, binding_id: 'SER:color', fdir_enabled: true }] });
  const localChip = CH.sensorChip({ node_id: 'cam10', reachability: 'local' }, 'color');
  assert(/restart-stream/.test(localChip) && /stop-stream/.test(localChip), 'local bound chip can restart/stop');
  assert(!/remove-binding/.test(localChip) && !/toggle-fdir/.test(localChip), 'local bound chip has NO remove-binding / FDIR toggle (projection)');

  // ── FDIR-off binding renders the "fdir off" affordance + Enable action ──
  CH._setState(
    [{ node_id: 'n1', host: '192.168.1.42', reachability: 'reachable', host_key_pinned: true, provision_state: 'ready' }],
    { n1: [{ node_id: 'n1', sensor: 'depth', status: 'degraded', mountpoint_id: 2002, binding_id: 'SER:depth', fdir_enabled: false }] });
  const offChip = CH.sensorChip({ node_id: 'n1', reachability: 'reachable', provision_state: 'ready' }, 'depth');
  assert(/fdir off/.test(offChip) && /Enable FDIR/.test(offChip) && /data-enabled="1"/.test(offChip),
    'fdir-disabled chip shows badge + Enable FDIR with data-enabled=1');

  console.log(`\ncamera_hosts: ${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

main();
