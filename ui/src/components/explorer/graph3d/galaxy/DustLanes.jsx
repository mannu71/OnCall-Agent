import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { getPuffTexture } from './noiseTexture';
import { DUST_BLEND } from './puffMaterial';
import { radiusForAreaFraction, spiralTheta } from './galaxyLayout';
import { mulberry32, gauss } from '../hash';
import { DUST_COLOR } from '../palette';

// Dark dust lanes hugging the inner edge of each arm.
//
// Extinction sells "Milky Way" more than any other single feature — a spiral
// without dark lanes reads as a diagram.
//
// One deliberately unphysical choice: these are drawn AFTER the gas but BEFORE
// the stars (renderOrder 2, between Nebulae at 1 and StarPoints at 3). The
// stars are additive with depthWrite off, so there is no depth for a
// multiply-blended lane to test against, and a "correct" ordering would dim
// stars in FRONT of the dust as well as behind it. Carving the gas and leaving
// the stars alone is both cheaper and better looking.

// Fraction of the arm's local width to offset inward.
const INNER_OFFSET = -1.4;

export function DustLanes({ galaxy, quality = 1 }) {
  const matRef = useRef(null);
  const texture = getPuffTexture();

  const data = useMemo(() => {
    if (!galaxy) return null;
    const { arms, R, rcore } = galaxy;

    const centres = [];
    const scales = [];
    const rolls = [];
    const spins = [];
    const colors = [];
    const alphas = [];
    const uvOffsets = [];
    const c = new THREE.Color(DUST_COLOR);

    for (const arm of arms) {
      const n = Math.round((arm.major ? 26 : 16) * quality);
      const rnd = mulberry32(0xd1157 + arm.index * 104729);

      for (let i = 0; i < n; i++) {
        const u = (i + 0.5) / n;
        const r = radiusForAreaFraction(rcore, R, u);
        const theta = spiralTheta(rcore, r);
        const angle = arm.base + theta;

        // Sit just inside the arm ridge, where real dust lanes form.
        const width = r * 0.055 + rcore * 0.02;
        const rr = r + width * INNER_OFFSET + gauss(rnd) * width * 0.3;

        centres.push(rr * Math.cos(angle), gauss(rnd) * R * 0.012, rr * Math.sin(angle));
        scales.push(R * (0.05 + rnd() * 0.055));
        rolls.push(rnd() * Math.PI * 2);
        spins.push((rnd() - 0.5) * 0.015);
        colors.push(c.r, c.g, c.b);
        alphas.push((0.55 + rnd() * 0.30) * (arm.major ? 1 : 0.7));
        uvOffsets.push(rnd(), rnd());
      }
    }

    return {
      centres: new Float32Array(centres),
      scales: new Float32Array(scales),
      rolls: new Float32Array(rolls),
      spins: new Float32Array(spins),
      colors: new Float32Array(colors),
      alphas: new Float32Array(alphas),
      uvOffsets: new Float32Array(uvOffsets),
      count: scales.length,
    };
  }, [galaxy, quality]);

  useFrame((state) => {
    const m = matRef.current;
    if (m) m.uTime = state.clock.elapsedTime;
  });

  if (!data || data.count === 0) return null;

  return (
    <instancedMesh
      args={[undefined, undefined, data.count]}
      frustumCulled={false}
      renderOrder={2}
      raycast={null}
    >
      <planeGeometry args={[1, 1]}>
        <instancedBufferAttribute attach="attributes-aCentre" args={[data.centres, 3]} />
        <instancedBufferAttribute attach="attributes-aScale" args={[data.scales, 1]} />
        <instancedBufferAttribute attach="attributes-aRoll" args={[data.rolls, 1]} />
        <instancedBufferAttribute attach="attributes-aSpin" args={[data.spins, 1]} />
        <instancedBufferAttribute attach="attributes-aColor" args={[data.colors, 3]} />
        <instancedBufferAttribute attach="attributes-aAlpha" args={[data.alphas, 1]} />
        <instancedBufferAttribute attach="attributes-aUvOffset" args={[data.uvOffsets, 2]} />
      </planeGeometry>
      <puffMaterial ref={matRef} attach="material" uMap={texture} {...DUST_BLEND} />
    </instancedMesh>
  );
}
