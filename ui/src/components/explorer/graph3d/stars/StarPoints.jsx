import { useMemo, useRef, useLayoutEffect } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { STAR_QUAD_SCALE, STAR_MATERIAL_PROPS } from './starMaterial';
import { attachStarRaycast } from './starRaycast';
import { nodeSeed, mulberry32 } from '../hash';

const HIGHLIGHT_SIZE = 0.5;
const DIM_SIZE = 0.2;

/**
 * One instanced billboard per node.
 *
 * Everything static (position, size, spike weight, twinkle phase) is written
 * once in a layout effect. Only colour and dim respond to selection. useFrame
 * writes a single uniform — contrast with the old NodeCloud, which rebuilt all
 * 2000 instance matrices and recomputed the bounding sphere every frame for
 * positions that never move.
 */
export function StarPoints({ nodes, highlightedIds, onHover, onClick }) {
  const meshRef = useRef(null);
  const materialRef = useRef(null);
  // Shared with the raycast so hit testing uses the same screen-space sizing
  // the vertex shader does. Picking is a touch more generous than the render
  // so small stars stay clickable.
  const pickParams = useRef({ pixelScale: 500, minPixels: 7 });

  // Static per-instance attributes.
  const statics = useMemo(() => {
    const n = nodes.length;
    const offsets = new Float32Array(n * 3);
    const sizes = new Float32Array(n);
    const spikes = new Float32Array(n);
    const phases = new Float32Array(n);
    const twSpeeds = new Float32Array(n);
    const radii = new Float32Array(n);

    for (let i = 0; i < n; i++) {
      const node = nodes[i];
      offsets[i * 3] = node.x;
      offsets[i * 3 + 1] = node.y;
      offsets[i * 3 + 2] = node.z;

      const size = node.size ?? 4;
      sizes[i] = size;
      // Only the big hubs get diffraction spikes, so they read as important
      // without needing to be moved.
      spikes[i] = THREE.MathUtils.smoothstep(size, 10, 24);
      radii[i] = size * HIGHLIGHT_SIZE * STAR_QUAD_SCALE * 0.5;

      const rnd = mulberry32(nodeSeed(node));
      phases[i] = rnd() * Math.PI * 2;
      twSpeeds[i] = 0.4 + rnd() * 1.1;
    }
    return { offsets, sizes, spikes, phases, twSpeeds, radii, count: n };
  }, [nodes]);

  // Selection-dependent attributes.
  const dynamics = useMemo(() => {
    const n = nodes.length;
    const colors = new Float32Array(n * 3);
    const dims = new Float32Array(n);
    const c = new THREE.Color();
    const hasHighlight = highlightedIds && highlightedIds.size > 0;

    for (let i = 0; i < n; i++) {
      const node = nodes[i];
      c.set(node.starColor ?? node.color ?? '#ffffff');
      const on = !hasHighlight || highlightedIds.has(node.id);
      if (!on) c.multiplyScalar(0.35);
      colors[i * 3] = c.r;
      colors[i * 3 + 1] = c.g;
      colors[i * 3 + 2] = c.b;
      dims[i] = on ? 1.0 : 0.28;
    }
    return { colors, dims };
  }, [nodes, highlightedIds]);

  // Sizes shrink for de-emphasised stars, matching the old behaviour.
  const scaledSizes = useMemo(() => {
    const hasHighlight = highlightedIds && highlightedIds.size > 0;
    const out = new Float32Array(statics.count);
    for (let i = 0; i < statics.count; i++) {
      const on = !hasHighlight || highlightedIds.has(nodes[i].id);
      out[i] = statics.sizes[i] * (on ? HIGHLIGHT_SIZE : DIM_SIZE);
    }
    return out;
  }, [statics, nodes, highlightedIds]);

  useLayoutEffect(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    // instanceMatrix stays identity — the shader billboards from aOffset.
    mesh.instanceMatrix.needsUpdate = true;
    // Positions never change, so a one-shot bounding sphere is enough and we
    // keep frustum culling on (a real win when zoomed into one arm).
    mesh.geometry.computeBoundingSphere();
    const bs = mesh.geometry.boundingSphere;
    if (bs) {
      let maxR = 0;
      for (let i = 0; i < statics.count; i++) {
        const d = Math.hypot(
          statics.offsets[i * 3],
          statics.offsets[i * 3 + 1],
          statics.offsets[i * 3 + 2]
        );
        if (d > maxR) maxR = d;
      }
      bs.center.set(0, 0, 0);
      bs.radius = maxR * 1.1 + 50;
    }
    attachStarRaycast(mesh, statics.offsets, statics.radii, pickParams.current);
  }, [statics]);

  useFrame((state) => {
    // The only per-frame writes in the whole star pipeline.
    const m = materialRef.current;
    if (!m) return;
    m.uTime = state.clock.elapsedTime;
    // Needed to convert the shader's pixel-size clamp into world units.
    const fov = (state.camera.fov * Math.PI) / 180;
    const pixelScale = state.size.height / (2 * Math.tan(fov / 2));
    m.uPixelScale = pixelScale;
    pickParams.current.pixelScale = pixelScale;
  });

  if (statics.count === 0) return null;

  return (
    <instancedMesh
      ref={meshRef}
      args={[undefined, undefined, statics.count]}
      renderOrder={3}
      onPointerOver={(e) => {
        e.stopPropagation();
        if (e.instanceId !== undefined && e.instanceId < nodes.length) {
          onHover(nodes[e.instanceId]);
        }
      }}
      onPointerOut={() => onHover(null)}
      onClick={(e) => {
        e.stopPropagation();
        if (e.instanceId !== undefined && e.instanceId < nodes.length) {
          onClick(nodes[e.instanceId]);
        }
      }}
    >
      <planeGeometry args={[1, 1]}>
        <instancedBufferAttribute attach="attributes-aOffset" args={[statics.offsets, 3]} />
        <instancedBufferAttribute attach="attributes-aSize" args={[scaledSizes, 1]} />
        <instancedBufferAttribute attach="attributes-aSpike" args={[statics.spikes, 1]} />
        <instancedBufferAttribute attach="attributes-aPhase" args={[statics.phases, 1]} />
        <instancedBufferAttribute attach="attributes-aTwSpeed" args={[statics.twSpeeds, 1]} />
        <instancedBufferAttribute attach="attributes-aColor" args={[dynamics.colors, 3]} />
        <instancedBufferAttribute attach="attributes-aDim" args={[dynamics.dims, 1]} />
      </planeGeometry>
      <starMaterial ref={materialRef} attach="material" {...STAR_MATERIAL_PROPS} />
    </instancedMesh>
  );
}
