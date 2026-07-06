import { useCallback, useRef } from 'react';

export function ResizeHandle({ side, onResize }) {
  const dragging = useRef(false);
  const lastX = useRef(0);

  const onPointerDown = useCallback((e) => {
    dragging.current = true;
    lastX.current = e.clientX;
    e.target.setPointerCapture(e.pointerId);
  }, []);

  const onPointerMove = useCallback(
    (e) => {
      if (!dragging.current) return;
      const delta = e.clientX - lastX.current;
      lastX.current = e.clientX;
      // Left panel: drag right = bigger (positive delta).
      // Right panel: drag left = bigger (negative delta -> invert).
      onResize(side === 'left' ? delta : -delta);
    },
    [onResize, side]
  );

  const onPointerUp = useCallback(() => {
    dragging.current = false;
  }, []);

  return (
    <div
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      className="w-1 cursor-col-resize hover:bg-primary/30 active:bg-primary/50 transition-colors shrink-0"
    />
  );
}
