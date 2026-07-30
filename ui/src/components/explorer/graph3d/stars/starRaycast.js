import * as THREE from 'three';

/**
 * Per-instance sphere picking for shader-billboarded stars.
 *
 * THREE.InstancedMesh.raycast walks instanceMatrix and raycasts the CPU-side
 * geometry. Our geometry is a unit quad that only faces the camera because the
 * vertex shader rotates it, and instanceMatrix is identity — so the stock path
 * would miss almost everything.
 *
 * The hit radius is resolved in SCREEN space, not world space. The vertex
 * shader clamps each star to a minimum pixel size, so a distant 4-unit Method
 * star is drawn far larger than its world radius; picking against the world
 * radius would leave a 2px target under a visibly larger dot. Working in
 * pixels keeps the hitbox matched to what the user actually sees at any zoom.
 *
 * @param params live object — { pixelScale, minPixels } — refreshed each frame
 *               from the camera, so this stays correct as the view changes.
 */
export function attachStarRaycast(mesh, offsets, worldRadii, params) {
  const localRay = new THREE.Ray();
  const inverse = new THREE.Matrix4();
  const local = new THREE.Vector3();
  const world = new THREE.Vector3();

  mesh.raycast = function raycast(raycaster, intersects) {
    const n = Math.min(this.count ?? 0, worldRadii.length);
    if (n === 0) return;

    inverse.copy(this.matrixWorld).invert();
    localRay.copy(raycaster.ray).applyMatrix4(inverse);

    const pixelScale = params.pixelScale || 500;
    const minPixels = params.minPixels || 6;

    for (let i = 0; i < n; i++) {
      local.set(offsets[i * 3], offsets[i * 3 + 1], offsets[i * 3 + 2]);

      // The disc group is rotation-only (no scale), so local distance equals
      // world distance and can drive the pixel->world conversion directly.
      const depth = localRay.origin.distanceTo(local);
      const minWorld = (minPixels * depth) / pixelScale;
      const r = Math.max(worldRadii[i], minWorld);

      if (localRay.distanceSqToPoint(local) > r * r) continue;

      world.copy(local).applyMatrix4(this.matrixWorld);
      const distance = raycaster.ray.origin.distanceTo(world);
      if (distance < raycaster.near || distance > raycaster.far) continue;

      intersects.push({
        distance,
        point: world.clone(),
        instanceId: i,
        object: this,
      });
    }
  };
}
