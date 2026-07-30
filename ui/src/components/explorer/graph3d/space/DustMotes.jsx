import { useMemo, useRef, useEffect } from 'react';
import { shaderMaterial } from '@react-three/drei';
import { extend, useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { mulberry32 } from '../hash';

// Near-camera dust, purely for parallax.
//
// The motes live in a box that is wrapped around the camera IN THE VERTEX
// SHADER, so there is no CPU work and they can never run out however far you
// fly. depthTest stays on so the galaxy correctly occludes them.

const BOX = 1800;

const DustMoteMaterial = shaderMaterial(
  { uTime: 0, uCamPos: new THREE.Vector3(), uBox: BOX, uPixelScale: 500, uDrift: new THREE.Vector3(6, 2, -4) },
  /* glsl */ `
    attribute float aSize;
    uniform float uTime;
    uniform vec3 uCamPos;
    uniform float uBox;
    uniform float uPixelScale;
    uniform vec3 uDrift;

    void main() {
      vec3 p = position + uTime * uDrift;
      // Toroidal wrap around the camera.
      vec3 rel = p - uCamPos;
      float h = uBox * 0.5;
      rel = mod(rel + h, uBox) - h;
      p = uCamPos + rel;

      vec4 mv = modelViewMatrix * vec4(p, 1.0);
      gl_PointSize = clamp(aSize * uPixelScale / max(1.0, -mv.z), 0.7, 3.5);
      gl_Position = projectionMatrix * mv;
    }
  `,
  /* glsl */ `
    precision highp float;
    void main() {
      float d = length(gl_PointCoord - 0.5) * 2.0;
      float a = pow(max(0.0, 1.0 - d), 3.0) * 0.06;
      if (a < 0.002) discard;
      gl_FragColor = vec4(0.62, 0.72, 1.0, a);
    }
  `
);

extend({ DustMoteMaterial });

export function DustMotes({ count = 1200 }) {
  const pointsRef = useRef(null);
  const matRef = useRef(null);

  const data = useMemo(() => {
    const rnd = mulberry32(0xd05700);
    const positions = new Float32Array(count * 3);
    const sizes = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      positions[i * 3] = (rnd() - 0.5) * BOX;
      positions[i * 3 + 1] = (rnd() - 0.5) * BOX;
      positions[i * 3 + 2] = (rnd() - 0.5) * BOX;
      sizes[i] = (0.5 + rnd() * 1.2) * 2.0;
    }
    return { positions, sizes };
  }, [count]);

  useEffect(() => {
    const p = pointsRef.current;
    if (p) p.raycast = () => {};
  }, []);

  useFrame((state) => {
    const m = matRef.current;
    if (!m) return;
    m.uTime = state.clock.elapsedTime;
    m.uCamPos.copy(state.camera.position);
    const fov = (state.camera.fov * Math.PI) / 180;
    m.uPixelScale = state.size.height / (2 * Math.tan(fov / 2));
  });

  return (
    <points ref={pointsRef} frustumCulled={false}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[data.positions, 3]} />
        <bufferAttribute attach="attributes-aSize" args={[data.sizes, 1]} />
      </bufferGeometry>
      <dustMoteMaterial
        ref={matRef}
        attach="material"
        transparent
        depthWrite={false}
        depthTest
        blending={THREE.AdditiveBlending}
        toneMapped={false}
      />
    </points>
  );
}
