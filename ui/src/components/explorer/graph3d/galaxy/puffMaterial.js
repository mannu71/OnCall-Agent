import * as THREE from 'three';
import { shaderMaterial } from '@react-three/drei';
import { extend } from '@react-three/fiber';

// Shared billboard material for gas clouds and dust lanes.
//
// The same shader serves both; only the blend mode differs. Gas is additive
// (it emits), dust is multiplicative (it absorbs).

const vertex = /* glsl */ `
  attribute vec3 aCentre;
  attribute float aScale;
  attribute float aRoll;
  attribute float aSpin;
  attribute vec3 aColor;
  attribute float aAlpha;
  attribute vec2 aUvOffset;

  uniform float uTime;
  uniform float uNear;

  varying vec2 vUv;
  varying vec3 vColor;
  varying float vAlpha;

  void main() {
    vColor = aColor;

    vec4 mv = modelViewMatrix * vec4(aCentre, 1.0);

    float ang = aRoll + uTime * aSpin;
    float c = cos(ang), s = sin(ang);
    vec2 corner = vec2(
      position.x * c - position.y * s,
      position.x * s + position.y * c
    ) * aScale;
    mv.xy += corner;

    // Fade as the camera gets close, so flying through a cloud does not
    // reveal a hard-edged card.
    float fade = smoothstep(uNear, uNear * 3.0, -mv.z);
    vAlpha = aAlpha * fade;

    // Drifting uv offset keeps the gas from looking frozen.
    vUv = uv + aUvOffset + vec2(uTime * 0.004, uTime * -0.003);

    gl_Position = projectionMatrix * mv;
  }
`;

const fragment = /* glsl */ `
  precision highp float;
  uniform sampler2D uMap;

  varying vec2 vUv;
  varying vec3 vColor;
  varying float vAlpha;

  void main() {
    float n = texture2D(uMap, vUv).a;
    float a = n * vAlpha;
    if (a < 0.003) discard;
    gl_FragColor = vec4(vColor, a);
  }
`;

export const PuffMaterial = shaderMaterial(
  { uTime: 0, uNear: 60, uMap: null },
  vertex,
  fragment
);

extend({ PuffMaterial });

// Gas emits.
export const GAS_BLEND = {
  transparent: true,
  depthWrite: false,
  depthTest: true,
  blending: THREE.AdditiveBlending,
  toneMapped: false,
};

// Dust absorbs. This uses ordinary alpha compositing of a dark colour rather
// than MultiplyBlending: multiply produced opaque near-white quads here
// instead of extinction, and normal-blending a dark sprite over the gas gives
// the same read with completely predictable compositing. Because the lanes
// are drawn before the stars, they carve the gas without dimming starlight.
export const DUST_BLEND = {
  transparent: true,
  depthWrite: false,
  depthTest: true,
  blending: THREE.NormalBlending,
  toneMapped: false,
};
