// camera_hosts.js — unified "Add Camera Host" page (local == remote).
// Drives the G6 admin API (/api/v1/admin/nodes…, /stream-bindings). Local
// activation (cam10) returns terminal per-sensor `results` synchronously; remote
// activation/provision is async and we poll. One verb, two response readers.
//
// Vanilla IIFE, ConsoleLib for auth (X-Admin-Token) + DOM helpers. A small pure
// surface is exposed on window.CameraHosts for the Node test runner.

(function () {
  'use strict';

  const CL = window.ConsoleLib;
  const $ = CL.$;
  const API = '/api/v1/admin';
  const SENSORS = ['color', 'depth', 'ir1', 'ir2'];
  const body = (typeof document !== 'undefined' && document.body) || { dataset: {} };
  const gatewayLanIp = (body.dataset && body.dataset.gatewayLanIp) || '';
  const LOCAL_ADDRS = new Set(
    [gatewayLanIp, '127.0.0.1', 'localhost', '::1', '0.0.0.0'].filter(Boolean));

  let nodes = [];
  let bindingsByNode = {};   // node_id -> [binding,…]

  function setStatus(msg, cls) { CL.setStatus(msg, cls, 'status'); }

  async function api(path, init) {
    const opts = Object.assign({ headers: {} }, init || {});
    if (opts.body && !opts.headers['Content-Type']) opts.headers['Content-Type'] = 'application/json';
    return CL.authFetch(API + path, opts);
  }
  async function readJson(r) { try { return await r.json(); } catch (e) { return null; } }
  function errDetail(body, r) { return (body && (body.detail || body.message)) || ('HTTP ' + r.status); }

  // ── pure helpers (exported for tests) ───────────────────────────────
  function isLocalAddr(host) { return LOCAL_ADDRS.has((host || '').trim()); }

  /** Map a node's state to the single next action its card should offer. */
  function nextStep(node) {
    if (node.node_id === 'cam10' || node.reachability === 'local') return 'activate';
    if (!node.host_key_pinned) return 'confirm_key';
    const ps = node.provision_state;
    if (ps === 'ready') return 'activate';
    if (ps === 'no_camera') return 'no_camera';
    if (ps === 'failed') return 'failed';
    return 'provision'; // null | reachable | probing
  }

  function statusPill(status) {
    const s = (status || 'offline').toLowerCase();
    const map = {
      online: ['online', 'online'],
      waiting_for_rtp: ['waiting', 'waiting'],
      stale: ['stale', 'stale'],
      degraded: ['degraded', 'degraded'],
      configured_offline: ['offline', 'offline'],
    };
    const [cls, label] = map[s] || ['offline', status || '—'];
    return '<span class="pill ' + cls + '">' + label + '</span>';
  }

  function reachBadge(node) {
    if (node.reachability === 'local') return '<span class="badge local">local · gateway</span>';
    const r = node.reachability || 'unknown';
    const cls = r === 'reachable' ? 'ready' : (r === 'unreachable' ? 'bad' : 'idle');
    let html = '<span class="badge ' + cls + '">' + r + '</span>';
    const ps = node.provision_state;
    if (ps) {
      const pcls = ps === 'ready' ? 'ready' : (ps === 'failed' || ps === 'no_camera' ? 'bad' : 'pending');
      html += ' <span class="badge ' + pcls + '">' + ps + '</span>';
    }
    return html;
  }

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

  /** Human "Ns/Nm/Nh ago" from an epoch-seconds timestamp (null → '—'). */
  function relTime(epochSec, nowMs) {
    if (epochSec == null) return '—';
    const now = (nowMs != null ? nowMs : Date.now()) / 1000;
    const d = Math.max(0, Math.round(now - epochSec));
    if (d < 60) return d + 's ago';
    if (d < 3600) return Math.round(d / 60) + 'm ago';
    if (d < 86400) return Math.round(d / 3600) + 'h ago';
    return Math.round(d / 86400) + 'd ago';
  }

  /** Compact rtp age (ms → "120ms" / "1.4s"); null → ''. */
  function fmtAge(ms) {
    if (ms == null) return '';
    return ms < 1000 ? (ms + 'ms') : (Math.round(ms / 100) / 10 + 's');
  }

  /** Direct viewer URL for a mountpoint (works for local + remote bindings). */
  function viewerUrl(b) { return '/preview/' + encodeURIComponent(b.mountpoint_id); }

  // ── data ────────────────────────────────────────────────────────────
  async function load() {
    try {
      const [rn, rb] = await Promise.all([api('/nodes'), api('/stream-bindings?include_rtp_age=true')]);
      if (!rn.ok) { setStatus('Load nodes failed: ' + errDetail(await readJson(rn), rn), 'danger'); return; }
      nodes = ((await readJson(rn)) || {}).nodes || [];
      bindingsByNode = {};
      if (rb.ok) {
        const bs = ((await readJson(rb)) || {}).bindings || [];
        bs.forEach(b => { (bindingsByNode[b.node_id] = bindingsByNode[b.node_id] || []).push(b); });
      }
      render();
      setStatus('Loaded ' + nodes.length + ' host(s)', 'ok');
    } catch (e) {
      setStatus('Load failed: ' + e.message, 'danger');
    }
  }

  // ── render ──────────────────────────────────────────────────────────
  function sensorChip(node, sensor) {
    const binds = bindingsByNode[node.node_id] || [];
    const b = binds.find(x => x.sensor === sensor);
    const isLocal = node.node_id === 'cam10' || node.reachability === 'local';
    const canActivate = isLocal || node.provision_state === 'ready';
    // An already-bound sensor (b present) gets operator controls; a not-yet-bound
    // sensor on a ready host gets the activate checkbox.
    const isBound = !!b;
    const checkbox = (canActivate && !isBound)
      ? '<input type="checkbox" data-sensor="' + sensor + '">'
      : '';
    const pillHtml = b ? statusPill(b.status) : '<span class="pill offline">—</span>';
    const mp = b ? ' <span class="muted">mp ' + b.mountpoint_id + '</span>' : '';
    const age = (b && b.rtp_age_ms != null) ? ' <span class="muted" title="media age">' + fmtAge(b.rtp_age_ms) + '</span>' : '';
    const fdirOff = b && b.fdir_enabled === false;
    const fdirNote = fdirOff ? ' <span class="badge warn" title="FDIR disabled">fdir off</span>' : '';
    let actions = '';
    if (isBound) {
      const bid = b.binding_id;
      const da = 'data-binding="' + esc(bid) + '"';
      actions = '<div class="stream-actions">' +
        '<a class="tiny" href="' + viewerUrl(b) + '" target="_blank" rel="noopener">Open</a>' +
        '<button class="secondary tiny" data-action="restart-stream" ' + da + '>Restart</button>' +
        '<button class="secondary tiny" data-action="stop-stream" ' + da + '>Stop</button>';
      if (!isLocal) {                       // remote-only: binding removal + FDIR toggle
        actions += '<button class="secondary tiny" data-action="toggle-fdir" data-enabled="' + (fdirOff ? '1' : '0') + '" ' + da + '>' +
          (fdirOff ? 'Enable FDIR' : 'Disable FDIR') + '</button>' +
          '<button class="danger tiny" data-action="remove-binding" ' + da + '>Remove</button>';
      }
      actions += '</div>';
    }
    return '<label class="sensor-chip' + (fdirOff ? ' disabled-fdir' : '') + '">' +
      checkbox + ' <b>' + sensor + '</b> ' + pillHtml + mp + age + fdirNote + actions + '</label>';
  }

  function remoteStep(node) {
    const step = nextStep(node);
    const nid = node.node_id;
    if (step === 'confirm_key') {
      return '<div class="step"><h4>1 · Confirm SSH host key</h4>' +
        '<p class="muted">Verify the fingerprint out-of-band on the node ' +
        '(<span class="mono">ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub</span>), then confirm.</p>' +
        '<div class="host-actions"><button class="secondary" data-action="fetch-key" data-node="' + nid + '">Fetch fingerprint</button>' +
        '<span class="mono" data-key-fp="' + nid + '"></span></div>' +
        '<div class="row" data-key-confirm="' + nid + '" style="display:none">' +
        '<input type="text" data-key-input="' + nid + '" placeholder="SHA256:… (paste from node)" style="width:380px">' +
        '<button class="primary" data-action="confirm-key" data-node="' + nid + '">Confirm</button></div></div>';
    }
    if (step === 'provision') {
      return '<div class="step"><h4>2 · Provision (deploy pipe over SSH)</h4>' +
        '<p class="muted">Pushes the node bundle, probes the camera, starts the mux + agent.</p>' +
        '<div class="host-actions"><button class="primary" data-action="provision" data-node="' + nid + '">Provision</button></div></div>';
    }
    if (step === 'no_camera') {
      return '<div class="step"><h4>⚠ No camera found on node</h4>' +
        '<p class="muted">Provisioning reached the node but found no RealSense. Attach a camera, then re-provision.</p>' +
        '<div class="host-actions"><button class="secondary" data-action="provision" data-node="' + nid + '">Re-provision</button></div></div>';
    }
    if (step === 'failed') {
      return '<div class="step"><h4 class="danger">Provision failed</h4>' +
        '<p class="muted">Check the gateway logs for this node, then retry.</p>' +
        '<div class="host-actions"><button class="secondary" data-action="provision" data-node="' + nid + '">Retry provision</button></div></div>';
    }
    return ''; // ready → sensors below
  }

  /** Node-level operator actions (remote only — cam10 is always-on/local). */
  function nodeOps(node) {
    if (node.node_id === 'cam10' || node.reachability === 'local') return '';
    const nid = esc(node.node_id);
    const da = 'data-node="' + nid + '"';
    const maint = !!node.maintenance;
    return '<span class="node-ops">' +
      '<button class="secondary tiny" data-action="check-node" ' + da + '>Check node</button>' +
      '<button class="secondary tiny" data-action="toggle-maint" data-on="' + (maint ? '1' : '0') + '" ' + da + '>' +
        (maint ? 'End maintenance' : 'Maintenance') + '</button>' +
      '<button class="secondary tiny" data-action="rotate-token" ' + da + '>Rotate token</button>' +
      '<button class="danger tiny" data-action="remove-host" ' + da + '>Remove host</button>' +
      '</span>';
  }

  /** Diagnostics line: maintenance state, last error, last seen. */
  function diagLine(node) {
    if (node.node_id === 'cam10' || node.reachability === 'local') return '';
    const bits = [];
    if (node.maintenance) bits.push('<span class="badge maint">maintenance — FDIR paused</span>');
    if (node.last_error) bits.push('<span class="err">last error: ' + esc(node.last_error) + '</span>');
    if (node.last_checked_at != null) bits.push('last seen ' + relTime(node.last_checked_at));
    if (node.serial) bits.push('serial ' + esc(node.serial));
    return bits.length ? '<p class="diag" data-diag="' + esc(node.node_id) + '">' + bits.join(' · ') + '</p>' : '';
  }

  function hostCard(node) {
    const isLocal = node.node_id === 'cam10' || node.reachability === 'local';
    const title = node.display_name || (isLocal ? 'Gateway camera' : node.host);
    let html = '<div class="host-card ' + (isLocal ? 'local' : 'remote') + '" data-node="' + node.node_id + '">';
    html += '<div class="host-head"><span class="host-title">' + esc(title) + '</span>' +
      '<span class="host-host">' + esc(node.host) + '</span>' +
      '<code class="muted">' + esc(node.node_id) + '</code>' + reachBadge(node) + nodeOps(node) + '</div>';
    html += diagLine(node);

    if (!isLocal) {
      const stepHtml = remoteStep(node);
      if (stepHtml && nextStep(node) !== 'activate') {
        html += stepHtml + '</div>';
        return html;  // not ready to show sensors yet
      }
      if (nextStep(node) !== 'activate') { html += '</div>'; return html; }
    }

    // sensor grid + activate (ready local/remote)
    html += '<div class="sensor-grid">' + SENSORS.map(s => sensorChip(node, s)).join('') + '</div>';
    html += '<div class="host-actions"><button class="primary" data-action="activate" data-node="' + node.node_id + '">Activate selected</button>' +
      '<span class="muted" data-activate-status="' + node.node_id + '"></span></div>';
    html += '</div>';
    return html;
  }

  function render() {
    const c = $('hostsContainer');
    if (!c) return;
    if (!nodes.length) { c.innerHTML = '<p class="muted">No hosts yet. Add one below.</p>'; return; }
    // local first, then remote
    const ordered = nodes.slice().sort((a, b) => {
      const al = (a.node_id === 'cam10') ? 0 : 1, bl = (b.node_id === 'cam10') ? 0 : 1;
      return al - bl || String(a.host).localeCompare(String(b.host));
    });
    c.innerHTML = ordered.map(hostCard).join('');
  }

  // ── actions ─────────────────────────────────────────────────────────
  function checkedSensors(nodeId) {
    const card = document.querySelector('.host-card[data-node="' + nodeId + '"]');
    if (!card) return [];
    return Array.from(card.querySelectorAll('input[type=checkbox][data-sensor]'))
      .filter(cb => cb.checked && !cb.disabled).map(cb => cb.dataset.sensor);
  }

  async function withButton(btn, fn) {
    if (btn) btn.disabled = true;
    try { return await fn(); }
    finally { if (btn) btn.disabled = false; }
  }

  async function activate(nodeId, btn) {
    const sensors = checkedSensors(nodeId);
    const st = document.querySelector('[data-activate-status="' + nodeId + '"]');
    if (!sensors.length) { if (st) st.textContent = 'select at least one sensor'; return; }
    await withButton(btn, async () => {
      if (st) st.textContent = 'activating ' + sensors.join(', ') + '…';
      const r = await api('/nodes/' + encodeURIComponent(nodeId) + '/streams',
        { method: 'POST', body: JSON.stringify({ sensors: sensors }) });
      const data = await readJson(r);
      if (r.status === 429) { if (st) st.textContent = 'rate limited (5/min) — wait a moment'; return; }
      if (!r.ok) { if (st) st.textContent = 'failed: ' + errDetail(data, r); return; }
      if (data && data.results) {                 // local: synchronous terminal outcome
        const bad = data.results.filter(x => !x.ok);
        if (st) st.textContent = bad.length
          ? ('partial: ' + bad.map(x => x.sensor + ' (' + x.detail + ')').join('; '))
          : ('activated ' + sensors.join(', '));
        await load();
      } else {                                     // remote: async → poll bindings
        if (st) st.textContent = 'provisioning streams…';
        const ok = await pollUntil(() => {
          const binds = bindingsByNode[nodeId] || [];
          return sensors.every(s => { const b = binds.find(x => x.sensor === s); return b && b.status === 'online'; });
        });
        if (st) st.textContent = ok ? 'done' : 'timeout: stream(s) did not reach online (still trying — refresh)';
      }
    });
  }

  async function fetchHostKey(nodeId, btn) {
    await withButton(btn, async () => {
      const r = await api('/nodes/' + encodeURIComponent(nodeId) + '/host-key');
      const data = await readJson(r);
      const fp = document.querySelector('[data-key-fp="' + nodeId + '"]');
      const confirm = document.querySelector('[data-key-confirm="' + nodeId + '"]');
      if (!r.ok) { if (fp) fp.textContent = errDetail(data, r); return; }
      if (fp) fp.textContent = 'gateway sees: ' + (data.fingerprint || '?');
      if (confirm) confirm.style.display = '';
    });
  }

  async function confirmHostKey(nodeId, btn) {
    const inp = document.querySelector('[data-key-input="' + nodeId + '"]');
    const expected = inp ? inp.value.trim() : '';
    if (!expected) { setStatus('paste the fingerprint read on the node', 'danger'); return; }
    await withButton(btn, async () => {
      const r = await api('/nodes/' + encodeURIComponent(nodeId) + '/host-key/confirm',
        { method: 'POST', body: JSON.stringify({ expected_fingerprint: expected }) });
      const data = await readJson(r);
      if (!r.ok) { setStatus('host key not pinned: ' + errDetail(data, r), 'danger'); return; }
      setStatus('host key pinned for ' + nodeId, 'ok');
      await load();
    });
  }

  async function provision(nodeId, btn) {
    const sudo = prompt('node sudo password (held in memory for the run, never stored):');
    if (sudo === null) return;
    await withButton(btn, async () => {
      setStatus('provisioning ' + nodeId + '…', 'info');
      const r = await api('/nodes/' + encodeURIComponent(nodeId) + '/provision',
        { method: 'POST', body: JSON.stringify({ sudo_password: sudo }) });
      const data = await readJson(r);
      if (r.status === 503) { const w = $('bundleWarn'); if (w) w.style.display = ''; setStatus('node bundle not built — see banner', 'danger'); return; }
      if (r.status === 412) { setStatus('confirm the host key first', 'danger'); return; }
      if (!r.ok) { setStatus('provision failed to start: ' + errDetail(data, r), 'danger'); return; }
      setStatus('provisioning… (polling state)', 'info');
      const reached = await pollUntil(() => {
        const n = nodes.find(x => x.node_id === nodeId);
        return n && ['ready', 'no_camera', 'failed'].includes(n.provision_state);
      });
      if (!reached) setStatus('provision still running (poll timed out) — refresh to see provision_state', 'danger');
    });
  }

  // ── node-level operator actions ─────────────────────────────────────
  async function checkNode(nodeId, btn) {
    await withButton(btn, async () => {
      const r = await api('/nodes/check', { method: 'POST', body: JSON.stringify({ node_id: nodeId }) });
      const data = await readJson(r);
      if (!r.ok) { setStatus('check failed: ' + errDetail(data, r), 'danger'); return; }
      setStatus('check ' + nodeId + ': ' + (data.reachable ? 'reachable' : ('unreachable — ' + (data.reason || '?'))),
        data.reachable ? 'ok' : 'danger');
      await load();
    });
  }

  async function rotateToken(nodeId, btn) {
    if (!confirm('Rotate the node-agent token for ' + nodeId + '?\nThe node agent will restart with a new token (the stream is unaffected).')) return;
    const sudo = prompt('node sudo password (held in memory for the run, never stored):');
    if (sudo === null) return;
    await withButton(btn, async () => {
      const r = await api('/nodes/' + encodeURIComponent(nodeId) + '/rotate-token',
        { method: 'POST', body: JSON.stringify({ sudo_password: sudo }) });
      const data = await readJson(r);
      if (!r.ok) { setStatus('rotate-token failed to start: ' + errDetail(data, r), 'danger'); return; }
      setStatus('rotating token for ' + nodeId + '… (agent restart)', 'info');
    });
  }

  async function toggleMaintenance(nodeId, on, btn) {
    const enable = on !== '1';   // data-on reflects CURRENT state; click flips it
    await withButton(btn, async () => {
      const r = await api('/nodes/' + encodeURIComponent(nodeId) + '/maintenance',
        { method: 'POST', body: JSON.stringify({ enabled: enable }) });
      const data = await readJson(r);
      if (!r.ok) { setStatus('maintenance toggle failed: ' + errDetail(data, r), 'danger'); return; }
      setStatus(nodeId + (enable ? ' → maintenance (FDIR paused)' : ' → maintenance ended (FDIR resumed)'), 'ok');
      await load();
    });
  }

  async function removeHost(nodeId, btn) {
    if (!confirm('Remove host ' + nodeId + ' from the gateway?\n\nThis will:\n• stop & remove its stream bindings\n• destroy its Janus mountpoints\n• drop its firewall rules\n• forget its host key + agent token\n\nThe node itself keeps the bundle. This cannot be undone.')) return;
    await withButton(btn, async () => {
      const r = await api('/nodes/' + encodeURIComponent(nodeId), { method: 'DELETE' });
      const data = await readJson(r);
      if (!r.ok) { setStatus('remove host failed: ' + errDetail(data, r), 'danger'); return; }
      const n = (data.removed_bindings || []).length;
      setStatus('removed ' + nodeId + ' (' + n + ' binding(s), firewall ' +
        (data.firewall_reconciled ? 'reconciled' : 'NOT reconciled — check logs') + ')',
        data.firewall_reconciled ? 'ok' : 'danger');
      await load();
    });
  }

  // ── per-stream (binding) operator actions ───────────────────────────
  async function streamOp(bindingId, op, btn, verb) {
    await withButton(btn, async () => {
      const r = await api('/stream-bindings/' + encodeURIComponent(bindingId) + '/' + op, { method: 'POST' });
      const data = await readJson(r);
      if (!r.ok) { setStatus(verb + ' ' + bindingId + ' failed: ' + errDetail(data, r), 'danger'); return; }
      setStatus(verb + ' ' + bindingId + ': ' + (data.detail || 'ok'), 'ok');
      await load();
    });
  }
  function restartStream(bindingId, btn) { return streamOp(bindingId, 'restart', btn, 'restart'); }
  function stopStream(bindingId, btn) {
    if (!confirm('Stop stream ' + bindingId + '?\nIt stays configured but goes offline, and FDIR is disabled for it so the monitor will not restart it. Re-enable FDIR (or Restart) to resume auto-recovery.')) return;
    return streamOp(bindingId, 'stop', btn, 'stop');
  }

  async function removeBinding(bindingId, btn) {
    if (!confirm('Remove binding ' + bindingId + '?\nDestroys its Janus mountpoint and frees its mountpoint/port. The node encoder is left as-is.')) return;
    await withButton(btn, async () => {
      const r = await api('/stream-bindings/' + encodeURIComponent(bindingId) + '/remove', { method: 'POST' });
      const data = await readJson(r);
      if (!r.ok) { setStatus('remove binding failed: ' + errDetail(data, r), 'danger'); return; }
      setStatus('removed binding ' + bindingId, 'ok');
      await load();
    });
  }

  async function toggleFdir(bindingId, enabledFlag, btn) {
    const enable = enabledFlag === '1';   // data-enabled=1 means currently OFF → click enables
    await withButton(btn, async () => {
      const r = await api('/stream-bindings/' + encodeURIComponent(bindingId) + '/fdir',
        { method: 'POST', body: JSON.stringify({ enabled: enable }) });
      const data = await readJson(r);
      if (!r.ok) { setStatus('FDIR toggle failed: ' + errDetail(data, r), 'danger'); return; }
      setStatus('FDIR ' + (enable ? 'enabled' : 'disabled') + ' for ' + bindingId, 'ok');
      await load();
    });
  }

  // ── firewall (page-level) ───────────────────────────────────────────
  async function firewall(apply, btn) {
    const out = $('fwOut');
    const st = $('fwStatus');
    await withButton(btn, async () => {
      if (st) st.textContent = apply ? 'applying…' : 'computing diff…';
      const r = await api('/firewall/reconcile?apply=' + (apply ? 'true' : 'false'), { method: 'POST' });
      const data = await readJson(r);
      if (!r.ok) { if (st) st.textContent = 'failed: ' + errDetail(data, r); return; }
      const added = data.added || [], removed = data.removed || [];
      if (st) st.textContent = (apply ? 'applied' : 'dry-run') + ': +' + added.length + ' / -' + removed.length +
        (added.length || removed.length ? '' : ' (in sync)');
      if (out) {
        out.style.display = '';
        out.textContent =
          (added.length ? 'WOULD ADD / ADDED:\n  ' + added.join('\n  ') + '\n' : '') +
          (removed.length ? 'WOULD REMOVE / REMOVED:\n  ' + removed.join('\n  ') + '\n' : '') +
          (added.length || removed.length ? '' : 'No changes — live firewall matches the binding store.');
      }
    });
  }

  async function addNode(btn) {
    const host = ($('hostInput').value || '').trim();
    const name = ($('displayNameInput').value || '').trim();
    const st = $('addStatus');
    if (!host) { st.textContent = 'enter a host IP'; return; }
    if (isLocalAddr(host)) {
      st.innerHTML = '<span class="danger">' + esc(host) + ' is the local gateway</span> — its camera is the built-in <code>cam10</code> host above.';
      const local = document.querySelector('.host-card.local');
      if (local && local.scrollIntoView) local.scrollIntoView({ behavior: 'smooth' });
      return;
    }
    await withButton(btn, async () => {
      st.textContent = 'adding…';
      const r = await api('/nodes', { method: 'POST', body: JSON.stringify({ host: host, display_name: name || null }) });
      const data = await readJson(r);
      if (r.status === 429) { st.textContent = 'rate limited (5/min) — wait a moment'; return; }
      if (!r.ok) { st.innerHTML = '<span class="danger">' + esc(errDetail(data, r)) + '</span>'; return; }
      st.innerHTML = 'added <code>' + esc(data.node_id) + '</code> — confirm its host key next.';
      $('hostInput').value = ''; $('displayNameInput').value = '';
      await load();
    });
  }

  // poll: re-load + test predicate every 2s, up to ~30 tries.
  async function pollUntil(pred, tries) {
    tries = tries || 30;
    for (let i = 0; i < tries; i++) {
      await load();
      if (pred()) return true;
      await new Promise(res => setTimeout(res, 2000));
    }
    return false;
  }

  // ── wiring ──────────────────────────────────────────────────────────
  function onClick(e) {
    const t = e.target.closest ? e.target.closest('[data-action]') : null;
    if (!t) return;
    const nid = t.dataset.node;
    const bid = t.dataset.binding;
    const a = t.dataset.action;
    if (a === 'activate') activate(nid, t);
    else if (a === 'fetch-key') fetchHostKey(nid, t);
    else if (a === 'confirm-key') confirmHostKey(nid, t);
    else if (a === 'provision') provision(nid, t);
    else if (a === 'check-node') checkNode(nid, t);
    else if (a === 'rotate-token') rotateToken(nid, t);
    else if (a === 'toggle-maint') toggleMaintenance(nid, t.dataset.on, t);
    else if (a === 'remove-host') removeHost(nid, t);
    else if (a === 'restart-stream') restartStream(bid, t);
    else if (a === 'stop-stream') stopStream(bid, t);
    else if (a === 'remove-binding') removeBinding(bid, t);
    else if (a === 'toggle-fdir') toggleFdir(bid, t.dataset.enabled, t);
  }

  function init() {
    const c = $('hostsContainer');
    if (c) c.addEventListener('click', onClick);
    const addBtn = $('addNodeBtn');
    if (addBtn) addBtn.addEventListener('click', () => addNode(addBtn));
    const fwd = $('fwDryRun'); if (fwd) fwd.addEventListener('click', () => firewall(false, fwd));
    const fwa = $('fwApply');
    if (fwa) fwa.addEventListener('click', () => {
      if (confirm('Apply firewall rules to live iptables now?\nThis narrows INPUT to the binding store. Review the dry-run first.')) firewall(true, fwa);
    });
    load();
  }

  // Exposed pure surface for tests (no DOM/network).
  window.CameraHosts = {
    isLocalAddr, nextStep, statusPill, reachBadge, hostCard, render,
    sensorChip, nodeOps, diagLine, relTime, fmtAge, viewerUrl,
    _setState: (n, b) => { nodes = n || []; bindingsByNode = b || {}; },
    _get: () => ({ nodes, bindingsByNode }),
  };

  if (typeof document !== 'undefined' && document.addEventListener) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
  }
})();
