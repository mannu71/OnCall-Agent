import * as THREE from 'three';
import { colorForEdgeType, alphaForEdgeType } from '../palette';

// Curved edge tessellation.
//
// Two shapes, because intra-arm and cross-arm edges are answering different
// questions:
//
//   intra-arm  — bowed OUTWARD from the galactic centre. Sagging inward would
//                funnel every edge through the bright core and produce a
//                hairball, which is exactly what the old straight-line
//                rendering did.
//   cross-arm  — bowed OUT OF THE DISC PLANE. In a flat disc a chord between
//                two arms is buried among the stars it passes over; lifting it
//                clear is what makes cross-cutting dependencies visible at all.

export const SEGMENTS = 8;

// Bow amounts are a fraction of edge length, but CAPPED against the galaxy
// radius. Uncapped, a chord spanning the disc lifts by half the galaxy and the
// cross-arm edges form a wireframe cage around it instead of arcs within it.
const OUTWARD_BOW = 0.18;
const OUTWARD_CAP = 0.10; // x R
const PLANE_BOW = 0.12;
const PLANE_CAP = 0.13; // x R

const _s = new THREE.Vector3();
const _t = new THREE.Vector3();
const _mid = new THREE.Vector3();
const _ctrl = new THREE.Vector3();
const _p = new THREE.Vector3();
const _color = new THREE.Color();

function quadratic(out, a, c, b, t) {
  const mt = 1 - t;
  out.set(
    mt * mt * a.x + 2 * mt * t * c.x + t * t * b.x,
    mt * mt * a.y + 2 * mt * t * c.y + t * t * b.y,
    mt * mt * a.z + 2 * mt * t * c.z + t * t * b.z
  );
  return out;
}

export function isCrossArm(s, t) {
  return (
    s.armIndex !== undefined &&
    t.armIndex !== undefined &&
    s.armIndex >= 0 &&
    t.armIndex >= 0 &&
    s.armIndex !== t.armIndex
  );
}

/**
 * Tessellate edges into a line-segment buffer.
 *
 * @param select  (edge, s, t) => intensity | null   null skips the edge
 */
export function buildEdgeGeometry(nodes, edges, select, folderHues, R = 1200) {
  const index = new Map();
  for (let i = 0; i < nodes.length; i++) index.set(nodes[i].id, i);

  const positions = [];
  const colors = [];

  for (const edge of edges) {
    const si = index.get(edge.source);
    const ti = index.get(edge.target);
    if (si === undefined || ti === undefined) continue;

    const s = nodes[si];
    const t = nodes[ti];
    const intensity = select(edge, s, t);
    if (intensity === null) continue;

    const cross = isCrossArm(s, t);

    _s.set(s.x, s.y, s.z);
    _t.set(t.x, t.y, t.z);
    _mid.addVectors(_s, _t).multiplyScalar(0.5);
    const dist = _s.distanceTo(_t);

    _ctrl.copy(_mid);
    if (cross) {
      // Lift clear of the disc, alternating side by a stable per-edge bit so
      // the arcs do not all pile onto one face.
      const side = (edge.source + edge.target) % 2 === 0 ? 1 : -1;
      _ctrl.y += side * Math.min(dist * PLANE_BOW, R * PLANE_CAP);
    } else {
      // Push away from the galactic axis.
      const radial = Math.hypot(_mid.x, _mid.z) || 1;
      const bow = Math.min(dist * OUTWARD_BOW, R * OUTWARD_CAP);
      _ctrl.x += (_mid.x / radial) * bow;
      _ctrl.z += (_mid.z / radial) * bow;
    }

    // Cross-arm edges carry their relationship family; intra-arm edges take
    // the folder hue and recede into texture.
    if (cross) {
      _color.set(colorForEdgeType(edge.type));
    } else {
      const hue = folderHues?.[s.fi];
      _color.set(hue ?? colorForEdgeType(edge.type));
    }

    for (let k = 0; k < SEGMENTS; k++) {
      const t0 = k / SEGMENTS;
      const t1 = (k + 1) / SEGMENTS;

      quadratic(_p, _s, _ctrl, _t, t0);
      positions.push(_p.x, _p.y, _p.z);
      colors.push(_color.r * intensity, _color.g * intensity, _color.b * intensity);

      quadratic(_p, _s, _ctrl, _t, t1);
      positions.push(_p.x, _p.y, _p.z);
      colors.push(_color.r * intensity, _color.g * intensity, _color.b * intensity);
    }
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  return geo;
}

export { alphaForEdgeType };
