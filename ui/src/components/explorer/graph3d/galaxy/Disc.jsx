import { forwardRef } from 'react';
import { useFrame } from '@react-three/fiber';

// Rotation rate of the whole galaxy, rad/s. ~26 minutes per revolution.
const SPIN = 0.004;

/**
 * The rotating galaxy. Everything positioned by galaxyLayout lives in here, so
 * all of its geometry is static in disc-local space and never needs rebuilding
 * as the view turns.
 *
 * The disc rotates RIGIDLY — one rotation.y for the whole group. This is not a
 * simplification to save cycles: real spiral arms are density waves precisely
 * because differential rotation would destroy them. Give inner stars a faster
 * angular velocity than outer ones and the arms shear apart into a featureless
 * annulus within a few revolutions (the classic winding problem). It looks
 * more "physical" for about ten seconds and then ruins the picture.
 */
export const Disc = forwardRef(function Disc({ children, paused = false }, ref) {
  useFrame((_, dt) => {
    if (paused || !ref?.current) return;
    // Clamp dt so a backgrounded tab doesn't snap the galaxy round on return.
    ref.current.rotation.y += SPIN * Math.min(dt, 0.1);
  });

  return <group ref={ref}>{children}</group>;
});
