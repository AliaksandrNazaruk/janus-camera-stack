// Mock fleet view-model — mirrors the shape of /api/v1/ui/* endpoints in the
// spec. Used by the Operator Console UI kit screens. Exposed on window.
window.FLEET = {
  gateway: { lanIp: "192.168.1.10", cidr: "192.168.1.0/24" },
  services: [
    { name: "Gateway", status: "healthy" },
    { name: "Janus", status: "healthy" },
    { name: "Cloudflare", status: "healthy" },
    { name: "FDIR", status: "enabled" },
    { name: "Firewall", status: "synced" },
    { name: "Streams", status: "degraded", label: "3/4 live" },
  ],
  metrics: {
    nodesOnline: [2, 2], streamsLive: [3, 4], fdirEvents: 1, openAlerts: 1,
  },
  alert: {
    severity: "warning", count: 1,
    message: "cam55/depth waiting_for_rtp — no packets received in 24s",
    action: "Open diagnostics",
  },
  attention: {
    binding: "cam55:depth", status: "waiting_for_rtp",
    error: "no packets received · rs-stream@depth failed on node",
  },
  nodes: [
    {
      nodeId: "cam10", host: "192.168.1.10", role: "local_gateway",
      model: "RealSense D435i", serial: "938422071421", status: "online", local: true,
      health: { agent: "online", camera: "present", lastSeen: "now", provision: "ready", maintenance: "off", hostKey: "pinned", token: "present" },
      streams: [
        { sensor: "color", status: "online", mp: 1305, port: 5004, rtpAge: "80ms" },
        { sensor: "depth", status: "online", mp: 1306, port: 5006, rtpAge: "85ms" },
      ],
    },
    {
      nodeId: "cam55", host: "192.168.1.55", role: "remote_producer",
      model: "RealSense D435", serial: "141722072135", status: "online", local: false,
      health: { agent: "online", camera: "present", lastSeen: "8s ago", provision: "ready", maintenance: "off", hostKey: "pinned", token: "present" },
      streams: [
        { sensor: "color", status: "online", mp: 2000, port: 5100, rtpAge: "90ms" },
        { sensor: "depth", status: "stale", mp: 2001, port: 5102, rtpAge: "24s" },
      ],
    },
  ],
  streams: [
    { binding: "cam10:color", node: "cam10", sensor: "color", status: "online", rtpAgeMs: 80, mountpoint: 1305, rtpPort: 5004, fdir: "enabled", lastError: null },
    { binding: "cam10:depth", node: "cam10", sensor: "depth", status: "online", rtpAgeMs: 85, mountpoint: 1306, rtpPort: 5006, fdir: "enabled", lastError: null },
    { binding: "cam55:color", node: "cam55", sensor: "color", status: "online", rtpAgeMs: 110, mountpoint: 2000, rtpPort: 5100, fdir: "enabled", lastError: null },
    { binding: "cam55:depth", node: "cam55", sensor: "depth", status: "stale", rtpAgeMs: 24000, mountpoint: 2001, rtpPort: 5102, fdir: "disabled", lastError: "no RTP" },
  ],
  events: [
    { time: "14:22", target: "cam55:color", message: "restarted by operator", result: "ok", action: "stream.restart", actor: "operator" },
    { time: "14:20", target: "cam55", message: "FDIR skipped — maintenance was on", result: "suppressed", reason: "maintenance" },
    { time: "14:15", message: "firewall reconcile applied", result: "ok", action: "firewall.apply", actor: "admin" },
    { time: "14:02", target: "cam55:depth", message: "rs-stream@depth failed on node", result: "failed", reason: "no_rtp" },
    { time: "13:58", target: "cam10:color", message: "node checked", result: "ok", action: "node.check", actor: "operator" },
  ],
  fdirEvents: [
    { time: "14:22", binding: "cam55:color", domain: "PRODUCER", signal: "rtp_age=25000ms", action: "restart_stream", result: "ok", suppressed: "no", reason: "—" },
    { time: "14:24", binding: "cam55:depth", domain: "PRODUCER", signal: "stale", action: "skipped", result: "—", suppressed: "yes", reason: "maintenance" },
  ],
  security: [
    { key: "Admin auth", value: "configured", status: "ok" },
    { key: "Viewer auth", value: "missing", status: "warn" },
    { key: "Node tokens", value: "present", status: "ok" },
    { key: "Host keys", value: "pinned", status: "ok" },
    { key: "Secrets perms", value: "ok (600)", status: "ok" },
  ],
};
