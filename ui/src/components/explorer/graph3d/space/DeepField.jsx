import { useMemo, useRef, useEffect } from 'react';
import { shaderMaterial } from '@react-three/drei';
import { extend, useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { mulberry32, gauss } from '../hash';

// Distant galaxies.
//
// Deliberately NOT a milky-way band across the sky: the codebase itself is the
// Milky Way here, and a second galaxy painted on the backdrop would compete
// with it. Instead the sky reads as intergalactic — faint, redshifted smudges
// that place the viewer outside the galaxy looking in, for ~40 quads.

const GALAXY_COUNT = 40;

const DeepGalaxyMaterial = shaderMaterial(
  { uTime: 0 },
  /* glsl */ `
    attribute vec3 aCentre;
    attribute float aScale;
    attribute float aRoll;
    attribute float aSquash;
    attribute vec3 aColor;
    attribute float aBright;

    varying vec2 vUv;
    varying vec3 vColor;
    varying float vBright;

    void main() {
      vUv = uv;
      vColor = aColor;
      vBright = aBright;

      vec4 mv = modelViewMatrix * vec4(aCentre, 1.0);
      // Billboard, rolled and squashed so some read edge-on and some face-on.
      float c = cos(aRoll), s = sin(aRoll);
      vec2 p = vec2(position.x, position.y * aSquash) * aScale;
      mv.xy += vec2(p.x * c - p.y * s, p.x * s + p.y * c);
      gl_Position = projectionMatrix * mv;
    }
  `,
  /* glsl */ `
    precision highp float;
    varying vec2 vUv;
    varying vec3 vColor;
    varying float vBright;

    void main() {
      vec2 p = vUv * 2.0 - 1.0;
      float d = length(p);
      if (d > 1.0) discard;
      // Bright nucleus fading into a soft disc.
      float nucleus = pow(max(0.0, 1.0 - d), 6.0);
      float disc = pow(max(0.0, 1.0 - d), 1.6) * 0.4;
      float a = (nucleus + disc) * vBright;
      if (a < 0.003) discard;
      gl_FragColor = vec4(vColor, a);
    }
  `
);

extend({ DeepGalaxyMaterial });

export function DeepField({ count = GALAXY_COUNT }) {
  const meshRef = useRef(null);

  const data = useMemo(() => {
    const rnd = mulberry32(0xdeadbee5);
    const centres = new Float32Array(count * 3);
    const scales = new Float32Array(count);
    const rolls = new Float32Array(count);
    const squashes = new Float32Array(count);
    const colors = new Float32Array(count * 3);
    const brights = new Float32Array(count);
    const c = new THREE.Color();

    for (let i = 0; i < count; i++) {
      const u = rnd() * 2 - 1;
      const th = rnd() * Math.PI * 2;
      const s = Math.sqrt(Math.max(0, 1 - u * u));
      const r = 30000 + rnd() * 18000;
      centres[i * 3] = s * Math.cos(th) * r;
      centres[i * 3 + 1] = u * r;
      centres[i * 3 + 2] = s * Math.sin(th) * r;

      scales[i] = (200 + Math.abs(gauss(rnd)) * 420) * (r / 40000);
      rolls[i] = rnd() * Math.PI;
      // Squash gives edge-on spirals alongside face-on ellipticals.
      squashes[i] = 0.12 + rnd() * 0.88;

      // Distant galaxies are redshifted; a few nearer ones stay neutral.
      c.set(rnd() < 0.7 ? '#c98b7a' : '#d9cfc4');
      colors[i * 3] = c.r;
      colors[i * 3 + 1] = c.g;
      colors[i * 3 + 2] = c.b;

      // Kept under the bloom threshold so they stay smudges, not lamps.
      brights[i] = 0.10 + rnd() * 0.18;
    }
    return { centres, scales, rolls, squashes, colors, brights };
  }, [count]);

  useEffect(() => {
    const m = meshRef.current;
    if (m) m.raycast = () => {};
  }, []);

  useFrame((state) => {
    // Ride the camera: these are effectively at infinity.
    if (meshRef.current) meshRef.current.position.copy(state.camera.position);
  });

  return (
    <instancedMesh
      ref={meshRef}
      args={[undefined, undefined, count]}
      frustumCulled={false}
      renderOrder={-8}
    >
      <planeGeometry args={[1, 1]}>
        <instancedBufferAttribute attach="attributes-aCentre" args={[data.centres, 3]} />
        <instancedBufferAttribute attach="attributes-aScale" args={[data.scales, 1]} />
        <instancedBufferAttribute attach="attributes-aRoll" args={[data.rolls, 1]} />
        <instancedBufferAttribute attach="attributes-aSquash" args={[data.squashes, 1]} />
        <instancedBufferAttribute attach="attributes-aColor" args={[data.colors, 3]} />
        <instancedBufferAttribute attach="attributes-aBright" args={[data.brights, 1]} />
      </planeGeometry>
      <deepGalaxyMaterial
        attach="material"
        transparent
        depthWrite={false}
        depthTest={false}
        blending={THREE.AdditiveBlending}
        toneMapped={false}
      />
    </instancedMesh>
  );
}
