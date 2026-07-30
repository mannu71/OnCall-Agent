import { useMemo } from 'react';
import { useThree } from '@react-three/fiber';
import {
  EffectComposer,
  Bloom,
  Vignette,
  Noise,
  ChromaticAberration,
} from '@react-three/postprocessing';
import { BlendFunction } from 'postprocessing';
import * as THREE from 'three';

/**
 * Post stack, sized to the canvas.
 *
 * mipmapBlur halves the render target once per level. With the default 8
 * levels a narrow canvas (the Explorer's graph pane can be under 300px with
 * both side panels open) drives an intermediate mip to zero, the framebuffer
 * goes incomplete, and the composer outputs solid black — the whole scene
 * disappears with no console error. Deriving the level count from the smaller
 * canvas dimension keeps the chain valid at any pane width.
 *
 * Bloom is tuned for a scene with many emissive sources: a higher threshold
 * than the old 0.3 (the backdrop is bright enough to smear at that) and lower
 * intensity than the old 1.2.
 */
export function PostFX({ quality = 'high' }) {
  const size = useThree((s) => s.size);

  const levels = useMemo(() => {
    const min = Math.max(1, Math.min(size.width, size.height));
    return Math.max(3, Math.min(8, Math.floor(Math.log2(min)) - 2));
  }, [size.width, size.height]);

  const rich = quality === 'high';

  return (
    // Re-key so the composer rebuilds its passes when the level count changes.
    // HalfFloat gives the gas gradients enough precision to avoid banding.
    <EffectComposer key={`${levels}-${quality}`} frameBufferType={THREE.HalfFloatType}>
      <Bloom
        luminanceThreshold={0.55}
        luminanceSmoothing={0.35}
        intensity={0.9}
        mipmapBlur
        radius={0.75}
        levels={levels}
      />
      {rich ? (
        <ChromaticAberration
          offset={[0.0004, 0.0004]}
          radialModulation
          modulationOffset={0.35}
        />
      ) : null}
      {/* Grain doubles as dither against banding in the faint nebulae. */}
      <Noise opacity={0.025} blendFunction={BlendFunction.OVERLAY} />
      {/* Cheapest single thing that reads as "space". */}
      <Vignette offset={0.28} darkness={0.75} />
    </EffectComposer>
  );
}
