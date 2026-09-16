export type ScrollMetrics = {
  top: number; left: number; height: number; width: number;
  viewportHeight: number; viewportWidth: number;
};
export type ScrollAnchor = {id: string; top: number; height: number};

const clamp = (value: number, maximum: number) => Math.max(0, Math.min(value, Math.max(0, maximum)));

/** Map document locations, rather than text lengths, across a saved redaction. */
export function linkedScrollPosition(source: ScrollMetrics, target: ScrollMetrics,
  sourceAnchors: ScrollAnchor[] = [], targetAnchors: ScrollAnchor[] = []) {
  const sourceMax = Math.max(0, source.height - source.viewportHeight);
  const targetMax = Math.max(0, target.height - target.viewportHeight);
  let top = sourceMax ? source.top / sourceMax * targetMax : 0;
  const lookup = new Map(targetAnchors.map(anchor => [anchor.id, anchor]));
  const shared = sourceAnchors.filter(anchor => lookup.has(anchor.id)).sort((a, b) => a.top - b.top);
  const line = source.top + 16;
  const anchor = shared.findLast(item => item.top <= line);
  if (anchor && source.top > 1 && source.top < sourceMax - 1) {
    const peer = lookup.get(anchor.id)!;
    const next = shared.find(item => item.top > anchor.top + 1);
    const peerNext = next ? lookup.get(next.id) : undefined;
    // Interpolate the paragraph and its following space. Wrapped or shortened
    // text can change heights; paragraph identities stay stable after masking.
    const sourceSpan = next ? next.top - anchor.top : anchor.height;
    const targetSpan = peerNext && peerNext.top > peer.top ? peerNext.top - peer.top : peer.height;
    top = peer.top + (line - anchor.top) / Math.max(1, sourceSpan) * targetSpan - 16;
  }
  const horizontalMax = Math.max(0, source.width - source.viewportWidth);
  const targetHorizontalMax = Math.max(0, target.width - target.viewportWidth);
  return {
    top: clamp(top, targetMax),
    left: horizontalMax ? clamp(source.left / horizontalMax * targetHorizontalMax, targetHorizontalMax) : 0,
  };
}
