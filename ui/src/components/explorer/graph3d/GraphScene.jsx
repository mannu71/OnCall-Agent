import { useState, useRef, useEffect, useCallback } from 'react';
import { Canvas, useThree, useFrame } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import { EffectComposer, Bloom } from '@react-three/postprocessing';
import * as THREE from 'three';
import { NodeCloud } from './NodeCloud';
import { EdgeLines } from './EdgeLines';
import { NodeLabels } from './NodeLabels';
import { NodeTooltip } from './NodeTooltip';

// Camera fly-to animation

function CameraAnimator({ target }) {
  const { camera } = useThree();
  const targetRef = useRef(null);
  const progress = useRef(1);

  useEffect(() => {
    if (target) {
      targetRef.current = target;
      progress.current = 0;
    }
  }, [target]);

  useFrame(() => {
    if (!targetRef.current || progress.current >= 1) return;

    progress.current = Math.min(1, progress.current + 0.02);
    const t = 1 - Math.pow(1 - progress.current, 3); // ease-out cubic

    camera.position.lerp(targetRef.current.position, t * 0.08);
    camera.lookAt(targetRef.current.lookAt);
  });

  return null;
}

// Idle auto-rotation

const IDLE_TIMEOUT_MS = 60_000;

function IdleAutoRotate({ controlsRef }) {
  const lastInteraction = useRef(null);

  const resetTimer = useCallback(() => {
    lastInteraction.current = Date.now();
    if (controlsRef.current) {
      controlsRef.current.autoRotate = false;
    }
  }, [controlsRef]);

  useEffect(() => {
    resetTimer();
    const canvas = document.querySelector('canvas');
    if (!canvas) return;

    canvas.addEventListener('pointerdown', resetTimer);
    canvas.addEventListener('wheel', resetTimer);
    return () => {
      canvas.removeEventListener('pointerdown', resetTimer);
      canvas.removeEventListener('wheel', resetTimer);
    };
  }, [resetTimer]);

  useFrame(() => {
    if (!controlsRef.current || lastInteraction.current === null) return;
    const idle = Date.now() - lastInteraction.current > IDLE_TIMEOUT_MS;
    controlsRef.current.autoRotate = idle;
  });

  return null;
}

// Main scene

export function GraphScene({ data, highlightedIds, cameraTarget, showLabels, onNodeClick }) {
  const [hovered, setHovered] = useState(null);
  const controlsRef = useRef(null);

  return (
    <Canvas
      camera={{ position: [0, 0, 800], fov: 50, near: 0.1, far: 100000 }}
      style={{ background: '#06090f' }}
      dpr={[1, 2]}
      gl={{ antialias: true, alpha: false }}
    >
      <color attach="background" args={['#06090f']} />
      <ambientLight intensity={0.5} />
      <pointLight position={[500, 500, 500]} intensity={0.6} />
      <pointLight position={[-300, -200, -300]} intensity={0.4} color="#6040ff" />

      <EdgeLines nodes={data.nodes} edges={data.edges} highlightedIds={highlightedIds} />
      <NodeCloud
        nodes={data.nodes}
        highlightedIds={highlightedIds}
        onHover={setHovered}
        onClick={onNodeClick}
      />
      {showLabels && <NodeLabels nodes={data.nodes} highlightedIds={highlightedIds} />}

      {/* Satellite galaxies for cross-repo linked projects */}
      {data.linked_projects?.map((lp) => {
        const offsetNodes = lp.nodes.map((n) => ({
          ...n,
          x: n.x + lp.offset.x,
          y: n.y + lp.offset.y,
          z: n.z + lp.offset.z,
        }));
        return (
          <group key={lp.project}>
            <EdgeLines nodes={offsetNodes} edges={lp.edges} highlightedIds={null} opacity={0.3} />
            <NodeCloud
              nodes={offsetNodes}
              highlightedIds={null}
              onHover={setHovered}
              onClick={onNodeClick}
              opacity={0.5}
            />
            {/* Inter-galaxy CROSS_* edges: source is in primary, target in
             * this linked project's offset nodes. */}
            {lp.cross_edges && lp.cross_edges.length > 0 && (
              <EdgeLines
                nodes={data.nodes}
                targetNodes={offsetNodes}
                edges={lp.cross_edges}
                highlightedIds={highlightedIds}
                opacity={0.85}
              />
            )}
          </group>
        );
      })}

      {hovered && <NodeTooltip node={hovered} />}

      <CameraAnimator target={cameraTarget} />
      <IdleAutoRotate controlsRef={controlsRef} />

      <EffectComposer>
        <Bloom
          luminanceThreshold={0.3}
          luminanceSmoothing={0.7}
          intensity={1.2}
          mipmapBlur
          radius={0.6}
        />
      </EffectComposer>

      <OrbitControls
        ref={controlsRef}
        enableDamping
        dampingFactor={0.08}
        rotateSpeed={0.5}
        zoomSpeed={1.5}
        minDistance={10}
        maxDistance={50000}
        autoRotateSpeed={0.4}
      />
    </Canvas>
  );
}
