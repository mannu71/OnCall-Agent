import { useMemo, useRef, useEffect } from 'react';
import { shaderMaterial } from '@react-three/drei';
import { extend, useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { mulberry32, gauss } from '../hash';
import { spiralTheta } from './galaxyLayout';
import { BAR_ANGLE } from './armAssignment';

// The unresolved stellar population — what actually makes this read as a
// galaxy rather than a network diagram.
//
// WHY THIS EXISTS: a photograph of a spiral galaxy is a smooth continuous
// sheet of light; you do not see individual stars. 2,000 graph nodes can never
// form that surface no matter how they are coloured — they read as dots. Real
// galaxy renders use six figures of particles.
//
// HONESTY: these points are SCENERY, not data. They carry no node identity,
// they are not pickable, and nothing about them encodes the codebase beyond
// tracing the same arms the real nodes occupy. They are the medium; the graph
// nodes are the resolved stars drawn on top of them, brighter and larger. This
// is the same bargain the background starfield already makes.

const DISC_SCALE_LENGTH = 0.30; // x R — exponential surface-brightness falloff
const BULGE_FRACTION = 0.30;
const INTERARM_FRACTION = 0.30;
const ARM_SPREAD = 0.30; // radians

const GalacticFieldMaterial = shaderMaterial(
  { uTime: 0, uPixelScale: 500, uGain: 1 },
  /* glsl */ `
    attribute float aSize;
    attribute float aBright;
    uniform float uPixelScale;
    varying vec3 vColor;
    varying float vBright;

    void main() {
      vColor = color;
      vBright = aBright;
      vec4 mv = modelViewMatrix * vec4(position, 1.0);
      // Kept tiny: these must blend into a continuous sheet, not resolve into
      // countable dots.
      gl_PointSize = clamp(aSize * uPixelScale / max(1.0, -mv.z), 0.9, 3.0);
      gl_Position = projectionMatrix * mv;
    }
  `,
  /* glsl */ `
    precision highp float;
    uniform float uGain;
    varying vec3 vColor;
    varying float vBright;

    void main() {
      float d = length(gl_PointCoord - 0.5) * 2.0;
      float a = pow(max(0.0, 1.0 - d), 2.5) * vBright * uGain;
      if (a < 0.002) discard;
      gl_FragColor = vec4(vColor, a);
    }
  `
);

extend({ GalacticFieldMaterial });

// Rejection-sample an exponential disc: surface density falls as exp(-r/h),
// and the area element contributes the extra factor of r.
function sampleDiscRadius(rnd, R, h) {
  const peak = h * Math.exp(-1); // max of r*exp(-r/h)
  for (let i = 0; i < 40; i++) {
    const r = rnd() * R;
    if (rnd() * peak <= r * Math.exp(-r / h)) return r;
  }
  return rnd() * R;
}

export function GalacticField({ galaxy, count = 90000, gain = 1 }) {
  const pointsRef = useRef(null);
  const matRef = useRef(null);

  const data = useMemo(() => {
    if (!galaxy) return null;
    const { R, rcore, arms } = galaxy;
    const h = R * DISC_SCALE_LENGTH;

    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    const sizes = new Float32Array(count);
    const brights = new Float32Array(count);

    const rnd = mulberry32(0x6a1ac71c);
    const c = new THREE.Color();

    // Young blue stars trace the arms; the bulge is old and yellow-red.
    const armBlue = new THREE.Color('#9fc4ff');
    const discWhite = new THREE.Color('#ffe9cf');
    const bulgeWarm = new THREE.Color('#ffc98a');

    const cosBar = Math.cos(BAR_ANGLE);
    const sinBar = Math.sin(BAR_ANGLE);

    // Arm selection weighted so the two major arms carry more light.
    const armWeights = arms.map((a) => (a.major ? 1.0 : 0.6));
    const armTotal = armWeights.reduce((s, w) => s + w, 0);

    for (let i = 0; i < count; i++) {
      let x, y, z, bright, inArm = false;

      if (rnd() < BULGE_FRACTION) {
        // Bar + bulge: triaxial, dense at centre, aligned to the bar so the
        // core reads as barred rather than round.
        const m = Math.pow(rnd(), 1.8);
        const u = rnd() * 2 - 1;
        const th = rnd() * Math.PI * 2;
        const s = Math.sqrt(Math.max(0, 1 - u * u));
        const lx = s * Math.cos(th) * m * R * 0.36;
        const lz = s * Math.sin(th) * m * R * 0.13;
        x = lx * cosBar - lz * sinBar;
        z = lx * sinBar + lz * cosBar;
        y = u * m * R * 0.085;
        c.copy(bulgeWarm);
        bright = 0.030 + rnd() * 0.030;
      } else {
        const r = Math.max(rcore * 0.25, sampleDiscRadius(rnd, R * 1.05, h));
        let angle;

        if (rnd() < INTERARM_FRACTION) {
          // Smooth inter-arm population — without it the arms look painted on.
          angle = rnd() * Math.PI * 2;
          c.copy(discWhite);
          bright = 0.014 + rnd() * 0.014;
        } else {
          // Same log-spiral the graph nodes use, so the real stars sit inside
          // these arms rather than floating over them.
          let pick = rnd() * armTotal;
          let ai = 0;
          for (let k = 0; k < arms.length; k++) {
            pick -= armWeights[k];
            if (pick <= 0) { ai = k; break; }
          }
          const theta = spiralTheta(rcore, r);
          angle = arms[ai].base + theta + gauss(rnd) * ARM_SPREAD;
          inArm = true;
          c.copy(armBlue).lerp(discWhite, rnd() * 0.7);
          bright = 0.022 + rnd() * 0.026;
        }

        x = r * Math.cos(angle);
        z = r * Math.sin(angle);
        // Thin disc, flaring slightly outward, thicker toward the bulge.
        const thick = R * 0.011 + R * 0.05 * Math.exp(-r / (R * 0.3));
        y = gauss(rnd) * thick;
      }

      positions[i * 3] = x;
      positions[i * 3 + 1] = y;
      positions[i * 3 + 2] = z;
      colors[i * 3] = c.r;
      colors[i * 3 + 1] = c.g;
      colors[i * 3 + 2] = c.b;
      sizes[i] = (inArm ? 1.5 : 1.2) + rnd() * 0.8;
      brights[i] = bright;
    }

    return { positions, colors, sizes, brights };
  }, [galaxy, count]);

  useEffect(() => {
    const p = pointsRef.current;
    if (p) p.raycast = () => {}; // scenery must never intercept a node click
  }, [data]);

  useFrame((state) => {
    const m = matRef.current;
    if (!m) return;
    const fov = (state.camera.fov * Math.PI) / 180;
    m.uPixelScale = state.size.height / (2 * Math.tan(fov / 2));
  });

  if (!data) return null;

  return (
    <points ref={pointsRef} frustumCulled={false} renderOrder={-1}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[data.positions, 3]} />
        <bufferAttribute attach="attributes-color" args={[data.colors, 3]} />
        <bufferAttribute attach="attributes-aSize" args={[data.sizes, 1]} />
        <bufferAttribute attach="attributes-aBright" args={[data.brights, 1]} />
      </bufferGeometry>
      <galacticFieldMaterial
        ref={matRef}
        attach="material"
        uGain={gain}
        vertexColors
        transparent
        depthWrite={false}
        depthTest={false}
        blending={THREE.AdditiveBlending}
        toneMapped={false}
      />
    </points>
  );
}
