import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { getPuffTexture } from './noiseTexture';
import { GAS_BLEND } from './puffMaterial';
import { radiusForAreaFraction, spiralTheta } from './galaxyLayout';
import { mulberry32, gauss, clamp } from '../hash';
import { HII_COLOR, BULGE_TINT } from '../palette';

// Star-forming gas along the arms, plus the bulge and bar glow.
//
// The puffs are placed with the SAME spiral function the stars use, just a
// different seed — that is what makes the gas trace the arms instead of
// sitting over them as a shapeless blob. Each folder's gas carries its own
// hue, so an arm segment is identifiable by its glow from a wide shot.

const MAX_PUFFS = 160;

export function Nebulae({ galaxy, quality = 1 }) {
  const matRef = useRef(null);
  const texture = getPuffTexture();

  const data = useMemo(() => {
    if (!galaxy) return null;
    const { folders, arms, R, rcore } = galaxy;

    const centres = [];
    const scales = [];
    const rolls = [];
    const spins = [];
    const colors = [];
    const alphas = [];
    const uvOffsets = [];
    const c = new THREE.Color();
    const hii = new THREE.Color(HII_COLOR);

    const push = (x, y, z, scale, color, alpha, rnd) => {
      centres.push(x, y, z);
      scales.push(scale);
      rolls.push(rnd() * Math.PI * 2);
      spins.push((rnd() - 0.5) * 0.03);
      colors.push(color.r, color.g, color.b);
      alphas.push(alpha);
      uvOffsets.push(rnd(), rnd());
    };

    // --- HII regions along each folder's arm segment ---
    const budget = Math.max(
      1,
      Math.round((MAX_PUFFS * quality) / Math.max(1, folders.length))
    );

    for (const f of folders) {
      const arm = arms[f.armIndex];
      if (!arm) continue;
      const n = clamp(Math.round((f.count / 18) * quality), 3, budget);
      const rnd = mulberry32(0xa11a5 + f.index * 7919);

      // Folder hue mixed into H-alpha pink: recognisably gas, but tinted so
      // each segment stays distinguishable.
      c.copy(hii).lerp(new THREE.Color(f.hue), 0.55);

      for (let i = 0; i < n; i++) {
        const u = f.u0 + ((i + 0.5) / n) * (f.u1 - f.u0);
        const r = radiusForAreaFraction(rcore, R, u);
        const theta = spiralTheta(rcore, r);
        const angle = arm.base + theta + gauss(rnd) * 0.10;
        const rr = r + gauss(rnd) * (r * 0.06);

        push(
          rr * Math.cos(angle),
          gauss(rnd) * R * 0.018,
          rr * Math.sin(angle),
          R * (0.040 + rnd() * 0.050) * (arm.major ? 1 : 0.8),
          c,
          // Very low per-puff alpha. These are additive and they overlap
          // heavily along an arm, so anything higher stacks into a solid
          // blown-out ring instead of reading as gas.
          (0.030 + rnd() * 0.025) * (arm.major ? 1 : 0.75),
          rnd
        );
      }
    }

    // --- Bulge + bar glow ---
    // Two quads that make the core blow out into a bloom flare. This is the
    // "bright core" of the metaphor and it is essentially free.
    const brnd = mulberry32(0xb0165);
    const warm = new THREE.Color(BULGE_TINT);
    push(0, 0, 0, R * 0.26, warm, 0.16, brnd);
    push(0, 0, 0, R * 0.15, warm, 0.24, brnd);

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
      renderOrder={1}
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
      <puffMaterial ref={matRef} attach="material" uMap={texture} {...GAS_BLEND} />
    </instancedMesh>
  );
}
