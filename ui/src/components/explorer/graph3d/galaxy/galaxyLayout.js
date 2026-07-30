// Re-projects the server's ring layout into a barred spiral galaxy.
//
// The C engine (layout3d.c) seeds every node onto a thin annulus of radius
// 500-750 hashed by directory, with z = -call_depth * 50. That's a star chart,
// not a galaxy. This module throws away the annulus and rebuilds positions as
// a Milky-Way-like SBbc: a bar-and-bulge core of the most-connected symbols,
// four logarithmic spiral arms carrying the code, and a halo of globular
// clusters for the folders too small to earn an arm segment.
//
// Pure and deterministic — no three.js, no React. Every scatter decision comes
// from a hash of the node's own identity, so reloads are byte-identical.
//
// The disc lies in the XZ plane (x = r cos, z = r sin, y = thickness) so the
// disc normal is +Y, which is what OrbitControls' up-vector expects.

import { nodeSeed, mulberry32, gauss, clamp, unitVector, byId } from '../hash';
import { folderHue, BULGE_TINT, HALO_TINT } from '../palette';
import { groupFolders, assignFoldersToArms, allocateSegments, BAR_ANGLE } from './armAssignment';

// Arm pitch angle. The Milky Way's true value is ~12 degrees, but that winds
// the arms 1.28 turns between the bar and the rim, so they overlap themselves
// and stop being readable from any single viewpoint. 20 degrees gives ~0.75
// turns — the classic grand-design look (M51 sits around here) — and keeps
// each arm traceable end to end.
const PITCH = (20 * Math.PI) / 180;
const B = Math.tan(PITCH); // 0.364
const CORE_FRACTION = 0.18; // arms start here, at the bar ends

// Fraction of the highest-degree nodes pulled into the bar regardless of
// folder. These are the symbols everything else depends on.
const BAR_DEGREE_FRACTION = 0.02;

// Real galaxies are not only arms. Without a field population between them the
// picture reads as a pinwheel rather than a galaxy.
const INTERARM_FRACTION = 0.18;
const INTERARM_SPREAD = 1.2; // radians of azimuthal scatter

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

function mixHex(a, b, t) {
  const pa = parseInt(a.slice(1), 16);
  const pb = parseInt(b.slice(1), 16);
  const ar = pa >> 16, ag = (pa >> 8) & 255, ab = pa & 255;
  const br = pb >> 16, bg = (pb >> 8) & 255, bb = pb & 255;
  const r = Math.round(ar + (br - ar) * t);
  const g = Math.round(ag + (bg - ag) * t);
  const bl = Math.round(ab + (bb - ab) * t);
  return `#${((r << 16) | (g << 8) | bl).toString(16).padStart(6, '0')}`;
}

export function spiralRadius(rcore, theta) {
  return rcore * Math.exp(B * theta);
}

// Inverse: which spiral phase corresponds to a given radius.
export function spiralTheta(rcore, r) {
  return Math.log(Math.max(r, rcore) / rcore) / B;
}

// Map an area fraction (0..1 across the disc annulus) to a radius. Uniform in
// area means uniform in apparent star density, which is what stops the core
// blowing out into a solid blob.
export function radiusForAreaFraction(rcore, R, u) {
  return Math.sqrt(rcore * rcore + clamp(u, 0, 1) * (R * R - rcore * rcore));
}

/**
 * @param {Array} rawNodes  nodes straight from GET /codegraph/repos/{r}/layout
 * @param {Array} rawEdges
 * @returns {{nodes, folders, arms, globulars, R, rcore, thetaMax, depth}}
 */
export function buildGalaxy(rawNodes, rawEdges) {
  const count = rawNodes.length;
  if (count === 0) {
    return { nodes: [], folders: [], arms: [], globulars: [], R: 1000, rcore: 180, thetaMax: 8, depth: 1 };
  }

  // Scale the galaxy with node count so star density stays roughly constant.
  // A sparse disc gets smaller rather than being padded with invented stars.
  const R = clamp(28 * Math.sqrt(count), 900, 3600);
  const rcore = CORE_FRACTION * R;
  const thetaMax = Math.log(R / rcore) / B;

  // --- degrees ----------------------------------------------------------
  const deg = new Map();
  for (const e of rawEdges) {
    deg.set(e.source, (deg.get(e.source) ?? 0) + 1);
    deg.set(e.target, (deg.get(e.target) ?? 0) + 1);
  }

  // Work on copies so the caller's fetched payload stays pristine and the
  // original server coordinates remain available for a debug toggle.
  const nodes = rawNodes.map((n) => ({
    ...n,
    sx: n.x,
    sy: n.y,
    sz: n.z,
    deg: deg.get(n.id) ?? 0,
  }));
  const nodeById = new Map(nodes.map((n) => [n.id, n]));

  // --- who lives in the bar --------------------------------------------
  // Nodes with no file_path (the Project node, packages) plus the top slice by
  // degree. Marking is done first so folder membership can exclude them.
  const barSet = new Set();
  for (const n of nodes) if (!n.file_path) barSet.add(n.id);

  const ranked = [...nodes].sort((a, b) => b.deg - a.deg || byId(a, b));
  const barQuota = Math.max(6, Math.round(count * BAR_DEGREE_FRACTION));
  for (let i = 0; i < ranked.length && barSet.size < barQuota; i++) {
    if (ranked[i].deg > 0) barSet.add(ranked[i].id);
  }

  // --- folders -> arms --------------------------------------------------
  const discCandidates = nodes.filter((n) => !barSet.has(n.id));
  const { depth, folders, strays } = groupFolders(discCandidates);
  const arms = allocateSegments(assignFoldersToArms(folders));

  folders.forEach((f, i) => {
    f.hue = folderHue(i);
    f.index = i;
    f.armName = arms[f.armIndex].name;
  });

  // --- bar + bulge ------------------------------------------------------
  // Triaxial ellipsoid aligned to BAR_ANGLE: long along the bar, narrow across
  // it, flattest vertically. pow(rnd, 0.55) biases toward the centre so it
  // reads as a dense bulge tapering into the bar ends.
  const barA = 0.34 * R;
  const barBAcross = 0.11 * R;
  const barC = 0.07 * R;
  const cosBar = Math.cos(BAR_ANGLE);
  const sinBar = Math.sin(BAR_ANGLE);

  for (const n of nodes) {
    if (!barSet.has(n.id)) continue;
    const rnd = mulberry32(nodeSeed(n));
    const dir = unitVector(rnd);
    const m = Math.pow(rnd(), 0.55);

    const lx = dir.x * barA * m;
    const lz = dir.z * barBAcross * m;
    n.x = lx * cosBar - lz * sinBar;
    n.z = lx * sinBar + lz * cosBar;
    n.y = dir.y * barC * m;

    n.component = 'bar';
    n.fi = -1;
    n.armIndex = -1;
    n.starColor = mixHex(n.color ?? '#ffffff', BULGE_TINT, 0.25);
  }

  // --- disc -------------------------------------------------------------
  for (const folder of folders) {
    const members = [...folder.nodes].sort((a, b) => b.deg - a.deg || byId(a, b));
    const span = folder.u1 - folder.u0;
    const arm = arms[folder.armIndex];
    // Minor arms are thinner and dimmer than major ones.
    const armWidth = arm.major ? 1.0 : 0.78;

    members.forEach((n, rank) => {
      const rnd = mulberry32(nodeSeed(n));
      const t = members.length > 1 ? rank / (members.length - 1) : 0;

      // Hubs rank first, so they sit at the inner end of the segment. The
      // call-depth nudge makes callees trail their callers along the arm; it
      // is approximate (the server runs local_optimize after seeding) and is
      // deliberately only a nudge, never a primary coordinate.
      const callDepth = clamp(Math.round(-(n.sz ?? 0) / 50), 0, 12);

      const r = radiusForAreaFraction(rcore, R, folder.u0 + t * span);
      const theta = spiralTheta(rcore, r) + callDepth * 0.05;
      let angle = arm.base + theta;

      // Field stars: displaced azimuthally at the same radius, which puts them
      // genuinely between the arms rather than further along one.
      if (rnd() < INTERARM_FRACTION) {
        angle += (rnd() - 0.5) * INTERARM_SPREAD;
        n.interArm = true;
      }

      // Arms get fuzzier outward. Pitch is only 12 degrees, so scattering
      // radially is within a couple of degrees of the true arm normal.
      const w = (r * 0.055 + rcore * 0.02) * armWidth;
      const rr = Math.max(rcore * 0.35, r + gauss(rnd) * w);

      n.x = rr * Math.cos(angle);
      n.z = rr * Math.sin(angle);
      // Thin outer disc thickening toward the bulge.
      n.y = gauss(rnd) * (R * 0.01 + R * 0.09 * Math.exp(-r / (R * 0.25)));

      n.component = 'disc';
      n.fi = folder.index;
      n.armIndex = folder.armIndex;
      n.theta = theta;
      n.starColor = mixHex(n.color ?? '#ffffff', folder.hue, 0.22);
    });
  }

  // --- halo: globular clusters -----------------------------------------
  // Folders too small for an arm segment become tight blobs on a sphere well
  // outside the disc. Astronomically right, and it gives small folders their
  // own identity instead of losing them in an arm.
  const globulars = [];
  if (strays.length > 0) {
    strays.sort(byId);
    const clusterCount = clamp(Math.round(strays.length / 6), 3, 12);
    for (let i = 0; i < clusterCount; i++) {
      const y = 1 - (2 * i + 1) / clusterCount;
      const ring = Math.sqrt(Math.max(0, 1 - y * y));
      const th = GOLDEN_ANGLE * i;
      const rad = R * (1.15 + 0.45 * (((i * 7919) % 100) / 100));
      globulars.push({
        index: i,
        x: Math.cos(th) * ring * rad,
        y: y * rad * 0.75,
        z: Math.sin(th) * ring * rad,
        sigma: R * 0.035,
      });
    }

    strays.forEach((n, i) => {
      const g = globulars[i % globulars.length];
      const rnd = mulberry32(nodeSeed(n));
      n.x = g.x + gauss(rnd) * g.sigma;
      n.y = g.y + gauss(rnd) * g.sigma;
      n.z = g.z + gauss(rnd) * g.sigma;
      n.component = 'halo';
      n.fi = -1;
      n.armIndex = -1;
      n.starColor = mixHex(n.color ?? '#ffffff', HALO_TINT, 0.3);
    });
  }

  // Anything that somehow escaped placement (no file_path and not in the bar
  // quota) gets parked in the bulge rather than at the origin.
  for (const n of nodes) {
    if (n.component) continue;
    const rnd = mulberry32(nodeSeed(n));
    const dir = unitVector(rnd);
    const m = Math.pow(rnd(), 0.55);
    n.x = dir.x * barA * m;
    n.y = dir.y * barC * m;
    n.z = dir.z * barBAcross * m;
    n.component = 'bar';
    n.fi = -1;
    n.armIndex = -1;
    n.starColor = mixHex(n.color ?? '#ffffff', BULGE_TINT, 0.25);
  }

  // Folder node lists point at the same objects we just positioned, so the
  // legend can read counts and hues straight off them.
  return {
    nodes,
    nodeById,
    folders: folders.map((f) => ({
      key: f.key,
      name: f.name,
      index: f.index,
      hue: f.hue,
      armIndex: f.armIndex,
      armName: f.armName,
      count: f.nodes.length,
      // Area-fraction span along the arm — the gas layer reuses this to trace
      // the same segment the stars occupy.
      u0: f.u0,
      u1: f.u1,
    })),
    arms: arms.map((a) => ({ name: a.name, base: a.base, major: a.major, index: a.index, load: a.load })),
    globulars,
    R,
    rcore,
    thetaMax,
    depth,
  };
}
