// console_app.js — SPA shell driver for /console.html.
// Hash-router → view dispatchers → render functions per section.
// Each view encapsulates its own state + polling logic; only the active
// view consumes bandwidth.

(function () {
  'use strict';
  const CL = window.ConsoleLib;
  const $ = CL.$;
  const fetchAuth = CL.authFetch;
  const fmtMs = CL.fmtMs;
  const fmtFps = CL.fmtFps;
  const fmtBytes = CL.fmtBytes;
  const fmtAgo = CL.fmtAgo;
  const setStatus = (msg, cls) => CL.setStatus(msg, cls, 'status');

  // ─ Active view tracking + topbar nav highlighting ─────────────────
  const VIEWS = {
    '/overview':    { sectionId: 'view-overview',    init: initOverview,    tick: tickOverview,    tickMs: 5000 },
    '/streams':     { sectionId: 'view-streams',     init: initStreams,     tick: tickStreams,     tickMs: 5000 },
    '/mountpoints': { sectionId: 'view-mountpoints', init: initMountpoints, tick: tickMountpoints, tickMs: 5000 },
    '/fdir':        { sectionId: 'view-fdir',        init: initFdir,        tick: tickFdir,        tickMs: 5000 },
    '/encoders':    { sectionId: 'view-encoders',    init: initEncoders,    tick: tickEncoders,    tickMs: 5000 },
    '/hardware':    { sectionId: 'view-hardware',    init: initHardware,    tick: null,            tickMs: 0 },
    '/audit':       { sectionId: 'view-audit',       init: initAudit,       tick: tickAudit,       tickMs: 10000 },
    '/admin':       { sectionId: 'view-admin',       init: initAdmin,       tick: null,            tickMs: 0 },
    '/settings':    { sectionId: 'view-settings',    init: initSettings,    tick: null,            tickMs: 0 },
    '/soak':        { sectionId: 'view-soak',        init: initSoak,        tick: null,            tickMs: 0 },
  };
  let activeRoute = null;
  let activeTimer = null;

  function showView(route) {
    const view = VIEWS[route];
    if (!view) { CL.navigate('/overview'); return; }
    // Hide all
    document.querySelectorAll('.view-section').forEach((s) => s.classList.remove('active'));
    document.querySelectorAll('.nav-link').forEach((a) => a.classList.remove('active'));
    $(view.sectionId).classList.add('active');
    const navLink = document.querySelector(`.nav-link[data-route="${route}"]`);
    if (navLink) navLink.classList.add('active');
    // Stop previous timer
    if (activeTimer) { clearInterval(activeTimer); activeTimer = null; }
    activeRoute = route;
    setStatus('Loading…', 'info');
    // First load
    Promise.resolve(view.init && view.init()).then(() => {
      if (view.tick && view.tickMs > 0) {
        activeTimer = setInterval(view.tick, view.tickMs);
      }
      setStatus('Updated ' + new Date().toLocaleTimeString(), 'ok');
    }).catch((e) => setStatus('Load err: ' + e.message, 'error'));
  }

  CL.onRouteChange(({ path }) => {
    if (!VIEWS[path]) { CL.navigate('/overview'); return; }
    if (path !== activeRoute) showView(path);
  });

  // ── Overview ──────────────────────────────────────────────────────
  const ovHistory = { videoAge: [], fps: [], jitter: [], rtt: [] };
  const OV_LEN = 60;
  function pushOv(k, v) {
    const a = ovHistory[k]; a.push(v); if (a.length > OV_LEN) a.shift();
  }
  function parsePrompt(text) {
    const out = {};
    text.split('\n').forEach((l) => {
      if (!l || l[0] === '#') return;
      const m = l.match(/^([a-zA-Z_:][\w:]*)(?:\{([^}]*)\})?\s+(\S+)/);
      if (!m) return;
      const [, n, , v] = m;
      const num = parseFloat(v);
      if (!Number.isFinite(num)) return;
      (out[n] = out[n] || []).push(num);
    });
    return out;
  }

  function initOverview() { return tickOverview(); }
  async function tickOverview() {
    try {
      const [hzR, fdR, modeR, evR, strR, metR] = await Promise.all([
        fetch('/healthz'),
        fetchAuth('/fdir/ladder'),
        fetchAuth('/fdir/mode'),
        fetchAuth('/fdir/events?n=10'),
        fetchAuth('/cameras/streams'),
        fetch('/metrics'),
      ]);
      const hz = hzR.ok ? await hzR.json() : null;
      const ladder = fdR.ok ? await fdR.json() : null;
      const mode = modeR.ok ? await modeR.json() : null;
      const events = evR.ok ? await evR.json() : [];
      const streams = strR.ok ? (await strR.json()).streams || [] : [];
      const metrics = metR.ok ? parsePrompt(await metR.text()) : {};

      $('stHealth').textContent = hz && hz.ok ? '✓ ok' : '✗ down';
      $('stMode').textContent = mode ? mode.mode : '?';
      $('stFdir').textContent = ladder ? 'L' + ladder.current_level : '?';
      const activeCnt = streams.filter(s => s.runtime_active).length;
      $('stStreams').textContent = `${activeCnt}/${streams.length}`;

      const va = (metrics['camstack_video_age_ms'] || [])[0];
      const fps = (metrics['camstack_janus_output_fps'] || [])[0];
      const jit = (metrics['camstack_client_jitter_ms'] || [])[0];
      const rtt = (metrics['camstack_client_rtt_ms'] || [])[0];
      if (Number.isFinite(va)) { pushOv('videoAge', va); $('ovVideoAge').textContent = fmtMs(va); CL.renderSparkline('ovVideoAgeChart', ovHistory.videoAge, { color: '#10b981' }); }
      if (Number.isFinite(fps)) { pushOv('fps', fps); $('ovFps').textContent = fmtFps(fps); CL.renderSparkline('ovFpsChart', ovHistory.fps, { color: '#2563eb' }); }
      if (Number.isFinite(jit)) { pushOv('jitter', jit); $('ovJitter').textContent = fmtMs(jit); CL.renderSparkline('ovJitterChart', ovHistory.jitter, { color: '#f59e0b' }); }
      if (Number.isFinite(rtt)) { pushOv('rtt', rtt); $('ovRtt').textContent = fmtMs(rtt); CL.renderSparkline('ovRttChart', ovHistory.rtt, { color: '#8b5cf6' }); }

      const evHtml = (events.slice(0, 8) || []).map((e) => {
        const ts = e.timestamp ? new Date(e.timestamp * 1000).toLocaleTimeString() : '?';
        const cls = e.severity === 'critical' || e.severity === 'error' ? 'failed'
                  : e.severity === 'warn' ? 'inactive' : 'active';
        return `<div class="audit-row"><span class="audit-ts">${ts}</span> <span class="badge ${cls}" style="font-size:10px;">${e.severity}</span> <span class="audit-action">${e.recovery_action}</span>: ${e.outcome}</div>`;
      }).join('') || '<p class="muted">no events</p>';
      $('ovEvents').innerHTML = evHtml;
    } catch (e) { /* swallow polling errors */ }
  }

  // ── Streams ───────────────────────────────────────────────────────
  function initStreams() { return tickStreams(); }
  async function tickStreams() {
    const r = await fetchAuth('/cameras/streams');
    if (!r.ok) { $('vwStreamsList').innerHTML = `<div class="err-msg">HTTP ${r.status}</div>`; return; }
    const data = await r.json();
    const streams = (data.streams || []).slice().sort((a, b) => {
      if (a.sensor === 'color') return -1;
      if (b.sensor === 'color') return 1;
      return (a.serial + a.sensor).localeCompare(b.serial + b.sensor);
    });
    if (!streams.length) {
      $('vwStreamsList').innerHTML = '<p class="muted">No allocations yet</p>'; return;
    }
    const html = streams.map((s) => {
      const rt = s.runtime_active === true ? 'active' : (s.runtime_active === false ? 'inactive' : 'absent');
      const rtLabel = s.runtime_active === true ? 'running' : (s.runtime_active === false ? 'stopped' : 'unknown');
      const dCls = s.desired_active ? 'active' : 'absent';
      const dLabel = s.desired_active ? 'ON' : 'off';
      const drift = (s.desired_active && s.runtime_active === false) ? ' ⚠ drift'
                  : (!s.desired_active && s.runtime_active === true) ? ' ⚠ unexpected' : '';
      const action = s.desired_active ? 'stop' : 'initialize';
      const btn = s.desired_active ? 'Disable' : 'Enable';
      const cls = s.desired_active ? 'danger' : 'primary';
      return `<div class="svc-row" style="grid-template-columns:1fr auto auto auto;">
        <span><code>${s.serial}:${s.sensor}</code> <span class="muted">· mp #${s.mp_id} port ${s.rtp_port}${drift}</span></span>
        <span class="badge ${dCls}" title="boot intent">desired ${dLabel}</span>
        <span class="badge ${rt}" title="live probe">runtime ${rtLabel}</span>
        <button class="${cls} toggle-stream-btn" data-serial="${s.serial}" data-sensor="${s.sensor}" data-action="${action}" style="padding:3px 10px;font-size:11px;">${btn}</button>
      </div>`;
    }).join('');
    $('vwStreamsList').innerHTML = html;
  }

  // ── Mountpoints + per-mp sparklines + inspect ─────────────────────
  const mpAgeByMp = {};
  function initMountpoints() { return tickMountpoints(); }
  async function tickMountpoints() {
    const r = await fetchAuth('/api/v1/admin/dashboard');
    if (!r.ok) { $('vwMpList').innerHTML = `<div class="err-msg">HTTP ${r.status}</div>`; return; }
    const data = await r.json();
    const mps = data.mountpoints || [];
    const live = new Set();
    const html = mps.map((m) => {
      live.add(m.id);
      const age = (m.media && m.media[0] && Number.isFinite(m.media[0].age_ms)) ? m.media[0].age_ms : null;
      if (age !== null) {
        if (!mpAgeByMp[m.id]) mpAgeByMp[m.id] = [];
        mpAgeByMp[m.id].push(age);
        if (mpAgeByMp[m.id].length > 30) mpAgeByMp[m.id].shift();
      }
      const ageLabel = age !== null ? `${age}ms` : '—';
      const ageCls = age === null ? 'absent' : (age < 200 ? 'active' : age < 1000 ? 'inactive' : 'failed');
      return `<div class="mp-row" style="display:grid;grid-template-columns:1fr auto 60px auto;align-items:center;gap:8px;">
        <span><span class="mp-id">#${m.id}</span> · ${m.description || '(no description)'} <span class="muted">[${m.type}]</span></span>
        <span class="badge ${ageCls}">${ageLabel}</span>
        <svg id="mpSpark_${m.id}" viewBox="0 0 60 20" preserveAspectRatio="none" style="width:60px;height:18px;"></svg>
        <span>
          <button class="inspect-mp-btn" data-mp-id="${m.id}" style="padding:2px 7px;font-size:11px;">ℹ</button>
          <a href="/preview/${m.id}" target="_blank" style="padding:2px 8px;font-size:11px;color:#2563eb;">View ↗</a>
          <button class="destroy-mp-btn" data-mp-id="${m.id}" title="Destroy mountpoint" style="padding:2px 7px;font-size:11px;color:#b91c1c;">🗑</button>
        </span>
      </div>`;
    }).join('') || '<p class="muted">No mountpoints</p>';
    $('vwMpList').innerHTML = html;
    mps.forEach((m) => {
      if (mpAgeByMp[m.id] && mpAgeByMp[m.id].length > 1) {
        CL.renderSparkline('mpSpark_' + m.id, mpAgeByMp[m.id], { color: '#10b981' });
      }
    });
    Object.keys(mpAgeByMp).forEach((id) => { if (!live.has(Number(id))) delete mpAgeByMp[id]; });
  }

  // Mountpoint CRUD (ported from operator_dashboard so console is the single hub).
  const MP = '/api/v1/admin';
  async function mpCreate() {
    const st = $('mpStatus');
    const id = parseInt($('mpId').value, 10);
    const port = parseInt($('mpPort').value, 10);
    if (!id || id < 1000) { st.textContent = 'Invalid ID (≥1000)'; return; }
    if (!port || port < 1024 || port > 65535) { st.textContent = 'Invalid RTP port'; return; }
    const body = { id, description: $('mpDesc').value.trim(), rtp_port: port,
                   codec: $('mpCodec').value, payload_type: 96, is_private: $('mpPrivate').checked };
    st.textContent = 'Creating…';
    try {
      let r, data;
      if ($('mpStartEncoder').checked) {
        const inst = $('encInstance').value.trim();
        if (!inst) { st.textContent = 'Encoder instance required'; return; }
        r = await fetchAuth(MP + '/streams/provision', {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ mountpoint: body, encoder_family: $('encFamily').value, encoder_instance: inst,
            encoder_env: { DEVICE: $('encDevice').value.trim(), PIX_FMT: $('encPixFmt').value.trim(),
              WIDTH: parseInt($('encWidth').value, 10) || 640, HEIGHT: parseInt($('encHeight').value, 10) || 480,
              FPS: parseInt($('encFps').value, 10) || 30, BITRATE_KBPS: parseInt($('encBitrate').value, 10) || 1500,
              GOP: parseInt($('encFps').value, 10) || 30, PRESET: 'veryfast', TUNE: 'zerolatency', ROTATION: 0 } }),
        });
        data = await r.json();
        st.textContent = (r.ok && data.mountpoint && data.mountpoint.created)
          ? '✓ #' + data.mountpoint.id + ' + encoder' : ('Failed: ' + (data.detail || JSON.stringify(data)));
      } else {
        r = await fetchAuth(MP + '/mountpoints', {
          method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
        data = await r.json();
        st.textContent = (r.ok && data.created)
          ? '✓ Created #' + data.id + ' (push RTP → 127.0.0.1:' + data.rtp_port + ')'
          : ('Failed: ' + (data.error || data.detail || r.status));
      }
      if (r.ok) { $('mpAddPanel').style.display = 'none'; tickMountpoints(); }
    } catch (e) { st.textContent = 'Network: ' + e.message; }
  }

  async function mpDestroy(mpId) {
    if (!confirm('Destroy mountpoint #' + mpId + '?\nAny live viewers are disconnected immediately.')) return;
    setStatus('Destroying #' + mpId + '…', 'info');
    try {
      const r = await fetchAuth(MP + '/mountpoints/' + mpId, { method: 'DELETE' });
      const data = await r.json().catch(() => ({}));
      if (r.ok && data.destroyed) { setStatus('#' + mpId + ' destroyed', 'ok'); tickMountpoints(); }
      else { setStatus('Destroy failed: ' + (data.error || data.detail || r.status), 'error'); }
    } catch (e) { setStatus('Network: ' + e.message, 'error'); }
  }

  // ── FDIR autonomy ─────────────────────────────────────────────────
  function initFdir() { return tickFdir(); }
  async function tickFdir() {
    const [lR, mR, eR] = await Promise.all([
      fetchAuth('/fdir/ladder'), fetchAuth('/fdir/mode'), fetchAuth('/fdir/events?n=25'),
    ]);
    const ladder = lR.ok ? await lR.json() : null;
    const mode = mR.ok ? await mR.json() : null;
    const events = eR.ok ? await eR.json() : [];
    if (!ladder) { $('vwFdirSummary').innerHTML = '<p class="err-msg">no data</p>'; return; }
    const lvl = ladder.current_level;
    const lvlCls = lvl === 0 ? 'active' : (lvl >= 3 ? 'failed' : 'inactive');
    const modeCls = mode && mode.mode === 'nominal' ? 'active' : mode && mode.mode === 'safe' ? 'failed' : 'inactive';
    const rebootDanger = ladder.reboot_count >= (ladder.max_fdir_reboots || 0) && (ladder.max_fdir_reboots > 0);
    let html = '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:10px;">';
    html += `<div class="stat"><strong>L${lvl}</strong> <span class="badge ${lvlCls}">${ladder.current_level_name}</span><span>ladder level</span></div>`;
    html += `<div class="stat"><strong>${ladder.total_recoveries}</strong><span>total recoveries</span></div>`;
    html += `<div class="stat"><strong>${ladder.reboot_count}/${ladder.max_fdir_reboots || '∞'}</strong><span>${rebootDanger ? '⚠ breaker' : 'reboot budget'}</span></div>`;
    html += `<div class="stat"><span class="badge ${modeCls}">${mode ? mode.mode : '?'}</span><span>mode (${mode ? Math.round(mode.uptime_s) : '?'}s)</span></div>`;
    html += '</div><div style="font-size:12px;font-family:monospace;">';
    (ladder.levels || []).forEach((lv, idx) => {
      const bCls = lv.attempts >= lv.max_attempts ? 'failed' : (lv.attempts > 0 ? 'inactive' : 'active');
      const arrow = idx === lvl ? '→ ' : '  ';
      html += `<div style="padding:3px 0;">${arrow}<code>${lv.name}</code> <span class="badge ${bCls}" style="font-size:10px;">${lv.attempts}/${lv.max_attempts}</span> <span class="muted">cooldown ${lv.cooldown_sec}s</span></div>`;
    });
    html += '</div>';
    $('vwFdirSummary').innerHTML = html;
    const evHtml = events.slice(0, 25).map((e) => {
      const ts = e.timestamp ? new Date(e.timestamp * 1000).toLocaleTimeString() : '?';
      const cls = e.severity === 'critical' || e.severity === 'error' ? 'failed'
                : e.severity === 'warn' ? 'inactive' : 'active';
      return `<div class="audit-row"><span class="audit-ts">${ts}</span> <span class="badge ${cls}" style="font-size:10px;">${e.severity}</span> <span class="audit-action">${e.recovery_action}</span>: ${e.outcome}</div>`;
    }).join('') || '<p class="muted">no events</p>';
    $('vwFdirEvents').innerHTML = evHtml;
  }

  async function resetFdir() {
    if (!confirm('Reset FDIR ladder to 0?')) return;
    const r = await fetchAuth('/fdir/ladder/reset', { method: 'POST' });
    if (!r.ok) { setStatus('Reset failed: ' + r.status, 'error'); return; }
    setStatus('Ladder reset', 'ok'); tickFdir();
  }

  // ── Encoders ──────────────────────────────────────────────────────
  function initEncoders() { return tickEncoders(); }
  async function tickEncoders() {
    const r = await fetchAuth('/api/v1/admin/encoders/status');
    if (!r.ok) { $('vwEncodersList').innerHTML = `<div class="err-msg">HTTP ${r.status}</div>`; return; }
    const encs = await r.json();
    if (!encs.length) { $('vwEncodersList').innerHTML = '<p class="muted">No encoder units</p>'; return; }
    const html = encs.map((e) => {
      const cls = e.active ? 'active' : 'inactive';
      const dims = (e.width && e.height) ? `${e.width}×${e.height}@${e.fps || '?'}` : '';
      const br = e.bitrate_kbps ? `${e.bitrate_kbps}kbps` : '';
      return `<div class="svc-row" style="grid-template-columns:1fr auto auto;">
        <span><code>${e.unit}</code>${dims ? ' <span class="muted">· ' + dims + ' ' + br + '</span>' : ''}${e.ffmpeg_pid ? ' <span class="muted">· pid ' + e.ffmpeg_pid + '</span>' : ''}</span>
        <span class="badge ${cls}">${e.active ? 'active' : 'inactive'}</span>
        <span class="muted" style="font-size:11px;">port ${e.rtp_port || '-'}</span>
      </div>`;
    }).join('');
    $('vwEncodersList').innerHTML = html;
  }

  // ── Hardware probe ────────────────────────────────────────────────
  function initHardware() { /* manual probe — no initial fetch */ }
  async function probeHardware() {
    $('vwHwList').innerHTML = '<p class="muted">Probing…</p>';
    const [vR, rR] = await Promise.all([
      fetchAuth('/api/v1/admin/devices/v4l2?probe_formats=true'),
      fetchAuth('/api/v1/admin/devices/realsense?include_profiles=true'),
    ]);
    let html = '<h3>V4L2 devices</h3>';
    if (vR.ok) {
      const devs = await vR.json();
      html += devs.length ? devs.map((d) => `<div class="audit-row"><code>${d.path}</code> ${d.label || ''} <span class="muted">${d.bus || ''} ${(d.capabilities || []).join(',')}</span></div>`).join('')
                          : '<p class="muted">none</p>';
    } else html += `<div class="err-msg">HTTP ${vR.status}</div>`;
    html += '<h3 style="margin-top:12px;">RealSense devices</h3>';
    if (rR.ok) {
      const rs = await rR.json();
      const devs = rs.devices || [];
      html += devs.length ? devs.map((d) => `<div class="audit-row"><code>${d.serial}</code> ${d.name} fw=${d.firmware} <span class="muted">${d.usb_port || ''}</span></div>`).join('')
                          : '<p class="muted">' + (rs.error || 'none') + '</p>';
    } else html += `<div class="err-msg">HTTP ${rR.status}</div>`;
    $('vwHwList').innerHTML = html;
  }

  // ── Audit log ────────────────────────────────────────────────────
  let auditFilters = {};
  function initAudit() { auditFilters = {}; return tickAudit(); }
  async function tickAudit() {
    const params = new URLSearchParams();
    if (auditFilters.action) params.set('action', auditFilters.action);
    if (auditFilters.target) params.set('target', auditFilters.target);
    const r = await fetchAuth('/api/v1/admin/audit-log?' + params.toString());
    if (!r.ok) { $('vwAuditList').innerHTML = `<div class="err-msg">HTTP ${r.status}</div>`; return; }
    const data = await r.json();
    const entries = data.entries || [];
    if (!entries.length) { $('vwAuditList').innerHTML = '<p class="muted">no entries</p>'; return; }
    $('vwAuditList').innerHTML = entries.slice(0, 50).map((e) => {
      const cls = e.outcome === 'error' || e.outcome === 'failure' ? 'failed'
                : e.outcome === 'success' ? 'active' : 'inactive';
      return `<div class="audit-row"><span class="audit-ts">${e.ts || ''}</span> <span class="badge ${cls}" style="font-size:10px;">${e.outcome || '?'}</span> <span class="audit-action">${e.action}</span> <span class="audit-target">${e.target || ''}</span></div>`;
    }).join('');
  }

  // ── Admin (summary) ───────────────────────────────────────────────
  async function initAdmin() {
    const r = await fetchAuth('/api/v1/admin/config');
    if (!r.ok) { $('vwAdminSummary').innerHTML = `<div class="err-msg">HTTP ${r.status}</div>`; return; }
    const cfg = await r.json();
    let html = '<table style="width:100%;border-collapse:collapse;font-size:12px;">';
    Object.entries(cfg).forEach(([k, v]) => {
      const val = typeof v === 'object' ? JSON.stringify(v) : String(v);
      html += `<tr style="border-bottom:1px solid #e2e8f0;"><td style="padding:4px;font-family:monospace;color:#475569;">${k}</td><td style="padding:4px;font-family:monospace;">${val.length > 100 ? val.slice(0, 100) + '…' : val}</td></tr>`;
    });
    html += '</table>';
    $('vwAdminSummary').innerHTML = html;
  }

  // ── Settings (runtime-config: live WebRTC/Network apply) ──────────
  // Two-step + confirm-bound: /validate returns revision_id+diff_hash;
  // /apply requires confirm == "apply-<diff_hash>" for that exact revision.
  const RC = '/api/v1/admin/runtime-config';
  let setRevision = null; // { revision_id, diff_hash } from the last VALID validate

  async function initSettings() {
    setRevision = null;
    $('setApplyBtn').disabled = true;
    $('setResult').textContent = '—';
    $('setIce').value = ''; $('setTtl').value = '';
    try {
      const r = await fetchAuth(RC + '/effective');
      const eff = r.ok ? await r.json() : null;
      $('setCurIce').textContent = eff ? eff.webrtc.ice_policy : ('HTTP ' + r.status);
      $('setCurTtl').textContent = eff ? eff.webrtc.turn_credential_ttl_seconds : '—';
    } catch (e) { $('setCurIce').textContent = $('setCurTtl').textContent = 'err'; }
    try {
      const r = await fetchAuth(RC + '/capabilities');
      const caps = r.ok ? await r.json() : null;
      if (caps) {
        const ns = (caps.blocked_impacts && caps.blocked_impacts.NEW_SESSIONS_ONLY) || [];
        $('setCaps').textContent = 'apply_supported=' + caps.apply_supported +
          ' · NEW_SESSIONS_ONLY: ' + (ns.length ? ns.join('; ') : 'apply-capable');
      }
    } catch (e) { /* non-fatal */ }
  }

  function buildSetPatch() {
    const w = {};
    const ice = $('setIce').value;
    const ttl = $('setTtl').value;
    if (ice) w.ice_policy = ice;
    if (ttl !== '') w.turn_credential_ttl_seconds = parseInt(ttl, 10);
    return Object.keys(w).length ? { webrtc: w } : null;
  }

  async function setValidate() {
    setRevision = null;
    $('setApplyBtn').disabled = true;
    const patch = buildSetPatch();
    if (!patch) { $('setResult').textContent = 'No change selected (pick a new value first).'; return; }
    const r = await fetchAuth(RC + '/validate', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(patch),
    });
    const j = await r.json();
    $('setResult').textContent = JSON.stringify({
      valid: j.valid, impact: j.impact, diff: j.diff,
      errors: j.errors, warnings: j.warnings, revision_id: j.revision_id,
    }, null, 2);
    if (j.valid && j.revision_id && j.diff_hash) {
      setRevision = { revision_id: j.revision_id, diff_hash: j.diff_hash };
      $('setApplyBtn').disabled = false;
      setStatus('Validated — ready to apply', 'ok');
    } else {
      setStatus(j.valid ? 'Valid but nothing to apply' : 'Not applyable', 'error');
    }
  }

  async function setApply() {
    if (!setRevision) return;
    if (!confirm('Apply revision ' + setRevision.revision_id +
                 '?\nWrites /etc/robot/rs-runtime.env and refreshes the live setting (with rollback on failure).')) return;
    const r = await fetchAuth(RC + '/apply', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ revision_id: setRevision.revision_id, confirm: 'apply-' + setRevision.diff_hash }),
    });
    const j = await r.json();
    $('setResult').textContent = 'HTTP ' + r.status + '\n' + JSON.stringify(j, null, 2);
    setRevision = null;
    $('setApplyBtn').disabled = true;
    if (r.status === 200) { setStatus('Applied (' + (j.changed ? 'changed' : 'no-op') + ')', 'ok'); initSettings(); }
    else { setStatus('Apply ' + (j.status || ('HTTP ' + r.status)), 'error'); }
  }

  // ── Soak viewer (vendored from soak.html) ─────────────────────────
  async function initSoak() {
    const r = await fetchAuth('/api/v1/admin/soak/files');
    if (!r.ok) { $('vwSoakFiles').innerHTML = `<div class="err-msg">HTTP ${r.status}</div>`; return; }
    const data = await r.json();
    if (!data.files.length) {
      $('vwSoakFiles').innerHTML = '<p class="muted">No soak_*.csv files yet</p>'; return;
    }
    $('vwSoakFiles').innerHTML = data.files.reverse().map((f) =>
      `<div class="file-row" data-name="${f.name}"><code>${f.name}</code> <span class="muted">${f.samples} samples · ${fmtBytes(f.size_bytes)} · ${fmtAgo(f.mtime)}</span></div>`
    ).join('');
  }

  function parseCsv(text) {
    const lines = text.split('\n').filter(l => l.trim() && !l.startsWith('#'));
    if (lines.length < 2) return { headers: [], rows: [] };
    const headers = lines[0].split(',');
    const rows = lines.slice(1).map((l) => {
      const parts = l.split(',');
      const obj = {};
      headers.forEach((h, i) => {
        const v = parts[i];
        obj[h] = v === '' || v === undefined ? null : (isNaN(Number(v)) ? v : Number(v));
      });
      return obj;
    });
    return { headers, rows };
  }

  function soakChart(metric, rows) {
    const values = rows.map(r => r[metric]).filter(v => Number.isFinite(v));
    if (!values.length) return `<div class="chart"><h3>${metric}</h3><p class="muted">no data</p></div>`;
    const min = Math.min(...values), max = Math.max(...values);
    const avg = values.reduce((a, b) => a + b, 0) / values.length;
    const sorted = [...values].sort((a, b) => a - b);
    const p99 = sorted[Math.floor(sorted.length * 0.99)];
    const last = values[values.length - 1];
    const w = 280, h = 60, range = max - min || 1, step = w / Math.max(1, values.length - 1);
    const points = values.map((v, i) => `${(i * step).toFixed(1)},${(h - ((v - min) / range) * (h - 4) - 2).toFixed(1)}`).join(' ');
    return `<div class="chart"><h3>${metric}</h3>
      <div style="font-size:11px;color:#64748b;font-family:monospace;margin-bottom:6px;">
        last:${last.toFixed(2)} min:${min.toFixed(2)} max:${max.toFixed(2)} avg:${avg.toFixed(2)} p99:${p99.toFixed(2)}
      </div>
      <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="width:100%;height:${h}px;background:#f8fafc;border-radius:3px;">
        <polyline points="${points}" fill="none" stroke="#2563eb" stroke-width="1.2" vector-effect="non-scaling-stroke"/>
      </svg></div>`;
  }

  async function loadSoakRun(name) {
    const r = await fetchAuth('/api/v1/admin/soak/file/' + encodeURIComponent(name));
    if (!r.ok) { setStatus('Failed: ' + r.status, 'error'); return; }
    const { headers, rows } = parseCsv(await r.text());
    if (!rows.length) { setStatus('Empty', 'error'); return; }
    const tsCol = headers.indexOf('ts') >= 0 ? 'ts' : headers[0];
    const firstTs = rows[0][tsCol], lastTs = rows[rows.length - 1][tsCol];
    $('vwSoakRunTitle').textContent = name;
    $('vwSoakRunMeta').innerHTML = `Samples: <strong>${rows.length}</strong> · Span: <strong>${((lastTs - firstTs) / 3600).toFixed(2)}h</strong> · Start: ${new Date(firstTs * 1000).toLocaleString()}`;
    const metrics = headers.filter((h) => h !== 'ts' && h !== 'mode');
    $('vwSoakCharts').innerHTML = metrics.map((m) => soakChart(m, rows)).join('');
    $('vwSoakSelected').style.display = 'block';
  }

  // ── Inspect modal ─────────────────────────────────────────────────
  async function inspectMp(mpId) {
    $('vwInspectTitle').textContent = 'Mountpoint #' + mpId;
    $('vwInspectBody').innerHTML = '<p class="muted">Loading…</p>';
    $('vwInspectModal').classList.add('open');
    const r = await fetchAuth('/api/v1/admin/mountpoints/' + mpId + '/info');
    if (!r.ok) { $('vwInspectBody').innerHTML = `<div class="err-msg">HTTP ${r.status}</div>`; return; }
    const data = await r.json();
    const raw = data.raw || {}, summary = data.summary || {};
    let html = `<div style="background:#f1f5f9;padding:8px;border-radius:4px;margin-bottom:10px;font-size:12px;">
      <strong>${raw.description || '(no description)'}</strong><br>
      type: <code>${raw.type || '?'}</code> · enabled: <code>${raw.enabled ? '✓' : '✗'}</code> · viewers: <strong>${raw.viewers ?? '?'}</strong>
    </div>`;
    if (raw.media && raw.media.length) {
      html += '<h4 style="margin:8px 0 4px;">Media tracks</h4><table style="width:100%;border-collapse:collapse;font-size:11px;"><thead><tr style="background:#f8fafc;"><th style="padding:4px;text-align:left;">mid</th><th>type</th><th>codec</th><th>pt</th><th>port</th><th>age_ms</th></tr></thead><tbody>';
      raw.media.forEach((m) => {
        html += `<tr style="border-top:1px solid #e2e8f0;"><td style="padding:4px;">${m.mid || '-'}</td><td>${m.type || '-'}</td><td>${m.codec || '-'}</td><td>${m.pt ?? '-'}</td><td>${m.port ?? '-'}</td><td>${m.age_ms ?? '-'}</td></tr>`;
      });
      html += '</tbody></table>';
    }
    html += `<div style="background:#fef9c3;padding:6px;border-radius:4px;margin-top:10px;font-size:12px;">
      status: <code>${summary.status || '?'}</code> · video_age_ms: <strong>${summary.video_age_ms ?? '?'}</strong>
    </div>`;
    html += `<details style="margin-top:10px;"><summary style="cursor:pointer;color:#64748b;font-size:11px;">Raw JSON</summary><pre style="background:#1e293b;color:#e2e8f0;padding:8px;border-radius:4px;font-size:11px;overflow-x:auto;">${JSON.stringify(data, null, 2).replace(/</g, '&lt;')}</pre></details>`;
    $('vwInspectBody').innerHTML = html;
  }

  // ── Toggles ──────────────────────────────────────────────────────
  async function toggleStream(serial, sensor, action) {
    setStatus(action + ' ' + serial + ':' + sensor + '…', 'info');
    const r = await fetchAuth(`/api/v1/cameras/${serial}/${sensor}/${action}`, { method: 'POST' });
    if (!r.ok) { setStatus(`${action} failed: ${r.status}`, 'error'); return; }
    setStatus(action + ' ok', 'ok');
    tickStreams();
  }

  // ── Delegated click handlers ─────────────────────────────────────
  document.addEventListener('click', (e) => {
    if (e.target.classList.contains('toggle-stream-btn')) {
      const d = e.target.dataset;
      toggleStream(d.serial, d.sensor, d.action);
    } else if (e.target.classList.contains('inspect-mp-btn')) {
      inspectMp(parseInt(e.target.dataset.mpId, 10));
    } else if (e.target.classList.contains('destroy-mp-btn')) {
      mpDestroy(parseInt(e.target.dataset.mpId, 10));
    } else if (e.target.classList.contains('file-row') || e.target.parentElement?.classList?.contains('file-row')) {
      const row = e.target.classList.contains('file-row') ? e.target : e.target.parentElement;
      document.querySelectorAll('.file-row').forEach(r => r.classList.remove('active'));
      row.classList.add('active');
      loadSoakRun(row.dataset.name);
    }
  });

  // Init wire-up
  $('vwFdirResetBtn').addEventListener('click', resetFdir);
  $('vwHwProbeBtn').addEventListener('click', probeHardware);
  $('mpAddToggle').addEventListener('click', () => {
    const p = $('mpAddPanel'); p.style.display = (p.style.display === 'none') ? 'block' : 'none';
  });
  $('mpCancelBtn').addEventListener('click', () => { $('mpAddPanel').style.display = 'none'; });
  $('mpCreateBtn').addEventListener('click', mpCreate);
  $('mpStartEncoder').addEventListener('change', () => {
    $('mpEncFields').style.display = $('mpStartEncoder').checked ? 'grid' : 'none';
  });
  $('setValidateBtn').addEventListener('click', setValidate);
  $('setApplyBtn').addEventListener('click', setApply);
  $('setRefreshBtn').addEventListener('click', initSettings);
  $('vwInspectClose').addEventListener('click', () => $('vwInspectModal').classList.remove('open'));
  $('vwInspectModal').addEventListener('click', (e) => {
    if (e.target.id === 'vwInspectModal') $('vwInspectModal').classList.remove('open');
  });
  $('auditFilterApply').addEventListener('click', () => {
    auditFilters = { action: $('auditFilterAction').value.trim(), target: $('auditFilterTarget').value.trim() };
    tickAudit();
  });
  $('auditFilterClear').addEventListener('click', () => {
    $('auditFilterAction').value = ''; $('auditFilterTarget').value = '';
    auditFilters = {}; tickAudit();
  });

  // ── Phase 4: Keyboard shortcuts (Linear/Notion-style g-prefix) ────
  // Press 'g' then one letter to navigate. 30s timeout if nothing is pressed.
  // Skip when focused in an input/textarea/select (operator typing filters).
  const KB_SHORTCUTS = {
    'o': '/overview', 's': '/streams', 'm': '/mountpoints', 'f': '/fdir',
    'e': '/encoders', 'h': '/hardware', 'a': '/audit', 'c': '/admin',
    'k': '/soak',  // 'soak' starts with s — collision; use 'k' (ekg-style)
  };
  let kbBuffer = '';
  let kbTimer = null;
  function clearKbBuf() { kbBuffer = ''; if (kbTimer) clearTimeout(kbTimer); kbTimer = null; }

  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const tag = (document.activeElement && document.activeElement.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    if (e.key === 'Escape') {
      clearKbBuf();
      $('vwInspectModal').classList.remove('open');
      return;
    }
    if (kbBuffer === 'g') {
      const route = KB_SHORTCUTS[e.key];
      if (route) { e.preventDefault(); CL.navigate(route); }
      clearKbBuf();
      return;
    }
    if (e.key === 'g') {
      kbBuffer = 'g';
      setStatus('g → o/s/m/f/e/h/a/c/k …', 'info');
      kbTimer = setTimeout(() => { clearKbBuf(); setStatus('Idle', 'info'); }, 1500);
    }
  });

  // ── Phase 4: URL state preservation for audit filters ────────────
  // When operator types in auditFilterAction/Target and clicks Apply, save
  // values to the hash query — refresh/back/share preserves filter state.
  CL.onRouteChange(({ path, query }) => {
    if (path === '/audit') {
      if (query.action !== undefined) $('auditFilterAction').value = query.action;
      if (query.target !== undefined) $('auditFilterTarget').value = query.target;
      auditFilters = {
        action: query.action || '',
        target: query.target || '',
      };
    }
  });
  // Override apply to also push query
  const _origApply = $('auditFilterApply');
  _origApply.replaceWith(_origApply.cloneNode(true));  // clean old listener (from before)
  $('auditFilterApply').addEventListener('click', () => {
    const q = {
      action: $('auditFilterAction').value.trim(),
      target: $('auditFilterTarget').value.trim(),
    };
    CL.navigate('/audit', q);  // updates hash, fires route handler that re-runs tickAudit
    auditFilters = q;
    tickAudit();
  });

  // Boot — default route
  if (!location.hash) location.hash = '#/overview';
  showView(CL.parseHash().path || '/overview');
})();
