(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  class StatsService {
    /**
     * @param {any} cfg
     * @param {any} clock
     * @param {any} logger
     * @param {any} streamingPort
     */
    constructor(cfg, clock, logger, streamingPort){
      this.cfg = cfg;
      this.clock = clock;
      this.log = logger;
      this.streaming = streamingPort;

      this._timer = null;
      this._prev = { t: 0, bytes: 0, packetsLost: 0, packetsReceived: 0, jbDelay: 0, jbEmitted: 0,
                     framesDecoded: 0, freezeCount: 0 };
      // Event-driven recovery signal: last computed inbound-rtp deltas (packets/frames advancing).
      // Read synchronously by PlayerController._buildPolicySnapshot via getMediaHealth().
      this._mediaHealth = null;
      this._text = '';
      this._running = false;
      this._joystick = null;

      // P1-OBS-001: server-side telemetry POST. Throttled — every Nth tick
      // sends raw getStats() summary to /telemetry. Backend (telemetry.py)
      // logs it + updates Prometheus (jitter/RTT/loss/frames/last_report_age).
      // POSTs are fire-and-forget; failures don't break local stats display.
      this._telemetryUrl = cfg.telemetryUrl || '/telemetry';
      this._telemetryIntervalMs = Number(cfg.telemetryIntervalMs || 10000);
      this._lastTelemetryMs = 0;
      this._sessionId = cfg.sessionId || (Math.random().toString(36).slice(2, 12));
      this._cameraName = cfg.cameraName || cfg.streamName || 'color';

      // SYNTH-001: telemetry-post outcome for window.__camstackHealth.telemetry.
      this._lastPostAtMs = null;
      this._lastPostOk = null;
    }

    /** @param {any} joystickService */
    setJoystickService(joystickService){
      this._joystick = joystickService || null;
    }

    start(onUpdate){
      if (this._running) return;
      this._running = true;
      const tick = async () => {
        if (!this._running) return;
        try {
          this._text = await this._collectText();
          onUpdate && onUpdate(this._text);
        } catch (e) {
          this.log.debug('stats_collect_error', { error: String(e) });
        }
      };
      tick();
      this._timer = this.clock.setInterval(tick, this.cfg.statsIntervalMs || 1000);
    }

    stop(){
      this._running = false;
      if (this._timer) {
        this.clock.clearInterval(this._timer);
        this._timer = null;
      }
    }

    /**
     * Synchronous read of the latest media-health deltas for event-driven recovery.
     * Returns { known } = false when there is no fresh sample (caller falls back to
     * legacy timer behaviour). A sample is "fresh" within ~3 stats intervals.
     * @returns {{known:boolean, packetsAdvancing?:boolean, framesAdvancing?:boolean,
     *            freezeActive?:boolean, ageMs?:number}}
     */
    getMediaHealth(){
      const h = this._mediaHealth;
      if (!h || !h.hasData) return { known: false };
      const ageMs = this.clock.nowMs() - h.at;
      const interval = (this.cfg && this.cfg.statsIntervalMs) || 1000;
      if (ageMs > 3 * interval) return { known: false, ageMs };
      return {
        known: true,
        packetsAdvancing: !!h.packetsAdvancing,
        framesAdvancing: !!h.framesAdvancing,
        freezeActive: !!h.freezeActive,
        ageMs,
      };
    }

    async _collectText(){
      const pc = this.streaming.getPeerConnection();
      if (!pc) return 'No peer connection';
      const report = await pc.getStats();

      let inboundVideo = null;
      let selectedPair = null;
      const byId = Object.create(null);

      report.forEach((stat) => {
        byId[stat.id] = stat;
        if (stat.type === 'inbound-rtp' && stat.kind === 'video') {
          if (!inboundVideo || (stat.bytesReceived || 0) > (inboundVideo.bytesReceived || 0)) inboundVideo = stat;
        }
        if (stat.type === 'candidate-pair' && stat.state === 'succeeded' && stat.nominated) {
          selectedPair = stat;
        }
      });
      // SYNTH-001: dereference the selected LOCAL candidate for type/protocol only
      // (relay detection). Address is intentionally NOT captured.
      const localCand = (selectedPair && selectedPair.localCandidateId)
        ? byId[selectedPair.localCandidateId] : null;

      const lines = [];
      const now = this.clock.nowMs();
      const prev = this._prev;
      const hasPrev = prev.t > 0;
      const dt = hasPrev ? Math.max(1, now - prev.t) : 0;

      // ── E2E latency (Camera→Client) — always first ──
      // E2E ≈ cameraJanus + jitterBuf + RTT/2 + decode (math unchanged)
      let rttMs = NaN;
      if (selectedPair) {
        rttMs = Number.isFinite(selectedPair.currentRoundTripTime) ? selectedPair.currentRoundTripTime * 1000 : NaN;
      }

      const jitterBufferDelay = inboundVideo ? inboundVideo.jitterBufferDelay : undefined;
      const jitterBufferEmittedCount = inboundVideo ? inboundVideo.jitterBufferEmittedCount : undefined;

      // Delta-based jitter buffer: per-interval average (not cumulative)
      let jitterBufMs = NaN;
      if (Number.isFinite(jitterBufferDelay) && Number.isFinite(jitterBufferEmittedCount)) {
        if (hasPrev && prev.jbEmitted > 0) {
          const dDelay = jitterBufferDelay - prev.jbDelay;
          const dCount = jitterBufferEmittedCount - prev.jbEmitted;
          if (dCount > 0) {
            jitterBufMs = (dDelay / dCount) * 1000;
          }
        } else if (jitterBufferEmittedCount > 0) {
          // First sample: use cumulative as fallback
          jitterBufMs = (jitterBufferDelay / jitterBufferEmittedCount) * 1000;
        }
      }

      const fpsFromCfg = Number.isFinite(this.cfg && this.cfg.cameraFramerateFps) ? this.cfg.cameraFramerateFps : NaN;
      const fpsFromStats = inboundVideo && Number.isFinite(inboundVideo.framesPerSecond) ? inboundVideo.framesPerSecond : NaN;
      const fps = Number.isFinite(fpsFromCfg) ? fpsFromCfg : fpsFromStats;

      let cameraJanusMs = NaN;
      if (Number.isFinite(fps) && fps > 0) {
        cameraJanusMs = (1000 / fps) / 2;
      }

      const decodeMs = Number.isFinite(this.cfg && this.cfg.decodeLatencyMs) ? this.cfg.decodeLatencyMs : 0;

      const camPart = Number.isFinite(cameraJanusMs) ? cameraJanusMs : 0;
      const jitterPart = Number.isFinite(jitterBufMs) ? jitterBufMs : 0;
      const netPart = Number.isFinite(rttMs) ? (rttMs / 2) : 0;
      const decodePart = decodeMs;

      const e2eMs = camPart + jitterPart + netPart + decodePart;
      lines.push(`E2E      ${Number.isFinite(e2eMs) && e2eMs > 0 ? e2eMs.toFixed(0) + ' ms' : '\u2014'}`);

      // ── FPS ──
      const fpsDisplay = Number.isFinite(fpsFromStats) ? Math.round(fpsFromStats) : '\u2014';
      lines.push(`FPS      ${fpsDisplay}`);

      // ── Bitrate ──
      if (inboundVideo && Number.isFinite(inboundVideo.bytesReceived)) {
        let bitrateStr = '\u2014';
        if (hasPrev) {
          const dbytes = Math.max(0, inboundVideo.bytesReceived - prev.bytes);
          const bitrateKbps = (dbytes * 8) / dt; // kbps because dt is ms
          bitrateStr = bitrateKbps >= 1000
            ? (bitrateKbps / 1000).toFixed(1) + ' Mbps'
            : bitrateKbps.toFixed(0) + ' kbps';
        }
        lines.push(`Bitrate  ${bitrateStr}`);
      }

      // ── Packet loss rate (interval) ──
      if (inboundVideo) {
        const curLost = inboundVideo.packetsLost || 0;
        const curRecv = inboundVideo.packetsReceived || 0;
        if (hasPrev) {
          const dLost = Math.max(0, curLost - prev.packetsLost);
          const dRecv = Math.max(0, curRecv - prev.packetsReceived);
          const dTotal = dLost + dRecv;
          const lossPct = dTotal > 0 ? (dLost / dTotal) * 100 : 0;
          lines.push(`Loss     ${lossPct.toFixed(1)}%`);
        }
      }

      // ── Joystick E2E latency + jitter (Browser→Robot) ──
      if (this._joystick) {
        const joyMs = this._joystick.joyE2eMs;
        const joyJitter = this._joystick.joyJitterMs;
        const e2eStr = Number.isFinite(joyMs) ? joyMs.toFixed(0) + ' ms' : '\u2014';
        const jitStr = Number.isFinite(joyJitter) ? '\u00b1' + joyJitter.toFixed(0) : '';
        lines.push(`Joy E2E  ${e2eStr}${jitStr ? ' ' + jitStr : ''}`);
      }

      // ── Event-driven recovery signal: are packets / decoded frames advancing? ──
      // Distinguishes "media path alive, awaiting keyframe" (packets↑, frames flat — do NOT
      // tear down) from "true silence" (packets flat — recover). Read via getMediaHealth().
      {
        const curRecvH = inboundVideo ? (inboundVideo.packetsReceived || 0) : prev.packetsReceived;
        const curFramesH = inboundVideo && Number.isFinite(inboundVideo.framesDecoded)
          ? inboundVideo.framesDecoded : (prev.framesDecoded || 0);
        const curFreezeH = inboundVideo && Number.isFinite(inboundVideo.freezeCount)
          ? inboundVideo.freezeCount : (prev.freezeCount || 0);
        this._mediaHealth = {
          at: now,
          hasData: hasPrev,
          packetsAdvancing: hasPrev ? (curRecvH - prev.packetsReceived) > 0 : false,
          framesAdvancing: hasPrev ? (curFramesH - (prev.framesDecoded || 0)) > 0 : false,
          freezeActive: hasPrev ? (curFreezeH - (prev.freezeCount || 0)) > 0 : false,
        };
      }

      // ── Save snapshot for delta computation ──
      this._prev = {
        t: now,
        bytes: inboundVideo ? (inboundVideo.bytesReceived || 0) : prev.bytes,
        packetsLost: inboundVideo ? (inboundVideo.packetsLost || 0) : prev.packetsLost,
        packetsReceived: inboundVideo ? (inboundVideo.packetsReceived || 0) : prev.packetsReceived,
        jbDelay: Number.isFinite(jitterBufferDelay) ? jitterBufferDelay : prev.jbDelay,
        jbEmitted: Number.isFinite(jitterBufferEmittedCount) ? jitterBufferEmittedCount : prev.jbEmitted,
        framesDecoded: inboundVideo && Number.isFinite(inboundVideo.framesDecoded)
          ? inboundVideo.framesDecoded : (prev.framesDecoded || 0),
        freezeCount: inboundVideo && Number.isFinite(inboundVideo.freezeCount)
          ? inboundVideo.freezeCount : (prev.freezeCount || 0),
      };

      // P1-OBS-001: throttled telemetry POST. Server keeps Prometheus
      // jitter/RTT/loss/last_report_age counters fresh from real client.
      if (now - this._lastTelemetryMs >= this._telemetryIntervalMs) {
        this._lastTelemetryMs = now;
        this._postTelemetry(inboundVideo, selectedPair, rttMs, jitterBufMs);
      }

      // SYNTH-001: publish a stable, read-only health snapshot for external
      // synthetic/browser tests. Must never throw into the stats loop.
      this._updateHealth(pc, inboundVideo, localCand, rttMs);

      return lines.join('\n');
    }

    /**
     * SYNTH-001: refresh window.__camstackHealth from the current getStats()
     * snapshot. READ-ONLY observability surface for external synthetic tests —
     * contains NO tokens, credentials, session/handle ids, URLs, SDP, or
     * candidate addresses (only candidate type/protocol). Fully try/catch'd:
     * a failure here must not affect playback or the stats display.
     */
    _updateHealth(pc, inboundVideo, localCand, rttMs){
      try {
        const v = this._videoEl();
        const lost = inboundVideo ? (inboundVideo.packetsLost || 0) : 0;
        const recv = inboundVideo ? (inboundVideo.packetsReceived || 0) : 0;
        const total = lost + recv;
        AP.__healthLoadedAtMs = AP.__healthLoadedAtMs || Date.now();
        window.__camstackHealth = {
          version: 1,
          page: { kind: this._healthPageKind(), loadedAtMs: AP.__healthLoadedAtMs },
          video: {
            readyState: v ? v.readyState : 0,
            width: v ? (v.videoWidth || 0) : 0,
            height: v ? (v.videoHeight || 0) : 0,
            paused: v ? !!v.paused : true,
            ended: v ? !!v.ended : false,
            framesDecoded: inboundVideo && Number.isFinite(inboundVideo.framesDecoded) ? inboundVideo.framesDecoded : 0,
            framesDropped: inboundVideo && Number.isFinite(inboundVideo.framesDropped) ? inboundVideo.framesDropped : 0,
            freezeDetected: !!(inboundVideo && (inboundVideo.freezeCount || 0) > 0),
            lastUpdateAtMs: Date.now(),
          },
          webrtc: {
            iceState: pc ? (pc.iceConnectionState || null) : null,
            connectionState: pc ? (pc.connectionState || null) : null,
            signalingState: pc ? (pc.signalingState || null) : null,
            selectedCandidateType: localCand ? (localCand.candidateType || null) : null,
            selectedCandidateProtocol: localCand ? (localCand.protocol || null) : null,
            rttMs: Number.isFinite(rttMs) ? rttMs : null,
            jitterMs: inboundVideo && Number.isFinite(inboundVideo.jitter) ? inboundVideo.jitter * 1000 : null,
            packetLossRatio: total > 0 ? lost / total : null,
            bytesReceived: inboundVideo ? (inboundVideo.bytesReceived || 0) : 0,
          },
          telemetry: {
            enabled: !!this._telemetryUrl,
            lastPostAtMs: this._lastPostAtMs,
            lastPostOk: this._lastPostOk,
          },
        };
      } catch (e) {
        this.log.debug('health_update_error', { error: String(e) });
      }
    }

    _videoEl(){
      try { return document.getElementById('video') || document.querySelector('video'); }
      catch (e) { return null; }
    }

    _healthPageKind(){
      const k = String(this._cameraName || '').toLowerCase();
      if (k.indexOf('depth') >= 0) return 'depth';
      if (k.indexOf('color') >= 0 || k.indexOf('rgb') >= 0) return 'color';
      if (k.indexOf('overlay') >= 0) return 'overlay';
      return 'unknown';
    }

    /** Fire-and-forget POST to backend /telemetry. Failures swallowed. */
    _postTelemetry(inboundVideo, selectedPair, rttMs, jitterBufMs){
      if (!inboundVideo) return;
      const payload = {
        event: 'stats_report',
        session_id: this._sessionId,
        camera: this._cameraName,
        packets_received: inboundVideo.packetsReceived,
        packets_lost: inboundVideo.packetsLost,
        bytes_received: inboundVideo.bytesReceived,
        frames_decoded: inboundVideo.framesDecoded,
        frames_dropped: inboundVideo.framesDropped,
        // current_rtt in seconds (WebRTC schema), backend converts to ms
        current_rtt: Number.isFinite(rttMs) ? rttMs / 1000.0 : null,
        // jitter in seconds (WebRTC schema)
        jitter: Number.isFinite(inboundVideo.jitter) ? inboundVideo.jitter : null,
        local_candidate: selectedPair ? this._candidateInfo(selectedPair, 'local') : null,
        remote_candidate: selectedPair ? this._candidateInfo(selectedPair, 'remote') : null,
        extra: {
          jitter_buffer_ms: Number.isFinite(jitterBufMs) ? jitterBufMs : null,
          joy_e2e_ms: this._joystick ? this._joystick.joyE2eMs : null,
        },
      };
      try {
        // A3: telemetry is viewer-auth gated. Forward the viewer token (same one
        // used for /client-config) so production accepts the report; omitted in
        // dev where the gate is disabled.
        const headers = { 'Content-Type': 'application/json' };
        const viewerToken =
          (typeof window !== 'undefined' && window.__viewerToken) || '';
        if (viewerToken) headers['X-Viewer-Token'] = viewerToken;
        this._lastPostAtMs = Date.now();  // SYNTH-001: health.telemetry tracking
        fetch(this._telemetryUrl, {
          method: 'POST',
          headers,
          body: JSON.stringify(payload),
          keepalive: true,  // survives page unload
        }).then((r) => { this._lastPostOk = !!(r && r.ok); })
          .catch((e) => { this._lastPostOk = false; this.log.debug('telemetry_post_failed', { error: String(e) }); });
      } catch (e) {
        this.log.debug('telemetry_post_threw', { error: String(e) });
      }
    }

    _candidateInfo(pair, side){
      // pair has localCandidateId/remoteCandidateId pointing to candidate stats,
      // but we don't keep the full report map here. Return minimal info.
      // Future enhancement: cache report and dereference. For now {type: ?} suffices
      // because backend just records candidate presence/transport.
      return {
        type: null,           // server treats null as "unknown" (still increments counters)
        protocol: null,
        address: null,
        port: null,
      };
    }
  }

  AP.App.StatsService = StatsService;

  // SYNTH-001: publish a default health skeleton at module load so external
  // synthetic tests can rely on window.__camstackHealth existing immediately
  // after the player scripts load, before the first stats tick. Populated live
  // by StatsService._updateHealth(). Contains no secrets.
  try {
    if (typeof window !== 'undefined' && !window.__camstackHealth) {
      AP.__healthLoadedAtMs = Date.now();
      window.__camstackHealth = {
        version: 1,
        page: { kind: 'unknown', loadedAtMs: AP.__healthLoadedAtMs },
        video: { readyState: 0, width: 0, height: 0, paused: true, ended: false,
                 framesDecoded: 0, framesDropped: 0, freezeDetected: false, lastUpdateAtMs: null },
        webrtc: { iceState: null, connectionState: null, signalingState: null,
                  selectedCandidateType: null, selectedCandidateProtocol: null,
                  rttMs: null, jitterMs: null, packetLossRatio: null, bytesReceived: 0 },
        telemetry: { enabled: true, lastPostAtMs: null, lastPostOk: null },
      };
    }
  } catch (e) { /* non-fatal */ }
})();
