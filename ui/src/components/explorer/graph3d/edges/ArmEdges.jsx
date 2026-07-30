import { useMemo, useEffect } from 'react';
import * as THREE from 'three';
import { buildEdgeGeometry, isCrossArm } from './edgeGeometry';
import { alphaForEdgeType } from '../palette';

// Curved edges, split into two buffers.
//
// The ambient buffer holds every visible edge and is built once per dataset.
// The highlight buffer is tiny and is the only thing rebuilt when the
// selection changes — the old EdgeLines re-tessellated all 4,479 edges (and
// allocated a THREE.Color per edge) on every click.

// Additive lines accumulate fast where arcs converge, so these are far lower
// than they look. Cross-arm stays ~2x intra: enough to read as the signal
// without turning the disc into a cage.
// A galaxy contains no lines. Any visible edge web breaks the illusion
// instantly, so the ambient layer is barely-there filament — enough to hint at
// structure inside the glow, not enough to read as a diagram. Selecting a node
// is what makes its relationships legible.
const AMBIENT_INTRA = 0.06;
const AMBIENT_CROSS = 0.12;
const HIGHLIGHT_GAIN = 0.85;

function useDisposed(geometry) {
  useEffect(() => () => geometry?.dispose(), [geometry]);
}

export function ArmEdges({ nodes, edges, highlightedIds, hoverIds, folderHues, R, showAmbient = false }) {
  const hasHighlight = highlightedIds && highlightedIds.size > 0;

  // Ambient: independent of selection, so selection changes never touch it.
  // Skipped entirely when hidden — no point tessellating 19k vertices nobody
  // will see.
  const ambient = useMemo(
    () =>
      !showAmbient
        ? null
        :
      buildEdgeGeometry(
        nodes,
        edges,
        (edge, s, t) => {
          const famAlpha = alphaForEdgeType(edge.type);
          // Cross-arm edges are the information; intra-arm edges are texture.
          const base = isCrossArm(s, t) ? AMBIENT_CROSS : AMBIENT_INTRA;
          return base * famAlpha;
        },
        folderHues,
        R
      ),
    [nodes, edges, folderHues, R, showAmbient]
  );

  // Highlight: only edges with both ends in the selection.
  const highlight = useMemo(() => {
    if (!hasHighlight) return null;
    return buildEdgeGeometry(
      nodes,
      edges,
      (edge, s, t) => {
        if (!highlightedIds.has(s.id) || !highlightedIds.has(t.id)) return null;
        return HIGHLIGHT_GAIN * alphaForEdgeType(edge.type);
      },
      folderHues,
      R
    );
  }, [nodes, edges, highlightedIds, hasHighlight, folderHues, R]);

  // Hover: the primary way to answer "what does this connect to". Clicking to
  // find out was too slow a loop, and showing every edge at once hid the
  // galaxy — so pointing at a star lights up just its own links.
  const hover = useMemo(() => {
    if (!hoverIds || hoverIds.size < 2) return null;
    return buildEdgeGeometry(
      nodes,
      edges,
      (edge, s, t) => {
        if (!hoverIds.has(s.id) || !hoverIds.has(t.id)) return null;
        return 1.15 * alphaForEdgeType(edge.type);
      },
      folderHues,
      R
    );
  }, [nodes, edges, hoverIds, folderHues, R]);

  useDisposed(ambient);
  useDisposed(highlight);
  useDisposed(hover);

  return (
    <group renderOrder={0}>
      {ambient && (
        <lineSegments geometry={ambient} raycast={null}>
          <lineBasicMaterial
            vertexColors
            transparent
            // Dim the whole ambient layer while something is selected, so the
            // highlighted subgraph reads against it.
            opacity={hasHighlight ? 0.25 : 1}
            blending={THREE.AdditiveBlending}
            depthWrite={false}
            toneMapped={false}
          />
        </lineSegments>
      )}

      {hover && (
        <lineSegments geometry={hover} raycast={null}>
          <lineBasicMaterial
            vertexColors
            transparent
            blending={THREE.AdditiveBlending}
            depthWrite={false}
            toneMapped={false}
          />
        </lineSegments>
      )}

      {highlight && (
        <lineSegments geometry={highlight} raycast={null}>
          <lineBasicMaterial
            vertexColors
            transparent
            blending={THREE.AdditiveBlending}
            depthWrite={false}
            toneMapped={false}
          />
        </lineSegments>
      )}
    </group>
  );
}
