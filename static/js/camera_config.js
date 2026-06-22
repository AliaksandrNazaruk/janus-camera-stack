(function(){
  'use strict';
  // Sprint X4 console_lib: token + authFetch shared via ConsoleLib.
  // apiPrefix kept for reverse-proxy compat (api_gateway prepends prefix on
  // off-port access). Empty default = system-wide routes (/config, /sensors).
  const CL = window.ConsoleLib;
  const API = document.body.dataset.apiPrefix || "";
  const $ = CL.$;
  const status = $("status");
  function setStatus(kind, msg){ status.className = kind; status.textContent = msg; }

  const getToken = CL.getToken;
  const clearToken = CL.clearToken;

  async function _getOnce(path, token){
    const headers = {};
    if (token) headers["X-Admin-Token"] = token;
    const r = await fetch(API + path, { cache: "no-store", headers });
    if (!r.ok) {
      const err = new Error("HTTP " + r.status + " " + path);
      err.status = r.status;
      throw err;
    }
    return r.json();
  }
  async function apiGet(path, withAuth){
    try { return await _getOnce(path, withAuth ? getToken(false) : null); }
    catch (e) {
      if (e.status !== 403) throw e;
      clearToken();
      return await _getOnce(path, getToken(true));
    }
  }

  async function _postOnce(path, body, token){
    const r = await fetch(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Admin-Token": token },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const t = await r.text();
      const err = new Error("HTTP " + r.status + ": " + t);
      err.status = r.status;
      throw err;
    }
    return r.json();
  }
  async function apiPost(path, body){
    try { return await _postOnce(path, body, getToken(false)); }
    catch (e) {
      if (e.status !== 403) throw e;
      clearToken();
      return await _postOnce(path, body, getToken(true));
    }
  }

  // modeMap: "WxH" -> sorted descending fps list
  let modeMap = {};
  let currentCfg = null;
  // This page configures ONLY the color encoder pipeline
  // (rtp-rgb@cam-rgb → janus). depth/IR live on separate pipeline
  // (realsense_mux on depth-camera node), so we hard-code to color
  // modes from the RealSense catalog rather than expose a misleading
  // sensor picker on /api/v1/color_camera/camera_config.html.
  function colorModesFromCatalog(catalog){
    if (!catalog || !catalog.sensors) return null;
    const color = catalog.sensors.find(s => s.key === "color");
    return color ? color.modes : null;
  }

  // ── Resolution + FPS ──────────────────────────────────────────
  function populateResolutions(modes){
    const sel = $("resolution");
    sel.innerHTML = "";
    modeMap = {};
    // Group by WxH, collect fps list
    for (const m of modes) {
      const key = m.width + "x" + m.height;
      if (!modeMap[key]) modeMap[key] = new Set();
      // m.fps can be a single number (pyrs) or list (v4l2)
      if (Array.isArray(m.fps)) m.fps.forEach(f => modeMap[key].add(f));
      else modeMap[key].add(m.fps);
    }
    // Convert sets to sorted descending lists
    for (const k of Object.keys(modeMap)) modeMap[k] = Array.from(modeMap[k]).sort((a,b)=>b-a);
    // Sort resolutions by area descending
    const keys = Object.keys(modeMap).sort((a, b) => {
      const [aw, ah] = a.split("x").map(Number);
      const [bw, bh] = b.split("x").map(Number);
      return (bw*bh) - (aw*ah);
    });
    for (const key of keys) {
      const opt = document.createElement("option");
      opt.value = key;
      opt.textContent = `${key}  (${modeMap[key].length} fps options)`;
      sel.appendChild(opt);
    }
  }
  function populateFpsForResolution(key, preferFps){
    const sel = $("fps");
    sel.innerHTML = "";
    const fpsList = (modeMap[key] || []).slice();
    for (const f of fpsList) {
      const opt = document.createElement("option");
      opt.value = String(f); opt.textContent = f + " fps";
      sel.appendChild(opt);
    }
    if (preferFps && fpsList.includes(preferFps)) sel.value = String(preferFps);
  }

  function populateForm(cfg){
    const key = cfg.width + "x" + cfg.height;
    if (!modeMap[key]) {
      const opt = document.createElement("option");
      opt.value = key; opt.textContent = `${key} (current — not in catalog)`;
      $("resolution").appendChild(opt);
      modeMap[key] = [cfg.fps];
    }
    $("resolution").value = key;
    populateFpsForResolution(key, cfg.fps);

    const rot = String(cfg.rotation || 0);
    const radio = document.querySelector(`#rotationGroup input[value="${rot}"]`);
    if (radio) radio.checked = true;

    $("bitrate").value = cfg.bitrate_kbps;
    $("preset").value = cfg.preset || "veryfast";
    $("tune").value = cfg.tune || "zerolatency";
  }

  function collectForm(currentCfg){
    const [w, h] = $("resolution").value.split("x").map(Number);
    const fps = parseInt($("fps").value, 10);
    const rotChecked = document.querySelector("#rotationGroup input:checked");
    const rotation = rotChecked ? parseInt(rotChecked.value, 10) : 0;
    return {
      width: w, height: h, fps,
      bitrate_kbps: parseInt($("bitrate").value, 10),
      gop: fps,
      preset: $("preset").value,
      tune: $("tune").value,
      snapshot_fps: currentCfg.snapshot_fps || 1,
      port: currentCfg.port || 5004,
      rotation,
    };
  }

  async function load(){
    setStatus("info", "Loading color modes + current config…");
    let source = "";
    try {
      // Prefer pyrealsense2 catalog (color sensor) — richer per-format mode list.
      try {
        const catalog = await apiGet("/sensors", false);
        const colorModes = colorModesFromCatalog(catalog);
        if (!colorModes || !colorModes.length) throw new Error("no color modes in catalog");
        populateResolutions(colorModes);
        source = `RealSense ${catalog.device || ""}`.trim();
      } catch (e) {
        // Fallback to raw V4L2 /modes (single sub-device, YUYV only)
        const v4l2 = await apiGet("/modes", false);
        populateResolutions(v4l2.modes || []);
        source = "V4L2 (fallback)";
      }
      // Current encoder config (admin)
      const cfg = await apiGet("/config", true);
      currentCfg = cfg;
      populateForm(cfg);
      $("sourceHint").textContent = `· source: ${source}`;
      setStatus("ok", `Loaded — capturing ${cfg.width}×${cfg.height} @ ${cfg.fps} fps, rotation ${cfg.rotation||0}°`);
      $("liveHint").textContent = "(Apply triggers encoder restart)";
    } catch (e) {
      setStatus("err", "Load failed: " + e.message);
    }
  }

  // ── Event wiring ───────────────────────────────────────────────
  $("resolution").addEventListener("change", () => {
    populateFpsForResolution($("resolution").value, currentCfg ? currentCfg.fps : null);
  });
  $("reloadBtn").addEventListener("click", load);
  $("cfgForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!currentCfg) return;
    const body = collectForm(currentCfg);
    setStatus("info", `Applying ${body.width}×${body.height} @ ${body.fps} fps, rot ${body.rotation}° …`);
    $("applyBtn").disabled = true;
    try {
      const result = await apiPost("/config", body);
      currentCfg = result;
      setStatus("ok", `Applied. Encoder restarting — viewer reconnects ~5s.`);
    } catch (e) {
      setStatus("err", "Apply failed: " + e.message);
    } finally {
      $("applyBtn").disabled = false;
    }
  });

  load();
})();
