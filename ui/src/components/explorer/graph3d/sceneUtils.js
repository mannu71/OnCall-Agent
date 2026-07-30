import * as THREE from 'three';

const _p = new THREE.Vector3();

// Compute a camera fly-to target (position + lookAt) that frames the given
// node IDs.
//
// `matrix` is the world matrix of the rotating disc group. Node coordinates
// are disc-LOCAL, and the disc turns continuously, so resolving them against
// the live matrix is what keeps a fly-to accurate after the galaxy has been
// spinning for a while. Pass null for untransformed coordinates.
export function computeCameraTarget(nodes, ids, matrix = null) {
  if (!ids || ids.size === 0) return null;

  const pts = [];
  let cx = 0, cy = 0, cz = 0;
  for (const node of nodes) {
    if (!ids.has(node.id)) continue;
    _p.set(node.x, node.y, node.z);
    if (matrix) _p.applyMatrix4(matrix);
    pts.push(_p.clone());
    cx += _p.x;
    cy += _p.y;
    cz += _p.z;
  }
  if (pts.length === 0) return null;

  const count = pts.length;
  cx /= count;
  cy /= count;
  cz /= count;

  // Distance based on cluster spread — ensure we never zoom too close
  let maxDist = 0;
  for (const p of pts) {
    const d = Math.sqrt((p.x - cx) ** 2 + (p.y - cy) ** 2 + (p.z - cz) ** 2);
    if (d > maxDist) maxDist = d;
  }

  // Minimum distance scales with count: single node = 300, cluster = spread-based
  const spreadDist = maxDist * 3;
  const minDist = count <= 5 ? 300 : 200;
  const distance = Math.max(minDist, spreadDist);
  const lookAt = new THREE.Vector3(cx, cy, cz);
  // Approach from above the disc plane so the arrival keeps the 3/4 framing
  // rather than dropping edge-on into the disc.
  const position = new THREE.Vector3(
    cx + distance * 0.25,
    cy + distance * 0.45,
    cz + distance * 0.85
  );

  return { position, lookAt };
}
