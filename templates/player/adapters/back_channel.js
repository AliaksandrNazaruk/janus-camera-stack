// back_channel.js — Universal pub/sub SDK on top of the Janus textroom transport.
//
// Sprint AB1 frontend: extract joystick-specific code from janus_textroom_adapter
// into a generic BackChannel API. Applications publish payloads to topics; server
// routes them to configured sinks based on topic name (see textroom_relay.py).
//
// Usage:
//
//   const channel = new BackChannel(textroomAdapter, logger);
//
//   // Send (publish) — payload is automatically wrapped with a topic field
//   channel.publish('joystick', {axes: [...], buttons: [...]});
//   channel.publish('voice', {pcm: base64String});
//   channel.publish('chat', {text: 'hello'});
//
//   // Receive (subscribe) — callbacks fire when the server pushes a message with this topic
//   // (requires a server-side textroom join — currently L4 sends text messages
//   // back through the textroom plugin as well)
//   channel.subscribe('robot-status', (payload) => updateUI(payload));
//
// Transport remains Janus textroom plugin. SDK adds:
//   1. Topic envelope ({topic, ...payload}) wrapping
//   2. Inbound dispatch by topic field
//   3. Ping/pong helper (system-level transport health, not application)

(function () {
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  class BackChannel {
    /**
     * @param {any} textroomAdapter  Object with a .sendMessage(text:string) method
     * @param {any} logger           Optional structured logger
     */
    constructor(textroomAdapter, logger) {
      if (!textroomAdapter) throw new Error('BackChannel requires textroomAdapter');
      this.transport = textroomAdapter;
      this.log = logger || console;
      /** @type {Map<string, Set<Function>>} */
      this._subscribers = new Map();
      this._installed = false;
      this._installHook();
    }

    // ── Outbound: publish ─────────────────────────────────────────────

    /**
     * Send a message under the given topic. Payload is wrapped in {topic, ...payload}.
     * Returns a boolean indicating whether the transport accepted it (queue depth, ready state).
     *
     * @param {string} topic  Application-defined identifier (matches server config)
     * @param {object} payload  Arbitrary JSON-serializable object
     */
    publish(topic, payload) {
      if (typeof topic !== 'string' || !topic) {
        this.log.warn && this.log.warn('back_channel: topic must be non-empty string');
        return false;
      }
      if (payload && typeof payload === 'object' && payload.topic && payload.topic !== topic) {
        this.log.warn && this.log.warn('back_channel: payload.topic differs from publish topic; overriding');
      }
      const envelope = Object.assign({ topic }, payload || {});
      try {
        return this.transport.sendMessage(JSON.stringify(envelope));
      } catch (e) {
        this.log.warn && this.log.warn('back_channel: publish failed', e);
        return false;
      }
    }

    // ── Inbound: subscribe ────────────────────────────────────────────

    /**
     * Register a callback for messages received on a given topic.
     * Returns an unsubscribe function.
     *
     * @param {string} topic
     * @param {(payload: object) => void} callback
     */
    subscribe(topic, callback) {
      if (typeof topic !== 'string' || !topic) {
        throw new Error('BackChannel.subscribe requires topic string');
      }
      if (typeof callback !== 'function') {
        throw new Error('BackChannel.subscribe requires callback function');
      }
      let set = this._subscribers.get(topic);
      if (!set) {
        set = new Set();
        this._subscribers.set(topic, set);
      }
      set.add(callback);
      return () => {
        const s = this._subscribers.get(topic);
        if (s) s.delete(callback);
      };
    }

    // ── Internal: dispatch inbound textroom messages by topic ─────────

    _installHook() {
      // Wrap transport's ondata if available — we intercept inbound, dispatch,
      // then fall back to original handler (preserves existing logging).
      const t = this.transport;
      if (typeof t.onMessage === 'function') {
        // If transport already exposes addListener pattern, use it
        t.onMessage((raw) => this._handleInbound(raw));
        this._installed = true;
        return;
      }
      // Otherwise, expose `_handleInbound` for transport to call directly.
      // Joystick_service migration will set `textroomAdapter._inboundDispatcher
      // = backChannel._handleInbound.bind(backChannel)`.
      t._inboundDispatcher = (raw) => this._handleInbound(raw);
      this._installed = true;
    }

    _handleInbound(raw) {
      let parsed;
      try {
        if (typeof raw === 'string') {
          parsed = JSON.parse(raw);
        } else if (raw && typeof raw === 'object') {
          parsed = raw;
        } else {
          return;
        }
      } catch (e) {
        return; // not JSON, ignore
      }

      // Textroom may envelope: {textroom: 'message', from, text: '<inner json>'}
      // Extract inner payload if needed.
      if (parsed && parsed.textroom === 'message' && typeof parsed.text === 'string') {
        try { parsed = JSON.parse(parsed.text); } catch (e) { return; }
      }
      if (!parsed || typeof parsed !== 'object') return;
      const topic = parsed.topic;
      if (typeof topic !== 'string' || !topic) return;

      const subs = this._subscribers.get(topic);
      if (!subs || !subs.size) return;
      subs.forEach((cb) => {
        try { cb(parsed); }
        catch (e) { this.log.warn && this.log.warn('back_channel subscriber error', topic, e); }
      });
    }

    // ── Utility ───────────────────────────────────────────────────────

    /**
     * Is the transport ready for publish? Useful before sending high-rate messages.
     */
    isReady() {
      return !!(this.transport && this.transport.ready);
    }

    /**
     * List currently subscribed topics (debug).
     */
    subscribedTopics() {
      return Array.from(this._subscribers.keys());
    }
  }

  AP.BackChannel = BackChannel;
})();
