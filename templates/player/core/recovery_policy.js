(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  const RecoveryAction = AP.Core.RecoveryAction;
  const RecoverySeverity = AP.Core.RecoverySeverity;

  /**
   * Decide next recovery action based on attempt count and policy.
   * Pure function: no I/O. cfg must be immutable (read-only) for deterministic behavior.
   *
   * @param {number} attemptOneBased
   * @param {number} severity one of RecoverySeverity
   * @param {{maxWatchRetries:number, maxReattachRetries:number}} cfg - MUST be immutable
   * @returns {number} RecoveryAction
   */
  function decideRecoveryAction(attemptOneBased, severity, cfg){
    const attempt = Math.max(1, Math.trunc(attemptOneBased || 1));
    const sev = Math.max(RecoverySeverity.SOFT, Math.min(RecoverySeverity.HARD, Math.trunc(severity || RecoverySeverity.SOFT)));

    // Phase 2 — on HARD severity (e.g., ICE disconnect/failed) try ICE_RESTART first
    // before full RECREATE_SESSION. ICE restart preserves the media stream (no black
    // screen) and handles mobile 4G→5G handoff in ~1-2sec vs 5-10sec full recreate.
    // If ICE_RESTART was tried + still HARD, escalate to RECREATE_SESSION.
    if (sev >= RecoverySeverity.HARD) {
      const iceRestartMax = Math.max(0, Math.trunc(cfg?.maxIceRestartAttempts ?? 2));
      if (attempt <= iceRestartMax) return RecoveryAction.ICE_RESTART;
      return RecoveryAction.RECREATE_SESSION;
    }

    const watchMax = Math.max(0, Math.trunc(cfg?.maxWatchRetries ?? 3));
    const reattachMax = Math.max(0, Math.trunc(cfg?.maxReattachRetries ?? 2));

    if (attempt <= watchMax) return RecoveryAction.SOFT_RESTART;
    if (attempt <= watchMax + reattachMax) return RecoveryAction.REATTACH_PLUGIN;
    return RecoveryAction.RECREATE_SESSION;
  }

  AP.Core.decideRecoveryAction = decideRecoveryAction;
})();
