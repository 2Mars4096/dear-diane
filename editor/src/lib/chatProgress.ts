type ProgressAckLikeEvent = {
  type: string;
  detected_mode?: string;
  phase_label?: string;
  content?: string;
};

export function progressAckText(event: ProgressAckLikeEvent): string | null {
  if (event.type !== "chat_complete" || event.detected_mode !== "progress_ack") {
    return null;
  }

  const phaseLabel =
    typeof event.phase_label === "string" ? event.phase_label.trim() : "";
  if (phaseLabel) {
    return phaseLabel;
  }

  const content = typeof event.content === "string" ? event.content.trim() : "";
  return content || null;
}
