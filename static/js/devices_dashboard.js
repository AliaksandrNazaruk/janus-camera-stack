(function(){
  'use strict';
  // Sprint X4 console_lib: shared auth/format via ConsoleLib.
  const CL = window.ConsoleLib;
  // Dashboard served via L4 router prefix="/cameras" — endpoints relative to
  // that prefix. We don't write /api/v1/cameras/* because:
  //   - cameras.example.com (canonical host): no gateway, L4 direct
  //   - api.example.com: dashboard not routed here (no SERVICE_MAP entry)
  const REGISTRY_URL = "/cameras/registry.json";
  const status = document.getElementById("status");
  const container = document.getElementById("devicesContainer");

  function setStatus(kind, msg){ status.className = kind; status.textContent = msg; }

  async function apiPost(url){
    const r = await CL.authFetch(url, { method: "POST" });
    if (!r.ok) {
      let detail;
      try { detail = (await r.json()).detail; } catch { detail = await r.text(); }
      throw new Error(`HTTP ${r.status}: ${detail}`);
    }
    return r.json();
  }

  function pill(text, cls){
    const el = document.createElement("span");
    el.className = `pill ${cls}`;
    el.textContent = text;
    return el;
  }

  function statusPill(s){
    if (!s.provisioning_supported) return pill("not supported", "warn");
    if (s.running === true)        return pill("running", "ok");
    if (s.running === false)       return pill("stopped", "warn");
    return pill("unknown", "warn");
  }

  function buildSensorRow(serial, s){
    const tr = document.createElement("tr");
    tr.dataset.serial = serial;
    tr.dataset.sensor = s.sensor;

    // Sensor label
    const tdLabel = document.createElement("td");
    tdLabel.innerHTML = `<strong>${s.label}</strong> <span class="hint inline">(${s.sensor})</span>`;
    tr.appendChild(tdLabel);

    // Status pill
    const tdStatus = document.createElement("td");
    tdStatus.appendChild(statusPill(s));
    tr.appendChild(tdStatus);

    // Encoder + Mountpoint
    const tdEnc = document.createElement("td");
    tdEnc.innerHTML = s.encoder_unit ? `<code>${s.encoder_unit}</code>` : "—";
    tr.appendChild(tdEnc);
    const tdMp = document.createElement("td");
    tdMp.innerHTML = s.mountpoint_id ? `<code>${s.mountpoint_id}</code>` : "—";
    tr.appendChild(tdMp);

    // Actions
    const tdActions = document.createElement("td");
    tdActions.className = "actions-cell";

    if (s.provisioning_supported && s.running) {
      // Stop button
      const stopBtn = document.createElement("button");
      stopBtn.type = "button"; stopBtn.className = "btn warn-btn"; stopBtn.textContent = "Stop";
      stopBtn.onclick = () => runLifecycle(stopBtn, serial, s.sensor, "stop");
      tdActions.appendChild(stopBtn);

      // Open viewer (dynamic URL)
      const viewerBtn = document.createElement("a");
      viewerBtn.className = "btn"; viewerBtn.href = s.viewer_url || `/cameras/${serial}/${s.sensor}/viewer.html`;
      viewerBtn.textContent = "Open viewer";
      viewerBtn.target = "_blank";
      tdActions.appendChild(viewerBtn);

      const cfgBtn = document.createElement("a");
      cfgBtn.className = "btn"; cfgBtn.href = s.config_url; cfgBtn.textContent = "Config";
      tdActions.appendChild(cfgBtn);
    } else if (s.provisioning_supported) {
      // Initialize button (color, not running)
      const initBtn = document.createElement("button");
      initBtn.type = "button"; initBtn.className = "btn primary-btn"; initBtn.textContent = "Initialize";
      initBtn.onclick = () => runLifecycle(initBtn, serial, s.sensor, "initialize");
      tdActions.appendChild(initBtn);
    } else {
      // Not provisionable — depth/IR
      const disabledBtn = document.createElement("button");
      disabledBtn.type = "button"; disabledBtn.className = "btn"; disabledBtn.disabled = true;
      disabledBtn.title = "Sprint X3: depth/IR pipeline (pyrealsense2 → ffmpeg) not yet implemented";
      disabledBtn.textContent = "Initialize";
      tdActions.appendChild(disabledBtn);

      const cfgBtn = document.createElement("a");
      cfgBtn.className = "btn"; cfgBtn.href = s.config_url; cfgBtn.textContent = "Config (501)";
      cfgBtn.title = "501 Not Implemented — see explainer";
      tdActions.appendChild(cfgBtn);
    }

    tr.appendChild(tdActions);
    return tr;
  }

  function renderDevices(devices){
    container.innerHTML = "";
    if (!devices.length) {
      setStatus("err", "No RealSense devices visible (pyrealsense2 SDK / libusb).");
      return;
    }
    for (const d of devices) {
      const fs = document.createElement("fieldset");
      const legend = document.createElement("legend");
      legend.innerHTML = `${d.name} <span class="hint inline">· serial <code>${d.serial}</code> · firmware ${d.firmware || "?"}</span>`;
      fs.appendChild(legend);

      const table = document.createElement("table");
      table.className = "dev-table";
      table.innerHTML = `<thead><tr><th>Sensor</th><th>Status</th><th>Encoder</th><th>Janus MP</th><th>Actions</th></tr></thead>`;
      const tbody = document.createElement("tbody");
      for (const s of d.sensors) tbody.appendChild(buildSensorRow(d.serial, s));
      table.appendChild(tbody);
      fs.appendChild(table);
      container.appendChild(fs);
    }
  }

  async function runLifecycle(button, serial, sensor, action){
    const original = button.textContent;
    button.disabled = true;
    button.textContent = action === "initialize" ? "Starting…" : "Stopping…";
    setStatus("info", `${action} ${sensor} on ${serial}…`);
    try {
      const result = await apiPost(`/cameras/${serial}/${sensor}/${action}`);
      setStatus("ok", `${action}: ${result.message}`);
      // Re-load registry to reflect new state + redraw rows
      await loadRegistry();
    } catch (e) {
      setStatus("err", `${action} failed: ${e.message}`);
      button.disabled = false;
      button.textContent = original;
    }
  }

  async function loadRegistry(){
    setStatus("info", "Loading registry…");
    try {
      const r = await fetch(REGISTRY_URL, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      renderDevices(data.devices || []);
      const runningCount = (data.devices || []).reduce(
        (acc, d) => acc + d.sensors.filter(s => s.running === true).length, 0);
      setStatus("ok", `Loaded — ${data.devices.length} device(s), ${runningCount} pipeline(s) running.`);
    } catch (e) {
      setStatus("err", `Load failed: ${e.message}`);
    }
  }

  const resetLink = document.getElementById("resetTokenLink");
  if (resetLink) resetLink.addEventListener("click", (e) => {
    e.preventDefault();
    clearToken();
    setStatus("info", "Admin token cleared. The next Initialize/Stop will ask for a new one.");
  });

  loadRegistry();
})();
