import * as THREE from 'three';

// Compute a camera fly-to target (position + lookAt) that frames the given node IDs.
export function computeCameraTarget(nodes, ids) {
  if (ids.size === 0) return null;

  let cx = 0,
    cy = 0,
    cz = 0,
    count = 0;
  for (const node of nodes) {
    if (ids.has(node.id)) {
      cx += node.x;
      cy += node.y;
      cz += node.z;
      count++;
    }
  }
  if (count === 0) return null;

  cx /= count;
  cy /= count;
  cz /= count;

  // Distance based on cluster spread — ensure we never zoom too close
  let maxDist = 0;
  for (const node of nodes) {
    if (ids.has(node.id)) {
      const d = Math.sqrt((node.x - cx) ** 2 + (node.y - cy) ** 2 + (node.z - cz) ** 2);
      if (d > maxDist) maxDist = d;
    }
  }

  // Minimum distance scales with count: single node = 300, cluster = spread-based
  const spreadDist = maxDist * 3;
  const minDist = count <= 5 ? 300 : 200;
  const distance = Math.max(minDist, spreadDist);
  const lookAt = new THREE.Vector3(cx, cy, cz);
  const position = new THREE.Vector3(cx + distance * 0.2, cy + distance * 0.15, cz + distance);

  return { position, lookAt };
}
