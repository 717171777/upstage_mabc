'use client';

import {useEffect, useRef} from 'react';
import {linkedScrollPosition, type ScrollAnchor} from '@/lib/document-scroll';

const metrics = (element: HTMLElement) => ({
  top: element.scrollTop, left: element.scrollLeft,
  height: element.scrollHeight, width: element.scrollWidth,
  viewportHeight: element.clientHeight, viewportWidth: element.clientWidth,
});
const anchors = (element: HTMLElement): ScrollAnchor[] => {
  const origin = element.getBoundingClientRect().top + element.clientTop;
  return Array.from(element.querySelectorAll<HTMLElement>('[data-scroll-anchor]')).map(node => {
    const box = node.getBoundingClientRect();
    return {id: node.dataset.scrollAnchor!, top: box.top - origin + element.scrollTop, height: box.height};
  });
};

export function useLinkedDocumentScroll(enabled: boolean, contentKey: string) {
  const originalRef = useRef<HTMLDivElement>(null);
  const copyRef = useRef<HTMLDivElement>(null);
  const lastSource = useRef<'original' | 'copy'>('original');

  useEffect(() => {
    const original = originalRef.current;
    const copy = copyRef.current;
    if (!original || !copy) return;
    let frame = 0;
    let disposed = false;
    const mirrored = new WeakMap<HTMLElement, {top: number; left: number}>();
    const sync = (source: HTMLElement, target: HTMLElement) => {
      if (!enabled || disposed) return;
      const next = linkedScrollPosition(metrics(source), metrics(target), anchors(source), anchors(target));
      if (Math.abs(target.scrollTop - next.top) < 1 && Math.abs(target.scrollLeft - next.left) < 1) return;
      mirrored.set(target, next);
      target.scrollTo({top: next.top, left: next.left, behavior: 'instant'});
      // Browsers round/clamp positions. Suppress the resulting event only if it
      // still matches this write; a user's intervening scroll remains actionable.
      mirrored.set(target, {top: target.scrollTop, left: target.scrollLeft});
    };
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => lastSource.current === 'original' ? sync(original, copy) : sync(copy, original));
    };
    const onScroll = (side: 'original' | 'copy') => () => {
      const source = side === 'original' ? original : copy;
      const expected = mirrored.get(source);
      mirrored.delete(source);
      if (expected && Math.abs(source.scrollTop - expected.top) < 1 && Math.abs(source.scrollLeft - expected.left) < 1) return;
      lastSource.current = side;
      schedule();
    };
    const fromOriginal = onScroll('original');
    const fromCopy = onScroll('copy');
    original.addEventListener('scroll', fromOriginal, {passive: true});
    copy.addEventListener('scroll', fromCopy, {passive: true});
    // The saved preview may finish loading later, or change height after an
    // image/font load or zoom. Re-align it without forcing another fetch.
    const resize = new ResizeObserver(schedule);
    const observeContent = () => {
      resize.disconnect();
      for (const pane of [original, copy]) {
        resize.observe(pane);
        for (const child of pane.children) resize.observe(child);
      }
      schedule();
    };
    const mutation = new MutationObserver(observeContent);
    for (const pane of [original, copy]) mutation.observe(pane, {childList: true, subtree: true});
    observeContent();
    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      original.removeEventListener('scroll', fromOriginal);
      copy.removeEventListener('scroll', fromCopy);
      resize.disconnect();
      mutation.disconnect();
    };
  }, [enabled, contentKey]);

  return {originalRef, copyRef};
}
