import * as THREE from 'three';
import { mulberry32 } from '../hash';

// One shared fBm puff texture for every gas and dust billboard.
//
// Module-level singleton: generation costs ~15ms, and remounting the scene or
// switching repos must not pay it again. 256x256 RGBA = 256KB.
//
// Chose layered billboards over raymarched volume noise deliberately —
// raymarching costs 30-60 texture/ALU steps per covered pixel, and with the
// disc filling the screen that is the entire frame budget on integrated
// graphics. A few hundred soft quads are fill-rate bound and nothing more.

let cached = null;

const SIZE = 256;

function valueNoise(rnd) {
  // Coarse random lattice, bilinearly sampled — cheap and smooth enough once
  // several octaves are stacked.
  const G = 32;
  const grid = new Float32Array(G * G);
  for (let i = 0; i < grid.length; i++) grid[i] = rnd();

  return (x, y, freq) => {
    const fx = x * freq;
    const fy = y * freq;
    const x0 = Math.floor(fx), y0 = Math.floor(fy);
    const tx = fx - x0, ty = fy - y0;
    // Smoothstep the interpolant so cell edges do not show.
    const sx = tx * tx * (3 - 2 * tx);
    const sy = ty * ty * (3 - 2 * ty);
    const at = (cx, cy) => grid[(((cy % G) + G) % G) * G + (((cx % G) + G) % G)];
    const a = at(x0, y0), b = at(x0 + 1, y0);
    const c = at(x0, y0 + 1), d = at(x0 + 1, y0 + 1);
    return (a + (b - a) * sx) * (1 - sy) + (c + (d - c) * sx) * sy;
  };
}

export function getPuffTexture() {
  if (cached) return cached;

  const rnd = mulberry32(0x9a5f00d);
  const noise = valueNoise(rnd);
  const data = new Uint8Array(SIZE * SIZE * 4);

  for (let y = 0; y < SIZE; y++) {
    for (let x = 0; x < SIZE; x++) {
      const u = x / SIZE;
      const v = y / SIZE;

      // 4-octave fBm
      let n = 0, amp = 0.5, freq = 4;
      for (let o = 0; o < 4; o++) {
        n += noise(u, v, freq) * amp;
        amp *= 0.5;
        freq *= 2;
      }

      // Radial falloff, so each tile is already a soft puff rather than a
      // square that needs masking at the edges.
      const dx = u - 0.5, dy = v - 0.5;
      const d = Math.min(1, Math.sqrt(dx * dx + dy * dy) * 2);
      const fall = Math.pow(Math.max(0, 1 - d), 2.2);

      const a = Math.max(0, Math.min(1, n * fall * 1.6));
      const i = (y * SIZE + x) * 4;
      data[i] = 255;
      data[i + 1] = 255;
      data[i + 2] = 255;
      data[i + 3] = Math.round(a * 255);
    }
  }

  cached = new THREE.DataTexture(data, SIZE, SIZE, THREE.RGBAFormat);
  cached.wrapS = THREE.RepeatWrapping;
  cached.wrapT = THREE.RepeatWrapping;
  cached.minFilter = THREE.LinearFilter;
  cached.magFilter = THREE.LinearFilter;
  cached.needsUpdate = true;
  return cached;
}
