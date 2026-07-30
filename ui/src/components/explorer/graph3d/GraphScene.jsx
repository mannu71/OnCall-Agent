import { useState, useRef, useMemo } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import { StarPoints } from './stars/StarPoints';
import { ArmEdges } from './edges/ArmEdges';
import { NodeLabels } from './NodeLabels';
import { NodeTooltip } from './NodeTooltip';
import { CameraRig } from './CameraRig';
import { PostFX } from './PostFX';
import { useReducedMotion } from './useReducedMotion';
import { Disc } from './galaxy/Disc';
import { GalacticField } from './galaxy/GalacticField';
import { Nebulae } from './galaxy/Nebulae';
import { DustLanes } from './galaxy/DustLanes';
import { Starfield } from './space/Starfield';
import { DeepField } from './space/DeepField';
import { DustMotes } from './space/DustMotes';

export function GraphScene({ data, galaxy, highlightedIds, cameraTarget, showLabels, showLinks, onNodeClick }) {
  const [hovered, setHovered] = useState(null);
  const controlsRef = useRef(null);
  const discRef = useRef(null);
  const reducedMotion = useReducedMotion();

  const R = galaxy?.R ?? 1200;

  // Scale the decorative field with how much code there actually is. A full
  // 90k-particle galaxy painted over a 13-node index would imply a large
  // codebase that does not exist — the scenery must not overstate the data.
  const fieldCount = useMemo(() => {
    const n = galaxy?.nodes?.length ?? 0;
    return Math.round(Math.max(2500, Math.min(90000, n * 45)));
  }, [galaxy]);

  // Adjacency built once per dataset so hovering can resolve a node's
  // neighbours without scanning every edge on each pointer move.
  const adjacency = useMemo(() => {
    const m = new Map();
    for (const e of data.edges) {
      let a = m.get(e.source); if (!a) m.set(e.source, (a = new Set()));
      a.add(e.target);
      let b = m.get(e.target); if (!b) m.set(e.target, (b = new Set()));
      b.add(e.source);
    }
    return m;
  }, [data.edges]);

  const hoverIds = useMemo(() => {
    if (!hovered) return null;
    const s = new Set([hovered.id]);
    for (const id of adjacency.get(hovered.id) ?? []) s.add(id);
    return s;
  }, [hovered, adjacency]);

  // folder index -> hue, so intra-arm edges can take their segment's colour.
  const folderHues = useMemo(() => {
    const out = {};
    for (const f of galaxy?.folders ?? []) out[f.index] = f.hue;
    return out;
  }, [galaxy]);

  return (
    <Canvas
      camera={{ position: [0, 0, 800], fov: 55, near: 1, far: 250000 }}
      style={{ background: '#06090f' }}
      dpr={[1, 2]}
      gl={{ antialias: true, alpha: false }}
    >
      <color attach="background" args={['#06090f']} />

      {/* Deep space. Outside the rotating disc: the sky must not spin with
          the galaxy. */}
      <Starfield />
      <DeepField />
      <DustMotes />

      {/* Everything the layout positions rides the rotating disc, so its
          geometry stays static in local space. */}
      {/* Render order inside the disc is load-bearing:
          edges (0) -> gas (1) -> dust lanes (2) -> stars (3). See DustLanes
          for why the lanes sit between the gas and the stars. */}
      <Disc ref={discRef} paused={reducedMotion}>
        {/* The luminous sheet the whole illusion rests on. Drawn first so the
            dust lanes have something to silhouette against. */}
        <GalacticField galaxy={galaxy} count={fieldCount} />
        <ArmEdges
          nodes={data.nodes}
          edges={data.edges}
          highlightedIds={highlightedIds}
          hoverIds={hoverIds}
          folderHues={folderHues}
          R={R}
          showAmbient={showLinks}
        />
        <Nebulae galaxy={galaxy} />
        <DustLanes galaxy={galaxy} />
        <StarPoints
          nodes={data.nodes}
          highlightedIds={highlightedIds}
          onHover={setHovered}
          onClick={onNodeClick}
        />
        {showLabels && <NodeLabels nodes={data.nodes} highlightedIds={highlightedIds} />}
        {hovered && <NodeTooltip node={hovered} />}
      </Disc>

      <CameraRig
        target={cameraTarget}
        nodes={data.nodes}
        discRef={discRef}
        controlsRef={controlsRef}
        R={R}
      />

      <PostFX />

      <OrbitControls
        ref={controlsRef}
        enableDamping
        dampingFactor={0.08}
        rotateSpeed={0.5}
        zoomSpeed={1.5}
        minDistance={5}
        maxDistance={R * 8}
      />
    </Canvas>
  );
}
