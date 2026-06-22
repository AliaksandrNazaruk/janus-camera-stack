// admin_config.js — UI for Phase 1 admin config page.
// Vanilla JS, no framework. Uses native fetch + DOM.
//
// Backend contract: /api/v1/admin/config/* (require_admin auth)
// Auth: prompts for CAM_ADMIN_TOKEN on first access, caches in sessionStorage.

(function () {
  'use strict';

  // Sprint X4 console_lib: shared auth/format helpers via ConsoleLib.
  const CL = window.ConsoleLib;
  const $ = CL.$;
  const status = $('status');

  async function apiFetch(path, init) {
    const opts = Object.assign({ headers: {} }, init || {});
    opts.headers['Content-Type'] = opts.headers['Content-Type'] || 'application/json';
    return CL.authFetch('/api/v1/admin/config' + path, opts);
  }

  function setStatus(msg, cls) {
    status.textContent = msg;
    status.className = cls || 'info';
  }

  // ─ Load snapshot ───────────────────────────────────────────────────
  async function loadSnapshot() {
    setStatus('Loading…', 'info');
    try {
      const r = await apiFetch('');
      if (!r.ok) {
        setStatus('Failed to load: HTTP ' + r.status, 'error');
        return;
      }
      const data = await r.json();
      renderSnapshot(data);
      setStatus('Loaded · last refresh ' + new Date().toLocaleTimeString(), 'ok');
    } catch (e) {
      setStatus('Network error: ' + e.message, 'error');
    }
  }

  function renderSnapshot(data) {
    // NAT
    $('natMapping').value = (data.nat_1_1_mapping && data.nat_1_1_mapping !== 'REPLACE_WITH_PUBLIC_IP')
      ? data.nat_1_1_mapping : '';
    $('iceIface').textContent = data.ice_enforce_list || '(unknown)';
    $('janusCfgDir').textContent = data.janus_cfg_dir || '(not found)';

    const jSt = data.janus_active ? '<span class="ok">janus active</span>' : '<span class="danger">janus inactive</span>';
    const rSt = data.relay_active ? '<span class="ok">relay active</span>' : '<span class="danger">relay inactive</span>';
    $('serviceStatus').innerHTML = jSt + ' · ' + rSt;

    // Secrets list
    const sec = $('secretsList');
    sec.innerHTML = '';
    data.secrets.forEach((s) => {
      if (!s.is_sensitive) {
        // Non-secret field — render inline above (TURN_HOST etc.)
        if (s.key === 'TURN_HOST') $('turnHost').value = s.masked || '';
        if (s.key === 'TURN_REALM') $('turnRealm').value = s.masked || '';
        return;
      }
      const row = document.createElement('div');
      row.className = 'secret-row';
      row.innerHTML =
        '<span class="secret-key">' + s.key + '</span>' +
        '<span class="secret-mask">' + s.masked + '</span>' +
        '<span class="secret-age">' + (s.last_rotated_human || '—') + '</span>' +
        '<span class="secret-actions">' +
          '<button type="button" data-key="' + s.key + '" class="rotate-btn">Rotate</button>' +
          (s.is_set ? '<button type="button" data-key="' + s.key + '" class="reveal-btn">Reveal</button>' : '') +
        '</span>';
      sec.appendChild(row);
    });
  }

  // ─ Rotate secret ───────────────────────────────────────────────────
  async function rotateSecret(key) {
    if (!confirm('Rotate ' + key + '?\n\nNew value generated. Apply required to activate. Active sessions using current value will be killed on Apply.')) {
      return;
    }
    setStatus('Rotating ' + key + '…', 'info');
    try {
      const r = await apiFetch('/rotate/' + encodeURIComponent(key), { method: 'POST', body: '{}' });
      if (!r.ok) {
        setStatus('Rotate failed: HTTP ' + r.status, 'error');
        return;
      }
      const data = await r.json();
      // Show new value once — user must copy
      $('revealValue').textContent = data.new_value;
      $('revealModal').style.display = 'flex';
      setStatus(key + ' rotated. Apply required to activate.', 'ok');
      $('applyHint').textContent = '⚠ Unapplied changes — click Apply';
      // Refresh
      loadSnapshot();
    } catch (e) {
      setStatus('Network error: ' + e.message, 'error');
    }
  }

  // ─ Reveal existing secret (re-auth check on backend) ───────────────
  async function revealSecret(key) {
    const expected = 'reveal-' + key;
    const phrase = prompt(
      'Reveal current ' + key + '?\n\n' +
      'Type the confirm phrase exactly:\n' +
      '  ' + expected + '\n\n' +
      '(All reveals are audit-logged.)'
    );
    if (phrase === null) return;   // cancelled
    if (phrase !== expected) {
      alert('Confirm phrase mismatch — reveal NOT executed.');
      return;
    }
    setStatus('Revealing ' + key + '…', 'info');
    try {
      const r = await apiFetch('/reveal/' + encodeURIComponent(key), {
        method: 'POST',
        body: JSON.stringify({ confirm: phrase }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        setStatus('Reveal failed: ' + (err.detail || r.status), 'error');
        return;
      }
      const data = await r.json();
      $('revealValue').textContent = data.value;
      $('revealModal').style.display = 'flex';
      setStatus(key + ' revealed (audit logged)', 'ok');
    } catch (e) {
      setStatus('Network error: ' + e.message, 'error');
    }
  }

  // ─ Set non-secret field ───────────────────────────────────────────
  async function setField(key, value) {
    setStatus('Saving ' + key + '…', 'info');
    try {
      const r = await apiFetch('/set', {
        method: 'POST',
        body: JSON.stringify({ key: key, value: value }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        setStatus('Save failed: ' + (err.detail || r.status), 'error');
        return;
      }
      setStatus(key + ' saved. Apply required to activate.', 'ok');
      $('applyHint').textContent = '⚠ Unapplied changes — click Apply';
    } catch (e) {
      setStatus('Network error: ' + e.message, 'error');
    }
  }

  // ─ Detect public IP ───────────────────────────────────────────────
  async function detectPublicIp() {
    setStatus('Probing public IP…', 'info');
    try {
      const r = await apiFetch('/detect-public-ip', { method: 'POST', body: '{}' });
      const data = await r.json();
      if (data.ip) {
        $('natMapping').value = data.ip;
        setStatus('Detected ' + data.ip + ' via ' + data.method, 'ok');
      } else {
        setStatus('Detection failed: ' + (data.error || 'unknown'), 'error');
      }
    } catch (e) {
      setStatus('Network error: ' + e.message, 'error');
    }
  }

  // ─ Save nat_1_1_mapping (re-renders janus.jcfg, no restart yet) ───
  async function saveNatMapping() {
    const ip = $('natMapping').value.trim();
    if (!ip) {
      setStatus('Enter an IP first', 'error');
      return;
    }
    setStatus('Saving NAT mapping…', 'info');
    try {
      const r = await apiFetch('/set-nat-mapping', {
        method: 'POST',
        body: JSON.stringify({ ip: ip }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        setStatus('Save failed: ' + (err.detail || r.status), 'error');
        return;
      }
      const data = await r.json();
      $('natStatus').textContent = '✓ saved (' + data.rendered_files.length + ' jcfg re-rendered)';
      setStatus('NAT mapping saved. Restart Janus to activate.', 'ok');
      $('applyHint').textContent = '⚠ Unapplied changes — click Apply';
    } catch (e) {
      setStatus('Network error: ' + e.message, 'error');
    }
  }

  // ─ Apply (final step: render + restart janus + relay) ─────────────
  async function applyConfig() {
    setStatus('Applying…', 'info');
    $('applyBtn').disabled = true;
    try {
      const r = await apiFetch('/apply', {
        method: 'POST',
        body: JSON.stringify({ restart_janus: true, restart_relay: true }),
      });
      const data = await r.json();
      let msg = 'Rendered ' + data.rendered.length + ' jcfg · ';
      msg += 'janus ' + (data.janus_restarted ? 'restarted ✓' : 'restart failed ✗') + ' · ';
      msg += 'relay ' + (data.relay_restarted ? 'restarted ✓' : 'restart failed ✗');
      if (data.errors.length) {
        msg += ' · errors: ' + data.errors.join('; ');
        setStatus(msg, 'error');
      } else {
        setStatus(msg, 'ok');
        $('applyHint').textContent = '';
      }
      loadSnapshot();
    } catch (e) {
      setStatus('Apply failed: ' + e.message, 'error');
    } finally {
      $('applyBtn').disabled = false;
    }
  }

  // ─ Event wiring ───────────────────────────────────────────────────
  document.addEventListener('click', (e) => {
    if (e.target.classList.contains('rotate-btn')) {
      rotateSecret(e.target.dataset.key);
    } else if (e.target.classList.contains('reveal-btn')) {
      revealSecret(e.target.dataset.key);
    } else if (e.target.classList.contains('set-field-btn')) {
      const key = e.target.dataset.field;
      const inputId = key === 'TURN_HOST' ? 'turnHost' : 'turnRealm';
      setField(key, $(inputId).value.trim());
    }
  });
  $('refreshBtn').addEventListener('click', loadSnapshot);
  $('detectIpBtn').addEventListener('click', detectPublicIp);
  $('setNatBtn').addEventListener('click', saveNatMapping);
  $('applyBtn').addEventListener('click', () => { $('applyModal').style.display = 'flex'; });
  $('applyCancel').addEventListener('click', () => { $('applyModal').style.display = 'none'; });
  $('applyConfirm').addEventListener('click', () => {
    $('applyModal').style.display = 'none';
    applyConfig();
  });
  $('revealClose').addEventListener('click', () => { $('revealModal').style.display = 'none'; });

  // ─ Boot ──────────────────────────────────────────────────────────
  loadSnapshot();
})();
