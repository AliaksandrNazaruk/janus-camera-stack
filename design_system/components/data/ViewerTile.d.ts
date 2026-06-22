import * as React from "react";

/**
 * One tile of the Viewer Wall — 16:9 video surface with a status overlay,
 * optional FDIR-event badge, pin toggle and quick-action footer.
 *
 * @startingPoint section="Data" subtitle="Viewer Wall video tile with status overlay" viewport="420x320"
 */
export interface ViewerTileProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Binding id shown in the overlay, e.g. "cam55:color". */
  binding: string;
  status?: string;
  /** rtp_age string, e.g. "90ms". */
  rtpAge?: string;
  /** Last FDIR event label, shown as a badge over the video. */
  fdirEvent?: string;
  pinned?: boolean;
  /** Real media element (e.g. a <video>); a placeholder renders if omitted. */
  media?: React.ReactNode;
  onRestart?: () => void;
  onDiagnose?: () => void;
  onPin?: () => void;
  onFullscreen?: () => void;
}

export function ViewerTile(props: ViewerTileProps): JSX.Element;
