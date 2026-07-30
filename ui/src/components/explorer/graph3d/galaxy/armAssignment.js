// Deciding which folder belongs to which spiral arm, and where along it.
//
// Two problems get solved here:
//
//   1. Grouping depth. `file_path.split('/')[0]` is the obvious key, but on a
//      repo where everything lives under `src/` it yields a single group and
//      the whole spiral collapses. So we walk depth 1..4 and take the first
//      depth that produces enough substantial groups.
//   2. Load balancing. A real spiral has 2 major + 2 minor arms, and a repo
//      has an arbitrary number of top-level folders. Folders get packed onto
//      arms greedily so each arm carries weight-proportional load, then laid
//      out as contiguous angular segments along their arm.

const MIN_FOLDERS = 4;
const MAX_FOLDERS = 20;
const MAX_DEPTH = 4;

// How many nodes a directory needs before it earns an arm segment.
//
// A fixed threshold of 8 is unreachable on a small repo: a 20-node index has
// no directory that big, so every node falls through to the halo and the
// galaxy loses its disc entirely. Scaling with repo size keeps small repos
// spiral-shaped instead of collapsing them into a scatter of globulars.
export function minMembersFor(count) {
  return Math.max(2, Math.min(8, Math.round(count / 25)));
}

export const BAR_ANGLE = 0.45;

// Two major arms springing from the ends of the bar, two minor arms offset
// between them. Weight drives how much of the codebase each arm carries.
export const ARMS = [
  { name: 'Perseus', base: BAR_ANGLE, weight: 1.0, major: true },
  { name: 'Scutum-Centaurus', base: BAR_ANGLE + Math.PI, weight: 1.0, major: true },
  { name: 'Sagittarius', base: BAR_ANGLE + Math.PI / 2, weight: 0.6, major: false },
  { name: 'Norma', base: BAR_ANGLE + (3 * Math.PI) / 2, weight: 0.6, major: false },
];

export function pathPrefix(filePath, depth) {
  if (!filePath) return '';
  const parts = filePath.split('/').filter(Boolean);
  if (parts.length <= 1) return parts[0] ?? '';
  // Never consume the basename as a grouping component — a folder key must
  // describe a directory, not a single file.
  return parts.slice(0, Math.min(depth, parts.length - 1)).join('/');
}

function groupAtDepth(nodes, depth) {
  const groups = new Map();
  for (const n of nodes) {
    const key = pathPrefix(n.file_path, depth);
    if (!key) continue;
    let g = groups.get(key);
    if (!g) groups.set(key, (g = []));
    g.push(n);
  }
  return groups;
}

// Walk deeper until the split is informative: enough groups clear MIN_MEMBERS,
// or going deeper would just shatter it into too many pieces.
export function chooseGroupDepth(nodes, minMembers) {
  let best = 1;
  for (let d = 1; d <= MAX_DEPTH; d++) {
    const groups = groupAtDepth(nodes, d);
    let substantial = 0;
    for (const g of groups.values()) if (g.length >= minMembers) substantial++;
    best = d;
    if (substantial >= MIN_FOLDERS) return d;
    if (groups.size > MAX_FOLDERS) return d;
  }
  return best;
}

// -> { depth, folders: [{key, name, nodes, index}], strays: node[] }
// `strays` are nodes from folders too small to earn an arm segment; they
// become globular clusters in the halo instead of being lost in an arm.
export function groupFolders(nodes) {
  const minMembers = minMembersFor(nodes.length);
  const depth = chooseGroupDepth(nodes, minMembers);
  const groups = groupAtDepth(nodes, depth);

  // Tiebreak on the key so equal-sized folders keep a stable order regardless
  // of how the server serialized its nodes.
  const sized = [...groups.entries()]
    .map(([key, ns]) => ({ key, nodes: ns }))
    .sort((a, b) => b.nodes.length - a.nodes.length || a.key.localeCompare(b.key));

  const folders = [];
  const strays = [];
  for (const g of sized) {
    if (g.nodes.length >= minMembers && folders.length < MAX_FOLDERS) {
      folders.push({ key: g.key, name: g.key, nodes: g.nodes, index: folders.length });
    } else {
      strays.push(...g.nodes);
    }
  }

  return { depth, folders, strays };
}

// Greedy bin-pack: each folder goes to whichever arm would be least full
// after taking it, measured relative to that arm's weight.
export function assignFoldersToArms(folders) {
  const arms = ARMS.map((a, i) => ({ ...a, index: i, folders: [], load: 0 }));

  for (const f of folders) {
    let bestArm = arms[0];
    let bestFill = Infinity;
    for (const a of arms) {
      const fill = (a.load + f.nodes.length) / a.weight;
      if (fill < bestFill) {
        bestFill = fill;
        bestArm = a;
      }
    }
    bestArm.folders.push(f);
    bestArm.load += f.nodes.length;
    f.armIndex = bestArm.index;
  }

  return arms;
}

// Lay each arm's folders out as contiguous segments running from the bar
// outward, biggest first — so the largest folders sit nearest the core and
// read as central to the codebase.
//
// Segments are allocated in AREA fraction, not angle. Radius grows
// exponentially with theta (r = rcore * e^(B*theta)), so splitting theta
// evenly would pack most of the codebase into the inner disc and blow the
// core out into a solid blob. Area fraction is also the natural currency
// here: a folder's node count is exactly what should map to disc area, so
// the allocation needs no fudge exponent.
export function allocateSegments(arms) {
  for (const arm of arms) {
    arm.folders.sort((a, b) => b.nodes.length - a.nodes.length || a.key.localeCompare(b.key));
    const total = arm.folders.reduce((s, f) => s + f.nodes.length, 0) || 1;

    let cursor = 0;
    for (const f of arm.folders) {
      const span = f.nodes.length / total;
      f.u0 = cursor;
      f.u1 = cursor + span;
      cursor += span;
    }
  }
  return arms;
}
