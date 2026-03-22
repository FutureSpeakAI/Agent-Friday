/**
 * desktop-viz/materials.ts — Texture generators, material factory, and helpers.
 */

import * as THREE from 'three';

// ── Texture Generators ──────────────────────────────────────────────────────

export function createGlowTexture(): THREE.CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = 64; canvas.height = 64;
  const ctx = canvas.getContext('2d')!;
  const gradient = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
  gradient.addColorStop(0, 'rgba(255,255,255,1)');
  gradient.addColorStop(0.2, 'rgba(255,255,255,0.8)');
  gradient.addColorStop(0.5, 'rgba(255,255,255,0.2)');
  gradient.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(canvas);
}

export function createCloudTexture(): THREE.CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = 128; canvas.height = 128;
  const ctx = canvas.getContext('2d')!;
  const gradient = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  gradient.addColorStop(0, 'rgba(255,255,255,0.15)');
  gradient.addColorStop(0.5, 'rgba(255,255,255,0.05)');
  gradient.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(canvas);
}

// ── Material Factory ────────────────────────────────────────────────────────

export function createMaterial(
  type: 'line' | 'mesh',
  color: number,
  opacity: number,
  additive = true,
  wireframe = false,
  dashed = false,
): THREE.Material {
  let mat: THREE.Material;
  if (dashed) {
    mat = new THREE.LineDashedMaterial({ color, dashSize: 0.2, gapSize: 0.1 });
  } else if (type === 'line') {
    mat = new THREE.LineBasicMaterial({ color });
  } else {
    mat = new THREE.MeshBasicMaterial({ color, wireframe });
  }
  (mat as any).transparent = true;
  (mat as any).opacity = opacity;
  mat.userData = { baseOpacity: opacity, isAccent: false };
  if (additive) {
    (mat as any).blending = THREE.AdditiveBlending;
    (mat as any).depthWrite = false;
  }
  return mat;
}

// ── Helpers ─────────────────────────────────────────────────────────────────

export function setGroupOpacity(group: THREE.Group, mult: number) {
  group.traverse((child: any) => {
    if (child?.material?.userData?.baseOpacity !== undefined) {
      child.material.opacity = child.material.userData.baseOpacity * mult;
    }
  });
}

export function smoothstep(x: number): number { return x * x * (3 - 2 * x); }
