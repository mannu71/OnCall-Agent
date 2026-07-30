import { useMemo, useRef, useEffect } from 'react';
import { shaderMaterial } from '@react-three/drei';
import { extend, useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { mulberry32 } from '../hash';

// Background stars.
//
// Two shells: a far one parented to the camera position so it behaves as a
// sky, and a nearer one that parallaxes against it as you orbit. Sprites are
// computed in the fragment shader — no texture, no memory.
//
// These are decorative and explicitly NOT graph nodes. Every star inside the
// galaxy disc is a real symbol; the fiction lives out here, thousands of units
// away, where it cannot be mistaken for data.

// Real stellar distribution is dominated by cool dwarfs. Weighting the draw
// this way is what keeps the sky from looking like uniform white noise.
const TEMPERATURES = [
  ['#ffb490', 0.42], // M/K red-orange dwarfs
  ['#ffe6bd', 0.30], // G sun-like
  ['#fff6ee', 0.18], // F/A white
  ['#cfe0ff', 0.08], // B blue-white
  ['#a9c6ff', 0.02], // O blue giant
];

const BackgroundStarMaterial = shaderMaterial(
  { uTime: 0, uPixelScale: 500, uOpacity: 1 },
  /* glsl */ `
    attribute float aSize;
    attribute float aPhase;
    attribute float aSpeed;
    attribute float aTwinkle;
    attribute float aBright;

    uniform float uTime;
    uniform float uPixelScale;

    varying vec3 vColor;
    varying float vBright;

    void main() {
      vColor = color;
      float tw = 1.0 + aTwinkle * sin(uTime * aSpeed + aPhase);
      vBright = aBright * tw;

      vec4 mv = modelViewMatrix * vec4(position, 1.0);
      // Perspective-correct point size, clamped so distant shells never
      // collapse to sub-pixel (invisible) or bloat into blobs.
      gl_PointSize = clamp(aSize * tw * uPixelScale / max(1.0, -mv.z), 0.8, 7.0);
      gl_Position = projectionMatrix * mv;
    }
  `,
  /* glsl */ `
    precision highp float;
    uniform float uOpacity;
    varying vec3 vColor;
    varying float vBright;

    void main() {
      float d = length(gl_PointCoord - 0.5) * 2.0;
      float core = pow(max(0.0, 1.0 - d), 9.0);
      float halo = pow(max(0.0, 1.0 - d), 2.0) * 0.30;
      float a = (core + halo) * vBright * uOpacity;
      if (a < 0.004) discard;
      gl_FragColor = vec4(vColor, a);
    }
  `
);

extend({ BackgroundStarMaterial });

function buildShell(count, rMin, rMax, seed, maxBright) {
  const rnd = mulberry32(seed);
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  const sizes = new Float32Array(count);
  const phases = new Float32Array(count);
  const speeds = new Float32Array(count);
  const twinkles = new Float32Array(count);
  const brights = new Float32Array(count);
  const c = new THREE.Color();

  for (let i = 0; i < count; i++) {
    // Uniform on the sphere.
    const u = rnd() * 2 - 1;
    const th = rnd() * Math.PI * 2;
    const s = Math.sqrt(Math.max(0, 1 - u * u));
    const r = rMin + rnd() * (rMax - rMin);
    positions[i * 3] = s * Math.cos(th) * r;
    positions[i * 3 + 1] = u * r;
    positions[i * 3 + 2] = s * Math.sin(th) * r;

    let pick = rnd();
    let hex = TEMPERATURES[0][0];
    for (const [h, w] of TEMPERATURES) {
      if (pick < w) { hex = h; break; }
      pick -= w;
    }
    c.set(hex);
    colors[i * 3] = c.r;
    colors[i * 3 + 1] = c.g;
    colors[i * 3 + 2] = c.b;

    // Scaled by shell radius so both shells subtend the same apparent size;
    // the coefficient is tuned so a typical star lands around 2px rather than
    // under the 0.8px floor, where it would be invisible.
    sizes[i] = (0.6 + rnd() * 1.8) * r * 0.0032;
    phases[i] = rnd() * Math.PI * 2;
    speeds[i] = 0.3 + rnd() * 1.6;
    twinkles[i] = rnd() * 0.35;
    // Capped well under the bloom threshold: an unclamped sky smears into a
    // uniform white haze the moment bloom is enabled.
    brights[i] = 0.18 + rnd() * maxBright;
  }
  return { positions, colors, sizes, phases, speeds, twinkles, brights, count };
}

function Shell({ data, cameraLocked, depthTest, renderOrder, opacity }) {
  const pointsRef = useRef(null);
  const matRef = useRef(null);

  // Background stars must never intercept a click meant for a code symbol.
  useEffect(() => {
    const p = pointsRef.current;
    if (p) p.raycast = () => {};
  }, []);

  useFrame((state) => {
    const m = matRef.current;
    if (m) {
      m.uTime = state.clock.elapsedTime;
      const fov = (state.camera.fov * Math.PI) / 180;
      m.uPixelScale = state.size.height / (2 * Math.tan(fov / 2));
    }
    // The far shell rides the camera so it reads as a sky at infinity — it
    // still rotates with the view because it lives in world space.
    if (cameraLocked && pointsRef.current) {
      pointsRef.current.position.copy(state.camera.position);
    }
  });

  return (
    <points ref={pointsRef} frustumCulled={false} renderOrder={renderOrder}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[data.positions, 3]} />
        <bufferAttribute attach="attributes-color" args={[data.colors, 3]} />
        <bufferAttribute attach="attributes-aSize" args={[data.sizes, 1]} />
        <bufferAttribute attach="attributes-aPhase" args={[data.phases, 1]} />
        <bufferAttribute attach="attributes-aSpeed" args={[data.speeds, 1]} />
        <bufferAttribute attach="attributes-aTwinkle" args={[data.twinkles, 1]} />
        <bufferAttribute attach="attributes-aBright" args={[data.brights, 1]} />
      </bufferGeometry>
      <backgroundStarMaterial
        ref={matRef}
        attach="material"
        uOpacity={opacity}
        vertexColors
        transparent
        depthWrite={false}
        depthTest={depthTest}
        blending={THREE.AdditiveBlending}
        toneMapped={false}
      />
    </points>
  );
}

export function Starfield({ farCount = 24000, midCount = 8000 }) {
  const far = useMemo(() => buildShell(farCount, 40000, 60000, 0x5eed01, 0.27), [farCount]);
  const mid = useMemo(() => buildShell(midCount, 12000, 20000, 0x5eed02, 0.22), [midCount]);

  return (
    <>
      <Shell data={far} cameraLocked depthTest={false} renderOrder={-10} opacity={1} />
      <Shell data={mid} cameraLocked={false} depthTest={false} renderOrder={-9} opacity={0.9} />
    </>
  );
}
