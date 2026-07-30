import { useRef, useEffect } from 'react';
import { useThree, useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { computeCameraTarget } from './sceneUtils';

const UP = new THREE.Vector3(0, 1, 0);

function easeInOutCubic(t) {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

/**
 * Owns the camera: initial 3/4 framing of the galaxy, plus fly-to tweens.
 *
 * Replaces the old CameraAnimator, which had two real bugs at this scale:
 *   - `camera.position.lerp(target, t * 0.08)` asymptotes, so the camera never
 *     actually arrived, it just got progressively closer;
 *   - it called `camera.lookAt()`, which OrbitControls overwrites from
 *     `controls.target` on its next update(). Damping hid the conflict.
 *
 * Positions are resolved through the disc's live world matrix, so a fly-to
 * lands correctly no matter how long the galaxy has been rotating.
 */
export function CameraRig({ target, nodes, discRef, controlsRef, R }) {
  const { camera } = useThree();
  const framed = useRef(false);
  const tween = useRef(null);

  // One-time initial framing. The <Canvas camera> prop is read on mount,
  // before the layout exists, so the real framing has to happen here.
  useEffect(() => {
    if (framed.current || !R) return;
    framed.current = true;
    camera.position.set(0, R * 1.5, R * 3.2);
    camera.updateProjectionMatrix();
    if (controlsRef.current) {
      controlsRef.current.target.set(0, 0, 0);
      controlsRef.current.maxDistance = R * 8;
      controlsRef.current.update();
    }
  }, [R, camera, controlsRef]);

  useEffect(() => {
    if (!target?.ids || !nodes) return;
    const controls = controlsRef.current;
    if (!controls) return;

    const matrix = discRef.current?.matrixWorld ?? null;
    const resolved = computeCameraTarget(nodes, target.ids, matrix);
    if (!resolved) return;

    const from = camera.position.clone();
    const dist = from.distanceTo(resolved.position);
    tween.current = {
      from,
      to: resolved.position,
      fromLook: controls.target.clone(),
      toLook: resolved.lookAt,
      dist,
      start: performance.now(),
      // Long inter-arm hops get more time, but never a crawl.
      duration: Math.min(2200, Math.max(700, 600 + dist * 0.12)),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  useFrame(() => {
    // Dev-only handle so the scene can be probed from the console (projecting
    // a node to screen coords, checking the disc transform). Stripped in prod.
    if (import.meta.env.DEV) {
      window.__rig = { camera, disc: discRef.current, controls: controlsRef.current };
    }
    const tw = tween.current;
    const controls = controlsRef.current;
    if (!tw || !controls) return;

    const raw = Math.min(1, (performance.now() - tw.start) / tw.duration);
    const t = easeInOutCubic(raw);

    camera.position.lerpVectors(tw.from, tw.to, t);
    // Arc the path so long hops read as flight rather than a dolly. sin(pi*t)
    // returns to zero at both ends, so the arrival is still exact.
    camera.position.addScaledVector(UP, Math.sin(Math.PI * t) * tw.dist * 0.08);
    controls.target.lerpVectors(tw.fromLook, tw.toLook, t);
    controls.update();

    if (raw >= 1) tween.current = null;
  });

  return null;
}
