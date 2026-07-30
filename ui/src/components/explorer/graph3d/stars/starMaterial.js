import * as THREE from 'three';
import { shaderMaterial } from '@react-three/drei';
import { extend } from '@react-three/fiber';

// Star billboards.
//
// The old NodeCloud used sphereGeometry(1, 32, 24) — 1536 triangles per
// instance, 3.07M per frame at 2000 nodes — and a sphere still doesn't look
// like a star. A camera-facing quad with a procedural corona costs 2 triangles
// and looks better: tight blown-out core, soft halo, and diffraction spikes on
// the big hubs.
//
// Position and scale live in instanced attributes rather than instanceMatrix,
// because the vertex shader has to billboard anyway. That leaves instanceMatrix
// identity and makes every per-frame write unnecessary — only uTime changes.

// Quad covers the corona, which needs roughly 3x the core's footprint.
export const STAR_QUAD_SCALE = 3.2;

const vertex = /* glsl */ `
  attribute vec3 aOffset;
  attribute float aSize;
  attribute float aSpike;
  attribute float aPhase;
  attribute float aTwSpeed;
  attribute vec3 aColor;
  attribute float aDim;

  uniform float uTime;
  uniform float uScale;
  uniform float uFogNear;
  uniform float uFogDensity;
  uniform float uPixelScale; // viewportHeight / (2 * tan(fov/2))
  uniform float uMinPixels;
  uniform float uMaxPixels;

  varying vec2 vUv;
  varying vec3 vColor;
  varying float vDim;
  varying float vSpike;
  varying float vFog;

  void main() {
    vUv = uv;
    vColor = aColor;
    vSpike = aSpike;

    // Twinkle is deliberately tiny. These are code symbols; anything stronger
    // reads as strobing rather than starlight.
    float tw = 1.0 + 0.06 * sin(uTime * aTwSpeed + aPhase);
    vDim = aDim * tw;

    vec4 mv = modelViewMatrix * vec4(aOffset, 1.0);

    // Clamp the quad to a screen-space size range.
    //
    // A Method star is 4 units across; framed on a 1200-unit galaxy that is
    // well under one pixel, and a soft-falloff billboard at sub-pixel size
    // renders as nothing at all. Opaque spheres got away with it because they
    // always lit at least one pixel. So convert the desired pixel bounds into
    // world units at this depth and clamp. The max stops a hub ballooning into
    // a screen-filling blob when you fly into the core.
    float unitsPerPixel = max(1e-6, -mv.z) / uPixelScale;
    float world = clamp(aSize * uScale,
                        uMinPixels * unitsPerPixel,
                        uMaxPixels * unitsPerPixel);
    mv.xy += position.xy * world;

    // Depth attenuation. Raw ShaderMaterial does not receive scene fog without
    // manually injecting the fog chunks, and scene fog would wrongly dim the
    // backdrop too — so distance falloff is applied here instead.
    vFog = exp(-max(0.0, -mv.z - uFogNear) * uFogDensity);

    gl_Position = projectionMatrix * mv;
  }
`;

const fragment = /* glsl */ `
  precision highp float;

  varying vec2 vUv;
  varying vec3 vColor;
  varying float vDim;
  varying float vSpike;
  varying float vFog;

  void main() {
    vec2 p = vUv * 2.0 - 1.0;
    float d = length(p);
    if (d > 1.0) discard;

    float core = pow(max(0.0, 1.0 - d), 14.0);
    float halo = pow(max(0.0, 1.0 - d), 2.2) * 0.20;
    float spike = (pow(max(0.0, 1.0 - abs(p.x) * 8.0), 3.0)
                 + pow(max(0.0, 1.0 - abs(p.y) * 8.0), 3.0)) * max(0.0, 1.0 - d);

    // Overall gain kept below 1: 2000 additive sprites overlap heavily in the
    // bulge, and at full strength the core saturates to a featureless white
    // disc that swallows the bar.
    float a = (core + halo + spike * vSpike) * vDim * vFog * 0.85;
    if (a < 0.004) discard;

    // Push the very centre past 1.0 so bloom picks up the excess as a corona.
    gl_FragColor = vec4(vColor * (1.0 + core * 1.6), a);
  }
`;

// Declared via drei's shaderMaterial so it can be mounted as JSX and driven
// through a ref inside useFrame. Creating the material during render and
// mutating it there instead trips React's ref/immutability rules.
export const StarMaterial = shaderMaterial(
  {
    uTime: 0,
    uScale: STAR_QUAD_SCALE,
    uFogNear: 2000,
    uFogDensity: 0.000045,
    uPixelScale: 500,
    uMinPixels: 3.0,
    uMaxPixels: 110.0,
  },
  vertex,
  fragment
);

extend({ StarMaterial });

// Blending/depth settings applied wherever <starMaterial /> is mounted.
export const STAR_MATERIAL_PROPS = {
  transparent: true,
  depthWrite: false,
  depthTest: true,
  blending: THREE.AdditiveBlending,
  toneMapped: false,
};
