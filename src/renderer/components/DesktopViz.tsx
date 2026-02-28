/**
 * DesktopViz.tsx — Holographic Neural Hub Visualization
 *
 * Designed by Gemini, adapted for Agent Friday's AGI OS.
 * 13 evolution structures, mood-reactive, audio-reactive,
 * post-processed with bloom + holographic shader.
 *
 * Replaces the old FridayCore.tsx as the base desktop visual layer.
 */

import { useRef, useEffect, useCallback, Component, ErrorInfo, ReactNode } from 'react';
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import type { MoodPalette } from '../contexts/MoodContext';
import type { SemanticState } from './FridayCore';

// ── Types ────────────────────────────────────────────────────────────────────

export interface DesktopVizProps {
  getLevels?: () => { mic: number; output: number };
  semanticState?: SemanticState;
  isSpeaking?: boolean;
  isListening?: boolean;
  moodPalette?: MoodPalette;
  moodIntensity?: number;
  moodTurbulence?: number;
  /** Index into EVOLUTION_PATH (0–12). Driven by useDesktopEvolution hook. */
  evolutionIndex?: number;
  /** 0–1 blend between current and next structure (for gradual week-long transitions). */
  transitionBlend?: number;
}

// ── Mood Mapping ─────────────────────────────────────────────────────────────

interface MoodConfig {
  baseColor: number;
  accentColor: number;
  rotationSpeed: number;
  bloomStrength: number;
  particleSpeedScale: number;
  grain: number;
}

const MOODS: Record<string, MoodConfig> = {
  LISTENING:  { baseColor: 0x00d2ff, accentColor: 0x8a2be2, rotationSpeed: 0.001, bloomStrength: 0.8, particleSpeedScale: 1.0, grain: 0.035 },
  REASONING:  { baseColor: 0x4b0082, accentColor: 0x00ffff, rotationSpeed: 0.003, bloomStrength: 0.6, particleSpeedScale: 0.5, grain: 0.02 },
  EXECUTING:  { baseColor: 0xffaa00, accentColor: 0xff3300, rotationSpeed: 0.008, bloomStrength: 1.2, particleSpeedScale: 1.8, grain: 0.05 },
  SUB_AGENTS: { baseColor: 0xffaa00, accentColor: 0xff3300, rotationSpeed: 0.008, bloomStrength: 1.2, particleSpeedScale: 1.8, grain: 0.05 },
  EXCITED:    { baseColor: 0xffffff, accentColor: 0x00e5ff, rotationSpeed: 0.015, bloomStrength: 1.8, particleSpeedScale: 2.5, grain: 0.08 },
  CALM:       { baseColor: 0x001133, accentColor: 0x0055aa, rotationSpeed: 0.0002, bloomStrength: 0.4, particleSpeedScale: 0.2, grain: 0.05 },
};

// ── Evolution Path ───────────────────────────────────────────────────────────

export const EVOLUTION_PATH = [
  { id: 'CUBES',        name: 'GENESIS LATTICE' },
  { id: 'ICOSAHEDRON',  name: 'SACRED SPHERE' },
  { id: 'NETWORK',      name: 'SHANNON NETWORK' },
  { id: 'DOME',         name: 'GEODESIC CATHEDRAL' },
  { id: 'ASTROLABE',    name: 'LOVELACE ASTROLABE' },
  { id: 'TESSERACT',    name: 'VON NEUMANN TESSERACT' },
  { id: 'QUANTUM',      name: 'DIRAC PROBABILITY' },
  { id: 'MANDELBROT',   name: 'MANDELBROT SET' },
  { id: 'MOBIUS',       name: 'TURING MOBIUS' },
  { id: 'GRID',         name: 'OCEAN OF LIGHT' },
  { id: 'CABLES',       name: 'FIBONACCI NERVE' },
  { id: 'NONE',         name: 'TRANSCENDENCE' },
  { id: 'EDEN',         name: 'GIGA EARTH (REZ)' },
];

// ── Holographic Shader ───────────────────────────────────────────────────────

const HolographicShader = {
  uniforms: {
    tDiffuse: { value: null as THREE.Texture | null },
    time: { value: 0.0 },
    amount: { value: 0.003 },
    angle: { value: 0.0 },
    grainAmount: { value: 0.04 },
  },
  vertexShader: /* glsl */ `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse;
    uniform float time;
    uniform float amount;
    uniform float angle;
    uniform float grainAmount;
    varying vec2 vUv;
    float rand(vec2 co) {
      return fract(sin(dot(co.xy, vec2(12.9898, 78.233))) * 43758.5453);
    }
    void main() {
      vec2 offset = amount * vec2(cos(angle + vUv.y * 2.0), sin(angle + vUv.x * 2.0));
      vec4 cr = texture2D(tDiffuse, vUv + offset);
      vec4 cga = texture2D(tDiffuse, vUv);
      vec4 cb = texture2D(tDiffuse, vUv - offset);
      vec4 finalColor = vec4(cr.r, cga.g, cb.b, cga.a);
      finalColor.rgb += (rand(vUv + time) - 0.5) * grainAmount;
      finalColor.rgb -= sin(vUv.y * 800.0 + time * 2.0) * 0.01;
      gl_FragColor = finalColor;
    }
  `,
};

// ── Error Boundary ───────────────────────────────────────────────────────────

class VizErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  state = { hasError: false };
  static getDerivedStateFromError() { return { hasError: true }; }
  componentDidCatch(err: Error, info: ErrorInfo) {
    console.error('[DesktopViz] Error boundary caught:', err, info);
  }
  render() { return this.state.hasError ? null : this.props.children; }
}

// ── Helper: texture generators ───────────────────────────────────────────────

function createGlowTexture(): THREE.CanvasTexture {
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

function createCloudTexture(): THREE.CanvasTexture {
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

// ── Helper: material factory ─────────────────────────────────────────────────

function createMaterial(
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

function setGroupOpacity(group: THREE.Group, mult: number) {
  group.traverse((child: any) => {
    if (child?.material?.userData?.baseOpacity !== undefined) {
      child.material.opacity = child.material.userData.baseOpacity * mult;
    }
  });
}

function smoothstep(x: number): number { return x * x * (3 - 2 * x); }

// ── Inner Component ──────────────────────────────────────────────────────────

function DesktopVizInner({
  getLevels,
  semanticState = 'LISTENING',
  isSpeaking = false,
  isListening = false,
  moodPalette,
  moodIntensity = 0.4,
  moodTurbulence = 0.2,
  evolutionIndex = 0,
  transitionBlend = 1.0,
}: DesktopVizProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cleanupRef = useRef<(() => void) | null>(null);

  // Refs for props so RAF closures always see latest
  const propsRef = useRef({
    getLevels, semanticState, isSpeaking, isListening,
    moodPalette, moodIntensity, moodTurbulence,
    evolutionIndex, transitionBlend,
  });
  useEffect(() => {
    propsRef.current = {
      getLevels, semanticState, isSpeaking, isListening,
      moodPalette, moodIntensity, moodTurbulence,
      evolutionIndex, transitionBlend,
    };
  });

  // Track evolution changes to trigger transitions
  const prevEvoRef = useRef(evolutionIndex);

  const initScene = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;

    // ── Scene Setup ──────────────────────────────────────────────────────────
    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x000205, 0.012);
    const glowTexture = createGlowTexture();
    const cloudTexture = createCloudTexture();

    const camera = new THREE.PerspectiveCamera(60, container.clientWidth / container.clientHeight, 0.1, 1000);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000103, 1);
    container.appendChild(renderer.domElement);

    // Post-processing
    const renderPass = new RenderPass(scene, camera);
    const bloomPass = new UnrealBloomPass(
      new THREE.Vector2(container.clientWidth, container.clientHeight), 1.2, 0.6, 0.15,
    );
    const holoPass = new ShaderPass(HolographicShader as any);
    const composer = new EffectComposer(renderer);
    composer.addPass(renderPass);
    composer.addPass(bloomPass);
    composer.addPass(holoPass);

    // ── State ────────────────────────────────────────────────────────────────
    const clock = new THREE.Clock();
    const structures: Record<string, THREE.Group> = {};

    let currentStructureId = EVOLUTION_PATH[evolutionIndex]?.id || 'CUBES';
    let targetStructureId = currentStructureId;
    let transitionProgress = 1.0;
    let metamorphosisFlash = 0.0;

    // Mood lerp values
    const moodLerp = {
      baseColor: new THREE.Color(MOODS.LISTENING.baseColor),
      accentColor: new THREE.Color(MOODS.LISTENING.accentColor),
      rotationSpeed: MOODS.LISTENING.rotationSpeed,
      bloomStrength: MOODS.LISTENING.bloomStrength,
      particleSpeedScale: MOODS.LISTENING.particleSpeedScale,
      grain: MOODS.LISTENING.grain,
    };

    // Audio
    let idleFactor = 0.4;
    let lastSoundTime = -10;
    const audioData = { low: 0, mid: 0, high: 0, total: 0 };

    // Camera targets
    const targetCamPos = new THREE.Vector3(0, 5, 15);
    const targetCamLook = new THREE.Vector3(0, 0, 0);
    const baseCamPos = new THREE.Vector3(0, 5, 15);
    camera.position.copy(targetCamPos);
    camera.lookAt(targetCamLook);

    // Structure-specific refs
    const coreCubes: THREE.Mesh[] = [];
    const matrixLines: THREE.Line[] = [];
    let gridOcean: THREE.Points | null = null;
    let mandelbrotSystem: THREE.Points | null = null;
    let tesseractLines: THREE.LineSegments | null = null;
    const astrolabeRings: THREE.Group[] = [];
    const shannonNodes: { pos: THREE.Vector3; velocity: THREE.Vector3 }[] = [];
    let shannonLines: THREE.LineSegments | null = null;
    let mobiusSystem: THREE.Points | null = null;
    const quantumRings: THREE.LineLoop[] = [];
    let abyssParticles: THREE.Points | null = null;
    const cathedralRings: THREE.Mesh[] = [];
    const edenDebris: THREE.Object3D[] = [];
    let edenPlayer: THREE.Group | null = null;
    let edenLady: THREE.Object3D | null = null;

    // Background
    let particleSystem: THREE.Points | null = null;
    const energyLines: THREE.Line[] = [];
    const nebulaClouds: THREE.Sprite[] = [];

    // ── Build Background ─────────────────────────────────────────────────────
    function buildBackground() {
      const particleCount = 800;
      const positions = new Float32Array(particleCount * 3);
      const colors = new Float32Array(particleCount * 3);
      const particleData: any[] = [];

      for (let i = 0; i < particleCount; i++) {
        const radius = 8 + Math.random() * 15;
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(Math.random() * 2 - 1);
        positions[i * 3] = radius * Math.sin(phi) * Math.cos(theta);
        positions[i * 3 + 1] = radius * Math.sin(phi) * Math.sin(theta);
        positions[i * 3 + 2] = radius * Math.cos(phi);
        particleData.push({ radius, baseRadius: radius, theta, basePhi: phi, phi, speed: 0.01 + Math.random() * 0.04 });
      }

      const pGeom = new THREE.BufferGeometry();
      pGeom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      pGeom.setAttribute('color', new THREE.BufferAttribute(colors, 3));
      particleSystem = new THREE.Points(pGeom, new THREE.PointsMaterial({
        size: 0.4, map: glowTexture, vertexColors: true, transparent: true,
        opacity: 0.8, blending: THREE.AdditiveBlending, depthWrite: false,
      }));
      particleSystem.userData.data = particleData;
      scene.add(particleSystem);

      // Energy flares
      for (let i = 0; i < 20; i++) {
        const start = new THREE.Vector3((Math.random() - 0.5) * 8, (Math.random() - 0.5) * 8, (Math.random() - 0.5) * 8);
        const end = new THREE.Vector3((Math.random() - 0.5) * 30, (Math.random() - 0.5) * 30, (Math.random() - 0.5) * 30);
        const mid1 = start.clone().lerp(end, 0.3).add(new THREE.Vector3((Math.random() - 0.5) * 10, (Math.random() - 0.5) * 10, (Math.random() - 0.5) * 10));
        const mid2 = start.clone().lerp(end, 0.7).add(new THREE.Vector3((Math.random() - 0.5) * 10, (Math.random() - 0.5) * 10, (Math.random() - 0.5) * 10));
        const curve = new THREE.CatmullRomCurve3([start, mid1, mid2, end]);
        const line = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints(curve.getPoints(50)),
          new THREE.LineBasicMaterial({ color: 0x00ffff, transparent: true, opacity: 0.05, blending: THREE.AdditiveBlending }),
        );
        line.userData = { pulse: Math.random() * Math.PI, pulseSpeed: 0.05 + Math.random() * 0.1, intensity: 0 };
        scene.add(line);
        energyLines.push(line);
      }

      // Nebula clouds
      for (let i = 0; i < 15; i++) {
        const material = new THREE.SpriteMaterial({
          map: cloudTexture, color: 0x00ffff, transparent: true, opacity: 0.1,
          blending: THREE.AdditiveBlending, depthWrite: false,
        });
        const sprite = new THREE.Sprite(material);
        const scale = 30 + Math.random() * 50;
        sprite.scale.set(scale, scale, 1);
        sprite.position.set((Math.random() - 0.5) * 100, (Math.random() - 0.5) * 100, -50 - Math.random() * 80);
        sprite.userData = { isAccent: Math.random() > 0.5, speed: (Math.random() - 0.5) * 0.001 };
        scene.add(sprite);
        nebulaClouds.push(sprite);
      }
    }

    // ── Build ALL 13 Structures ──────────────────────────────────────────────

    function buildAllStructures() {
      // 1. CUBES — 3x3x3 grid logo with 15% random dropout
      const gCubes = new THREE.Group();
      const boxMat = createMaterial('mesh', 0x00ffff, 0.85, false);  // Solid faces, NOT additive
      const edgeMat = createMaterial('line', 0x00ffff, 0.8, true);
      edgeMat.userData.isAccent = true;
      const boxGeo = new THREE.BoxGeometry(1.4, 1.4, 1.4);
      const edgeGeo = new THREE.EdgesGeometry(boxGeo);
      const gridSize = 3;
      const spacing = 1.6;
      const cubeOffset = (gridSize * spacing) / 2 - (spacing / 2);
      for (let x = 0; x < gridSize; x++) {
        for (let y = 0; y < gridSize; y++) {
          for (let z = 0; z < gridSize; z++) {
            if (Math.random() > 0.85) continue;  // 15% dropout
            const mesh = new THREE.Mesh(boxGeo, boxMat.clone());
            mesh.add(new THREE.LineSegments(edgeGeo, edgeMat.clone()));
            const posX = (x * spacing) - cubeOffset;
            const posY = (y * spacing) - cubeOffset;
            const posZ = (z * spacing) - cubeOffset;
            mesh.position.set(posX, posY, posZ);
            const dir = new THREE.Vector3(posX, posY, posZ).normalize();
            if (dir.length() === 0) dir.set(0, 1, 0);
            mesh.userData = { baseX: posX, baseY: posY, baseZ: posZ, dir, rx: Math.random() * Math.PI * 2, speed: 0.5 + Math.random() * 0.5 };
            gCubes.add(mesh);
            coreCubes.push(mesh);
          }
        }
      }
      gCubes.scale.set(1.5, 1.5, 1.5);
      structures['CUBES'] = gCubes;

      // 2. ICOSAHEDRON
      const gIco = new THREE.Group();
      gIco.add(new THREE.Mesh(new THREE.IcosahedronGeometry(5.0, 3), createMaterial('mesh', 0x00ffff, 0.15, true, true)));
      const midIco = createMaterial('mesh', 0x00ffff, 0.3, true, true);
      midIco.userData.isAccent = true;
      gIco.add(new THREE.Mesh(new THREE.IcosahedronGeometry(3.5, 2), midIco));
      gIco.add(new THREE.Mesh(new THREE.IcosahedronGeometry(2.0, 1), createMaterial('mesh', 0xffffff, 0.6, true, true)));
      structures['ICOSAHEDRON'] = gIco;

      // 3. DOME (Cathedral)
      const gDome = new THREE.Group();
      gDome.add(new THREE.Mesh(new THREE.SphereGeometry(35, 48, 32, 0, Math.PI * 2, 0, Math.PI / 2), createMaterial('mesh', 0x00ffff, 0.1, true, true)));
      for (let i = 0; i < 8; i++) {
        const angle = (i / 8) * Math.PI * 2;
        const pillar = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.8, 50, 8), createMaterial('mesh', 0x00ffff, 0.15, true, true));
        pillar.position.set(Math.cos(angle) * 18, -10, Math.sin(angle) * 18);
        gDome.add(pillar);
      }
      const chMat = createMaterial('mesh', 0xffffff, 0.5, true, true);
      chMat.userData.isAccent = true;
      for (let i = 0; i < 6; i++) {
        const crystal = new THREE.Mesh(new THREE.OctahedronGeometry(4 - i * 0.5, 1), chMat.clone());
        crystal.position.y = 25 - i * 4;
        crystal.rotation.y = i * Math.PI / 4;
        gDome.add(crystal);
        cathedralRings.push(crystal);
      }
      const abyssGeo = new THREE.BufferGeometry();
      const aPts: number[] = [];
      for (let i = 0; i < 2000; i++) {
        const r = Math.random() * 40;
        const theta = Math.random() * Math.PI * 2;
        const depth = -10 - Math.pow(r, 1.2) * 0.4;
        aPts.push(r * Math.cos(theta), depth, r * Math.sin(theta));
      }
      abyssGeo.setAttribute('position', new THREE.Float32BufferAttribute(aPts, 3));
      abyssParticles = new THREE.Points(abyssGeo, new THREE.PointsMaterial({
        size: 0.5, map: glowTexture, color: 0x00ffff, transparent: true,
        opacity: 0.5, blending: THREE.AdditiveBlending, depthWrite: false,
      }));
      abyssParticles.userData = { isAccent: true };
      gDome.add(abyssParticles);
      structures['DOME'] = gDome;

      // 4. CABLES (Fibonacci Nerve)
      const gCables = new THREE.Group();
      const cableMat = createMaterial('mesh', 0x00ffff, 0.15, true, false);
      cableMat.userData.isAccent = true;
      const phiFib = Math.PI * (3.0 - Math.sqrt(5.0));
      for (let i = 0; i < 80; i++) {
        const y = 1 - (i / 79) * 2;
        const radius = Math.sqrt(1 - y * y);
        const theta = phiFib * i;
        const start = new THREE.Vector3(Math.cos(theta) * radius * 30, y * 30, Math.sin(theta) * radius * 30);
        const end = new THREE.Vector3(0, 0, 0);
        const mid = start.clone().lerp(end, 0.5).applyAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 1.5);
        gCables.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3([start, mid, end]), 50, 0.15, 6, false), cableMat.clone()));
      }
      structures['CABLES'] = gCables;

      // 5. GRID (Ocean of Light)
      const gGrid = new THREE.Group();
      const oceanGeo = new THREE.PlaneGeometry(100, 100, 80, 80);
      oceanGeo.rotateX(-Math.PI / 2);
      gridOcean = new THREE.Points(oceanGeo, new THREE.PointsMaterial({
        size: 0.35, map: glowTexture, transparent: true, opacity: 0.5,
        blending: THREE.AdditiveBlending, depthWrite: false,
      }));
      (gridOcean.material as THREE.PointsMaterial).userData = { baseOpacity: 0.5, isAccent: true };
      gridOcean.userData.baseY = new Float32Array(oceanGeo.attributes.position.count);
      for (let i = 0; i < oceanGeo.attributes.position.count; i++) {
        gridOcean.userData.baseY[i] = oceanGeo.attributes.position.getY(i);
      }
      gGrid.add(gridOcean);
      structures['GRID'] = gGrid;

      // 6. MANDELBROT SET
      const gMandel = new THREE.Group();
      const mPts: number[] = [];
      const mCols: number[] = [];
      const mData: any[] = [];
      const maxIter = 40;
      for (let x = -2.1; x < 0.8; x += 0.012) {
        for (let y = -1.2; y < 1.2; y += 0.012) {
          let cx = x, cy = y, zx = 0, zy = 0, iter = 0;
          while (zx * zx + zy * zy < 4 && iter < maxIter) {
            const tmp = zx * zx - zy * zy + cx;
            zy = 2 * zx * zy + cy;
            zx = tmp;
            iter++;
          }
          if (iter < maxIter && iter > 2) {
            const smooth = iter + 1 - Math.log(Math.log(Math.sqrt(zx * zx + zy * zy))) / Math.log(2);
            const height = (smooth / maxIter) * 6.0;
            mPts.push((x + 0.65) * 10, height - 3.0, y * 10);
            mCols.push(1, 1, 1);
            mData.push({ baseX: (x + 0.65) * 10, baseZ: y * 10, baseY: height - 3.0, iterRatio: smooth / maxIter });
          }
        }
      }
      const mGeom = new THREE.BufferGeometry();
      mGeom.setAttribute('position', new THREE.Float32BufferAttribute(mPts, 3));
      mGeom.setAttribute('color', new THREE.Float32BufferAttribute(mCols, 3));
      mandelbrotSystem = new THREE.Points(mGeom, new THREE.PointsMaterial({
        size: 0.25, map: glowTexture, vertexColors: true, transparent: true,
        opacity: 0.4, blending: THREE.AdditiveBlending, depthWrite: false,
      }));
      mandelbrotSystem.userData.data = mData;
      gMandel.add(mandelbrotSystem);
      structures['MANDELBROT'] = gMandel;

      // 7. ASTROLABE
      const gAstrolabe = new THREE.Group();
      const astroMatSolid = createMaterial('mesh', 0x00ffff, 0.15, true, false);
      const astroMatDash = createMaterial('line', 0x00ffff, 0.6, true, false, true);
      astroMatDash.userData.isAccent = true;
      for (let i = 1; i <= 8; i++) {
        const ringGroup = new THREE.Group();
        const radius = i * 2.0;
        ringGroup.add(new THREE.Mesh(new THREE.TorusGeometry(radius, 0.05, 8, 80), astroMatSolid.clone()));
        const dashedEdges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.TorusGeometry(radius, 0.2, 4, 40)), astroMatDash.clone());
        dashedEdges.computeLineDistances();
        ringGroup.add(dashedEdges);
        ringGroup.rotation.x = Math.random() * Math.PI;
        ringGroup.rotation.y = Math.random() * Math.PI;
        ringGroup.userData = { rxSpeed: (Math.random() - 0.5) * 0.015, rySpeed: (Math.random() - 0.5) * 0.015 };
        gAstrolabe.add(ringGroup);
        astrolabeRings.push(ringGroup);
      }
      structures['ASTROLABE'] = gAstrolabe;

      // 8. TESSERACT
      const gTesseract = new THREE.Group();
      const tessMat = createMaterial('line', 0x00ffff, 0.8, true, false);
      tessMat.userData.isAccent = true;
      const tessNodesMat = new THREE.PointsMaterial({
        size: 1.0, map: glowTexture, color: 0x00ffff, transparent: true,
        opacity: 0.5, blending: THREE.AdditiveBlending, depthWrite: false,
      });
      const tPts4D: { x: number; y: number; z: number; w: number }[] = [];
      for (let i = 0; i < 16; i++) {
        tPts4D.push({ x: (i & 1) ? 1 : -1, y: (i & 2) ? 1 : -1, z: (i & 4) ? 1 : -1, w: (i & 8) ? 1 : -1 });
      }
      const tEdges: number[] = [];
      for (let i = 0; i < 16; i++) {
        for (let j = i + 1; j < 16; j++) {
          if (Math.abs(tPts4D[i].x - tPts4D[j].x) + Math.abs(tPts4D[i].y - tPts4D[j].y) +
              Math.abs(tPts4D[i].z - tPts4D[j].z) + Math.abs(tPts4D[i].w - tPts4D[j].w) === 2) {
            tEdges.push(i, j);
          }
        }
      }
      const tessGeo = new THREE.BufferGeometry();
      tessGeo.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(tEdges.length * 3), 3));
      tesseractLines = new THREE.LineSegments(tessGeo, tessMat);
      tesseractLines.userData = { pts4D: tPts4D, edges: tEdges, angleXW: 0, angleYW: 0 };
      const tessNodesGeo = new THREE.BufferGeometry();
      tessNodesGeo.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(16 * 3), 3));
      const tessNodes = new THREE.Points(tessNodesGeo, tessNodesMat);
      tessNodes.userData.isAccent = true;
      gTesseract.add(tesseractLines);
      gTesseract.add(tessNodes);
      gTesseract.scale.set(3, 3, 3);
      structures['TESSERACT'] = gTesseract;

      // 9. NETWORK (Shannon)
      const gNet = new THREE.Group();
      const netNodeMat = new THREE.PointsMaterial({
        size: 0.6, map: glowTexture, color: 0x00ffff, transparent: true,
        opacity: 0.8, blending: THREE.AdditiveBlending,
      });
      const netLineMat = createMaterial('line', 0x00ffff, 0.2, true, false);
      netLineMat.userData.isAccent = true;
      const netPts: THREE.Vector3[] = [];
      for (let i = 0; i < 120; i++) {
        const p = new THREE.Vector3((Math.random() - 0.5) * 20, (Math.random() - 0.5) * 20, (Math.random() - 0.5) * 20);
        netPts.push(p);
        shannonNodes.push({ pos: p, velocity: new THREE.Vector3((Math.random() - 0.5) * 0.015, (Math.random() - 0.5) * 0.015, (Math.random() - 0.5) * 0.015) });
      }
      gNet.add(new THREE.Points(new THREE.BufferGeometry().setFromPoints(netPts), netNodeMat));
      shannonLines = new THREE.LineSegments(new THREE.BufferGeometry(), netLineMat);
      gNet.add(shannonLines);
      structures['NETWORK'] = gNet;

      // 10. MOBIUS
      const gMobius = new THREE.Group();
      const mobPts: { u: number; v: number }[] = [];
      for (let u = 0; u < Math.PI * 2; u += 0.04) {
        for (let v = -1; v <= 1; v += 0.15) mobPts.push({ u, v });
      }
      const mobGeo = new THREE.BufferGeometry();
      mobGeo.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(mobPts.length * 3), 3));
      mobiusSystem = new THREE.Points(mobGeo, new THREE.PointsMaterial({
        size: 0.25, map: glowTexture, color: 0x00ffff, transparent: true,
        opacity: 0.8, blending: THREE.AdditiveBlending, depthWrite: false,
      }));
      mobiusSystem.userData = { data: mobPts, isAccent: true };
      gMobius.add(mobiusSystem);
      gMobius.scale.set(4, 4, 4);
      structures['MOBIUS'] = gMobius;

      // 11. QUANTUM (Massive Rainbow Cloud)
      const gQuantum = new THREE.Group();
      gQuantum.add(new THREE.Points(
        new THREE.SphereGeometry(3.5, 64, 64),
        new THREE.PointsMaterial({ size: 0.4, map: glowTexture, color: 0xffffff, transparent: true, opacity: 0.4, blending: THREE.AdditiveBlending, depthWrite: false }),
      ));
      const numQRings = 30;
      for (let i = 0; i < numQRings; i++) {
        const qLineMat = createMaterial('line', 0xffffff, 0.5, true, false);
        qLineMat.userData.isAccent = true;
        const qGeo = new THREE.BufferGeometry();
        qGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(300 * 3), 3));
        const qLine = new THREE.LineLoop(qGeo, qLineMat as THREE.LineBasicMaterial);
        qLine.rotation.x = Math.random() * Math.PI;
        qLine.rotation.y = Math.random() * Math.PI;
        qLine.userData = { radius: 6 + i * 0.4, waveSpeed: 2 + Math.random() * 4, colorPhase: i / numQRings };
        gQuantum.add(qLine);
        quantumRings.push(qLine);
      }
      structures['QUANTUM'] = gQuantum;

      // 12. NONE (Transcendence — Matrix rain)
      const gNone = new THREE.Group();
      const lineMat = createMaterial('line', 0x00ffff, 0.2, true, false);
      lineMat.userData.isAccent = true;
      for (let i = 0; i < 100; i++) {
        const x = (Math.random() - 0.5) * 40;
        const z = (Math.random() - 0.5) * 40;
        const y = (Math.random() - 0.5) * 40;
        const line = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(x, y - 5, z), new THREE.Vector3(x, y + 5, z)]),
          lineMat.clone(),
        );
        line.userData = { speed: 0.01 + Math.random() * 0.02, baseY: y };
        gNone.add(line);
        matrixLines.push(line);
      }
      structures['NONE'] = gNone;

      // 13. GIGA EARTH (REZ Tribute) — Box tunnel, orange sphere, energy spines, geometric player
      const gEden = new THREE.Group();

      // Box tunnel with solid backside
      const tunnelGroup = new THREE.Group();
      const edenTunnelGeo = new THREE.BoxGeometry(40, 40, 200, 4, 4, 20);
      const tunnelWireMat = createMaterial('line', 0xff00ff, 0.3, true, false);
      tunnelWireMat.userData.isAccent = true;
      const tunnelWire = new THREE.LineSegments(
        new THREE.EdgesGeometry(edenTunnelGeo),
        tunnelWireMat,
      );
      const tunnelSolidMat = new THREE.MeshBasicMaterial({ color: 0x050515, side: THREE.BackSide });
      (tunnelSolidMat as any).transparent = true;
      (tunnelSolidMat as any).opacity = 1.0;
      tunnelSolidMat.userData = { baseOpacity: 1.0, isTunnelSolid: true };
      const tunnelSolid = new THREE.Mesh(edenTunnelGeo, tunnelSolidMat);
      tunnelGroup.add(tunnelWire);
      tunnelGroup.add(tunnelSolid);
      gEden.add(tunnelGroup);

      // Giga Earth sphere (orange, segmented, with pole holes)
      const sphereGeo = new THREE.SphereGeometry(6, 24, 16, 0, Math.PI * 2, 0.2, Math.PI - 0.4);
      const sphereMat = new THREE.MeshBasicMaterial({ color: 0xff6600, transparent: true, opacity: 0.9 });
      sphereMat.userData = { baseOpacity: 0.9, isBossSphere: true };
      edenLady = new THREE.Mesh(sphereGeo, sphereMat);
      const sphereWire = new THREE.WireframeGeometry(sphereGeo);
      const sphereWireMat = new THREE.LineBasicMaterial({ color: 0x550000, transparent: true, opacity: 0.6 });
      sphereWireMat.userData = { baseOpacity: 0.6, isBossWire: true };
      (edenLady as THREE.Mesh).add(new THREE.LineSegments(sphereWire, sphereWireMat));
      gEden.add(edenLady);

      // Vertical energy spines
      const spineMat = createMaterial('line', 0xff3300, 0.8, true, false);
      for (let i = 0; i < 15; i++) {
        const sx = (Math.random() - 0.5) * 4;
        const sz = (Math.random() - 0.5) * 4;
        gEden.add(new THREE.Line(
          new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(sx, -50, sz), new THREE.Vector3(sx, 50, sz)]),
          spineMat,
        ));
      }

      // Geometric player (cylinder body + sphere head + ring)
      edenPlayer = new THREE.Group();
      const pBodyMat = new THREE.MeshBasicMaterial({ color: 0xffffff });
      pBodyMat.userData = { baseOpacity: 1.0, isPlayerBody: true };
      edenPlayer.add(new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.3, 1.5, 8), pBodyMat));
      const pHead = new THREE.Mesh(new THREE.SphereGeometry(0.2, 8, 8), pBodyMat);
      pHead.position.y = 0.9;
      edenPlayer.add(pHead);
      const ringPts: THREE.Vector3[] = [];
      for (let ri = 0; ri <= 32; ri++) {
        const th = (ri / 32) * Math.PI * 2;
        ringPts.push(new THREE.Vector3(Math.cos(th) * 1.5, Math.sin(th) * 1.5, 0));
      }
      const pRing = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(ringPts),
        createMaterial('line', 0x00ffff, 0.8, true, false),
      );
      pRing.rotation.x = Math.PI / 2;
      edenPlayer.add(pRing);
      gEden.add(edenPlayer);

      // Line-based data streaks (debris)
      const edenDebrisMat = createMaterial('mesh', 0x00ffff, 0.4, true, true);
      for (let i = 0; i < 60; i++) {
        const dGeo = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(0, 0, 0),
          new THREE.Vector3(0, 0, -2 - Math.random() * 3),
        ]);
        const dLine = new THREE.Line(dGeo, edenDebrisMat);
        dLine.position.set(
          (Math.random() - 0.5) * 35,
          (Math.random() - 0.5) * 35,
          (Math.random() - 0.5) * 100,
        );
        dLine.userData = { speed: 1.0 + Math.random() * 3.0 };
        gEden.add(dLine);
        edenDebris.push(dLine as any);
      }
      structures['EDEN'] = gEden;
    }

    // ── Color update ─────────────────────────────────────────────────────────

    function updateGroupColors(group: THREE.Group) {
      group.traverse((child: any) => {
        if (child?.material?.userData) {
          const targetColor = (child.material.userData.isAccent || child.userData?.isAccent)
            ? moodLerp.accentColor : moodLerp.baseColor;
          if (child === mandelbrotSystem && mandelbrotSystem?.geometry?.attributes?.color) {
            // Per-vertex iterRatio-based coloring
            const colors = child.geometry.attributes.color.array;
            const mDat = child.userData.data;
            if (mDat) {
              for (let mi = 0; mi < mDat.length; mi++) {
                const c = new THREE.Color().copy(moodLerp.baseColor).lerp(moodLerp.accentColor, mDat[mi].iterRatio);
                colors[mi * 3] = c.r;
                colors[mi * 3 + 1] = c.g;
                colors[mi * 3 + 2] = c.b;
              }
              child.geometry.attributes.color.needsUpdate = true;
            }
          } else if (child.material.userData.isTunnelSolid) {
            child.material.color.copy(moodLerp.baseColor).multiplyScalar(0.05);
          } else if (child.material.userData.isBossSphere) {
            child.material.color.copy(moodLerp.baseColor);
          } else if (child.material.userData.isBossWire) {
            child.material.color.copy(moodLerp.accentColor).multiplyScalar(0.3);
          } else if (child.material.userData.isPlayerBody) {
            child.material.color.setHex(0xffffff);
          } else if (child.material.color && targetStructureId !== 'QUANTUM') {
            child.material.color.copy(targetColor);
            if (child.geometry?.type === 'BoxGeometry' && !child.material.wireframe &&
                (currentStructureId === 'CUBES' || targetStructureId === 'CUBES')) {
              child.material.color.copy(moodLerp.baseColor).multiplyScalar(0.15);
            }
          }
        }
      });
    }

    // ── Set evolution ────────────────────────────────────────────────────────

    function setEvolution(idx: number) {
      if (idx < 0 || idx >= EVOLUTION_PATH.length) return;
      const newId = EVOLUTION_PATH[idx].id;
      if (newId === targetStructureId && transitionProgress >= 1.0) return;
      targetStructureId = newId;
      transitionProgress = 0;
      metamorphosisFlash = 1.0;
    }

    // ── Init ─────────────────────────────────────────────────────────────────

    buildBackground();
    buildAllStructures();

    // Add all structures to scene, hide all except starting one
    Object.values(structures).forEach((g) => {
      scene.add(g);
      g.visible = false;
      setGroupOpacity(g, 0);
    });
    const startId = EVOLUTION_PATH[evolutionIndex]?.id || 'CUBES';
    if (structures[startId]) {
      structures[startId].visible = true;
      setGroupOpacity(structures[startId], 1);
    }

    // ── Resize ───────────────────────────────────────────────────────────────

    function onResize() {
      if (!container) return;
      const w = container.clientWidth;
      const h = container.clientHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
      composer.setSize(w, h);
    }
    window.addEventListener('resize', onResize);

    // ── Animation Loop ───────────────────────────────────────────────────────

    let animId = 0;

    function animate() {
      animId = requestAnimationFrame(animate);
      if (document.hidden) return;

      const delta = Math.min(clock.getDelta(), 0.1);
      const elapsed = clock.getElapsedTime() * 0.5;
      const props = propsRef.current;

      // Map semantic state to mood key
      const moodKey = props.semanticState || 'LISTENING';
      const tMood = MOODS[moodKey] || MOODS.LISTENING;

      // Check for evolution index changes from props
      if (props.evolutionIndex !== prevEvoRef.current) {
        setEvolution(props.evolutionIndex);
        prevEvoRef.current = props.evolutionIndex;
      }

      // ── Audio data from app's audio system ───────────────────────────
      const levels = props.getLevels?.() ?? { mic: 0, output: 0 };
      const activeLevel = props.isSpeaking ? levels.output : props.isListening ? levels.mic : 0;

      // Synthesize audio bands from the level
      const heartbeat = (Math.sin(elapsed * Math.PI) + 1) / 2;
      audioData.low = activeLevel * 0.8 + heartbeat * 0.05;
      audioData.mid = activeLevel * 0.5;
      audioData.high = activeLevel * 0.3;
      audioData.total = (audioData.low + audioData.mid + audioData.high) / 3;

      if (audioData.total > 0.02) lastSoundTime = elapsed;
      const isQuiet = elapsed - lastSoundTime > 6.0;
      const targetIdle = isQuiet ? 0.2 : 1.0;
      idleFactor = THREE.MathUtils.lerp(idleFactor, targetIdle, delta * (isQuiet ? 0.3 : 2.0));

      // ── Mood lerping ────────────────────────────────────────────────
      const ml = delta * 0.5;
      moodLerp.baseColor.lerp(new THREE.Color(tMood.baseColor), ml);
      moodLerp.accentColor.lerp(new THREE.Color(tMood.accentColor), ml);
      moodLerp.rotationSpeed = THREE.MathUtils.lerp(moodLerp.rotationSpeed, tMood.rotationSpeed, ml);
      moodLerp.bloomStrength = THREE.MathUtils.lerp(moodLerp.bloomStrength, tMood.bloomStrength, ml);
      moodLerp.particleSpeedScale = THREE.MathUtils.lerp(moodLerp.particleSpeedScale, tMood.particleSpeedScale, ml);
      moodLerp.grain = THREE.MathUtils.lerp(moodLerp.grain, tMood.grain, ml);

      if (metamorphosisFlash > 0) metamorphosisFlash = Math.max(0, metamorphosisFlash - delta * 0.3);

      // ── Structure transitions ───────────────────────────────────────
      if (transitionProgress < 1.0) transitionProgress = Math.min(1.0, transitionProgress + delta * 0.125);
      const ease = smoothstep(transitionProgress);
      const scatterIntensity = Math.sin(transitionProgress * Math.PI) + metamorphosisFlash * 1.5;

      Object.keys(structures).forEach((key) => {
        const g = structures[key];
        if (!g) return;
        updateGroupColors(g);
        if (key === targetStructureId) {
          g.visible = true;
          setGroupOpacity(g, ease);
          g.scale.setScalar(1.2 - ease * 0.2);
        } else if (key === currentStructureId && transitionProgress < 1.0) {
          setGroupOpacity(g, 1 - ease);
          g.scale.setScalar(1.0 + ease * 0.5);
        } else {
          g.visible = false;
        }
      });
      if (transitionProgress >= 1.0 && currentStructureId !== targetStructureId) {
        currentStructureId = targetStructureId;
      }

      // ── Camera ──────────────────────────────────────────────────────
      if (targetStructureId === 'DOME') {
        targetCamPos.set(0, -6 + Math.sin(elapsed * 0.2) * 2, 18);
        targetCamLook.set(0, 5, 0);
      } else if (targetStructureId === 'GRID') {
        targetCamPos.set(0, 2 + Math.sin(elapsed * 0.2) * 1, 15 - (elapsed * 2) % 10);
        targetCamLook.set(0, 1, 0);
      } else if (targetStructureId === 'CABLES' || targetStructureId === 'ASTROLABE') {
        targetCamPos.set(Math.sin(elapsed * 0.1) * 12, Math.cos(elapsed * 0.1) * 12, 15 + Math.sin(elapsed * 0.2) * 5);
        targetCamLook.set(0, 0, 0);
      } else if (targetStructureId === 'MANDELBROT') {
        targetCamPos.set(Math.sin(elapsed * 0.2) * 14, 8 + Math.sin(elapsed * 0.3) * 3, Math.cos(elapsed * 0.2) * 14);
        targetCamLook.set(0, 0, 0);
      } else if (targetStructureId === 'NETWORK') {
        targetCamPos.set(Math.sin(elapsed * 0.2) * 10, Math.sin(elapsed * 0.3) * 6, Math.cos(elapsed * 0.2) * 10);
        targetCamLook.set(0, 0, 0);
      } else if (targetStructureId === 'EDEN') {
        targetCamPos.set(Math.sin(elapsed * 0.5) * 2, Math.cos(elapsed * 0.3) * 1.5, 28);
        targetCamLook.set(0, 0, 0);
      } else {
        targetCamPos.set(Math.sin(elapsed * 0.15) * 6, 5 + Math.cos(elapsed * 0.2) * 2, 18 + Math.sin(elapsed * 0.1) * 3);
        targetCamLook.set(0, 0, 0);
      }
      baseCamPos.lerp(targetCamPos, delta * 0.8);
      camera.position.copy(baseCamPos);
      const currentLook = new THREE.Vector3();
      camera.getWorldDirection(currentLook);
      const idealLook = targetCamLook.clone().sub(camera.position).normalize();
      currentLook.lerp(idealLook, delta * 2.0);
      camera.lookAt(camera.position.clone().add(currentLook));

      // ── Animate active structure ────────────────────────────────────
      const activeGroup = structures[targetStructureId];
      if (activeGroup && !['GRID', 'MANDELBROT', 'TESSERACT', 'NETWORK', 'EDEN'].includes(targetStructureId)) {
        activeGroup.rotation.y += moodLerp.rotationSpeed * idleFactor;
        if (targetStructureId !== 'DOME') activeGroup.rotation.z += moodLerp.rotationSpeed * 0.5 * idleFactor;
      }

      // CUBES — organicBreathe + dir-based expansion
      if (structures['CUBES']?.visible && coreCubes.length > 0) {
        coreCubes.forEach((c) => {
          if (!c?.userData) return;
          c.rotation.x += 0.01 * c.userData.speed * idleFactor;
          c.rotation.y += 0.012 * c.userData.speed * idleFactor;
          const organicBreathe = Math.sin(elapsed * 2.0 * c.userData.speed + c.userData.rx) * 0.15;
          const interactionForce = audioData.total * 0.5;  // Will be driven by face/hand tracking when MediaPipe is wired
          const expansion = organicBreathe + audioData.low * 1.5 + interactionForce * 1.5;
          c.position.set(
            c.userData.baseX + c.userData.dir.x * expansion,
            c.userData.baseY + c.userData.dir.y * expansion,
            c.userData.baseZ + c.userData.dir.z * expansion,
          );
        });
      }

      // DOME
      if (structures['DOME']?.visible) {
        if (abyssParticles) {
          abyssParticles.rotation.y -= 0.005 * idleFactor;
          abyssParticles.scale.setScalar(1.0 + audioData.low * 0.3 * idleFactor);
        }
        cathedralRings.forEach((c, i) => {
          if (c) {
            c.rotation.y += 0.002 * (i % 2 === 0 ? 1 : -1) * idleFactor;
            c.scale.setScalar(1.0 + audioData.mid * 0.1);
          }
        });
      }

      // GRID
      if (structures['GRID']?.visible && gridOcean?.geometry?.attributes?.position && gridOcean.userData) {
        const positions = (gridOcean.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array;
        const baseYs = gridOcean.userData.baseY as Float32Array;
        for (let i = 0; i < gridOcean.geometry.attributes.position.count; i++) {
          positions[i * 3 + 1] = baseYs[i] +
            Math.sin(positions[i * 3] * 0.1 + elapsed) * 0.8 +
            Math.cos(positions[i * 3 + 2] * 0.1 - elapsed * 1.2) * 0.8 +
            Math.sin(positions[i * 3] * 0.3 + positions[i * 3 + 2] * 0.3) * (audioData.low * 3.0 * idleFactor);
        }
        gridOcean.geometry.attributes.position.needsUpdate = true;
      }

      // MANDELBROT
      if (structures['MANDELBROT']?.visible && mandelbrotSystem?.geometry?.attributes?.position && mandelbrotSystem.userData?.data) {
        mandelbrotSystem.rotation.y = elapsed * 0.05;
        const positions = (mandelbrotSystem.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array;
        const mDat = mandelbrotSystem.userData.data as any[];
        const colorsArr = (mandelbrotSystem.geometry.attributes.color as THREE.BufferAttribute).array as Float32Array;
        for (let i = 0; i < mDat.length; i++) {
          const wave = Math.sin(mDat[i].baseX * 0.5 + elapsed) * Math.cos(mDat[i].baseZ * 0.5 + elapsed) * (audioData.low * 3.0);
          positions[i * 3 + 1] = mDat[i].baseY + audioData.low * 4.0 * mDat[i].iterRatio * idleFactor + wave;
          const c = new THREE.Color().copy(moodLerp.baseColor).lerp(moodLerp.accentColor, mDat[i].iterRatio + audioData.mid);
          colorsArr[i * 3] = c.r; colorsArr[i * 3 + 1] = c.g; colorsArr[i * 3 + 2] = c.b;
        }
        mandelbrotSystem.geometry.attributes.position.needsUpdate = true;
        mandelbrotSystem.geometry.attributes.color.needsUpdate = true;
      }

      // ASTROLABE
      if (structures['ASTROLABE']?.visible && astrolabeRings.length > 0) {
        astrolabeRings.forEach((ring) => {
          if (ring?.userData) {
            ring.rotation.x += ring.userData.rxSpeed * (1 + audioData.mid * 3);
            ring.rotation.y += ring.userData.rySpeed * (1 + audioData.mid * 3);
          }
        });
      }

      // TESSERACT
      if (structures['TESSERACT']?.visible && tesseractLines?.userData && tesseractLines.geometry?.attributes?.position) {
        const tessGroup = structures['TESSERACT'];
        const tessNodesChild = tessGroup.children[1] as THREE.Points;
        tesseractLines.userData.angleXW += (0.5 + audioData.low * 3.0) * delta * idleFactor;
        tesseractLines.userData.angleYW += (0.3 + audioData.low * 2.0) * delta * idleFactor;
        const cosXW = Math.cos(tesseractLines.userData.angleXW), sinXW = Math.sin(tesseractLines.userData.angleXW);
        const cosYW = Math.cos(tesseractLines.userData.angleYW), sinYW = Math.sin(tesseractLines.userData.angleYW);
        const pts3D: THREE.Vector3[] = [];
        (tesseractLines.userData.pts4D as { x: number; y: number; z: number; w: number }[]).forEach((p) => {
          let x = p.x, y = p.y, _z = p.z, w = p.w;
          const nx = x * cosXW - w * sinXW; const nw = x * sinXW + w * cosXW; x = nx; w = nw;
          const ny = y * cosYW - w * sinYW; const nw2 = y * sinYW + w * cosYW; y = ny; w = nw2;
          const wf = 2 / (4 - w);
          pts3D.push(new THREE.Vector3(x * wf, y * wf, _z * wf));
        });
        const lPositions = (tesseractLines.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array;
        const edges = tesseractLines.userData.edges as number[];
        for (let i = 0; i < edges.length; i += 2) {
          const idx = (i / 2) * 6;
          const e1 = edges[i], e2 = edges[i + 1];
          if (pts3D[e1] && pts3D[e2]) {
            lPositions[idx] = pts3D[e1].x; lPositions[idx + 1] = pts3D[e1].y; lPositions[idx + 2] = pts3D[e1].z;
            lPositions[idx + 3] = pts3D[e2].x; lPositions[idx + 4] = pts3D[e2].y; lPositions[idx + 5] = pts3D[e2].z;
          }
        }
        tesseractLines.geometry.attributes.position.needsUpdate = true;
        if (tessNodesChild?.geometry?.attributes?.position) {
          const nPositions = (tessNodesChild.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array;
          pts3D.forEach((p, i) => { nPositions[i * 3] = p.x; nPositions[i * 3 + 1] = p.y; nPositions[i * 3 + 2] = p.z; });
          tessNodesChild.geometry.attributes.position.needsUpdate = true;
        }
      }

      // NETWORK
      if (structures['NETWORK']?.visible && shannonLines?.geometry && shannonNodes.length > 0) {
        const netGroup = structures['NETWORK'];
        const nodeChild = netGroup.children[0] as THREE.Points;
        if (nodeChild?.geometry?.attributes?.position) {
          const nodePos = (nodeChild.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array;
          const linePts: THREE.Vector3[] = [];
          for (let i = 0; i < 120; i++) {
            if (!shannonNodes[i]) continue;
            const p = shannonNodes[i].pos, v = shannonNodes[i].velocity;
            p.add(v);
            if (p.length() > 20) v.multiplyScalar(-1);
            nodePos[i * 3] = p.x; nodePos[i * 3 + 1] = p.y; nodePos[i * 3 + 2] = p.z;
            let connected = 0;
            for (let j = i + 1; j < 120; j++) {
              if (connected > 3 || !shannonNodes[j]) break;
              if (p.distanceTo(shannonNodes[j].pos) < 6 + audioData.high * 6) {
                linePts.push(p.clone(), shannonNodes[j].pos.clone());
                connected++;
              }
            }
          }
          nodeChild.geometry.attributes.position.needsUpdate = true;
          if (linePts.length > 0) shannonLines!.geometry.setFromPoints(linePts);
          else shannonLines!.geometry.setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
        }
      }

      // MOBIUS
      if (structures['MOBIUS']?.visible && mobiusSystem?.geometry?.attributes?.position && mobiusSystem.userData?.data) {
        const positions = (mobiusSystem.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array;
        const mDat = mobiusSystem.userData.data as { u: number; v: number }[];
        const flowOffset = elapsed * (2 + audioData.mid * 4);
        for (let i = 0; i < mDat.length; i++) {
          const u = mDat[i].u + flowOffset, v = mDat[i].v;
          const R = 3.0, r = 1.5;
          positions[i * 3] = (R + r * v * Math.cos(u / 2)) * Math.cos(u);
          positions[i * 3 + 1] = (R + r * v * Math.cos(u / 2)) * Math.sin(u);
          positions[i * 3 + 2] = r * v * Math.sin(u / 2);
        }
        mobiusSystem.geometry.attributes.position.needsUpdate = true;
      }

      // QUANTUM
      if (structures['QUANTUM']?.visible && quantumRings.length > 0) {
        quantumRings.forEach((qLine) => {
          if (!qLine?.geometry?.attributes?.position || !qLine.userData) return;
          const positions = (qLine.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array;
          for (let i = 0; i < 300; i++) {
            const theta = (i / 300) * Math.PI * 2;
            const wave = Math.sin(theta * 10 + elapsed * qLine.userData.waveSpeed) * (audioData.high * 4.0);
            const r = qLine.userData.radius + wave;
            positions[i * 3] = Math.cos(theta) * r;
            positions[i * 3 + 1] = Math.sin(theta) * r;
            positions[i * 3 + 2] = 0;
          }
          qLine.geometry.attributes.position.needsUpdate = true;
          if ((qLine.material as THREE.LineBasicMaterial).color) {
            (qLine.material as THREE.LineBasicMaterial).color.setHSL(
              (qLine.userData.colorPhase + elapsed * 0.1 + audioData.mid) % 1.0, 1.0, 0.5,
            );
          }
        });
      }

      // EDEN (Giga Earth) — mood-reactive + orbital player
      if (structures['EDEN']?.visible) {
        if (edenLady) {
          edenLady.rotation.y += moodLerp.rotationSpeed * 5.0 * idleFactor;
          edenLady.scale.setScalar(1.0 + audioData.low * 0.5 * moodLerp.bloomStrength);
        }
        if (edenPlayer) {
          const r = 8 + audioData.mid * 5 * moodLerp.particleSpeedScale;
          edenPlayer.position.x = Math.cos(elapsed * 2.0) * r;
          edenPlayer.position.z = Math.sin(elapsed * 2.0) * r;
          edenPlayer.position.y = -3 + Math.sin(elapsed * 4.0) * 2;
          edenPlayer.rotation.y = -elapsed * 2.0;
          edenPlayer.rotation.z = Math.sin(elapsed * 1.5) * 0.1;
          edenPlayer.rotation.x = 0.2;
        }
        edenDebris.forEach((d) => {
          d.position.z += (d.userData.speed * moodLerp.particleSpeedScale) + (audioData.mid * 5);
          if (d.position.z > 30) {
            d.position.z = -100;
            d.position.x = (Math.random() - 0.5) * 35;
            d.position.y = (Math.random() - 0.5) * 35;
          }
        });
      }

      // NONE (Transcendence)
      if (structures['NONE']?.visible && matrixLines.length > 0) {
        matrixLines.forEach((line) => {
          if (line?.userData) {
            line.position.y += line.userData.speed * (1 + audioData.mid * 3);
            if (line.position.y > 20) line.position.y = -20;
          }
        });
      }

      // ── Global particles ────────────────────────────────────────────
      if (particleSystem?.geometry?.attributes?.position && particleSystem.geometry.attributes.color && particleSystem.userData?.data) {
        const positions = (particleSystem.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array;
        const pData = particleSystem.userData.data as any[];
        const colorsArr = (particleSystem.geometry.attributes.color as THREE.BufferAttribute).array as Float32Array;
        const targetPhiIsHorizontal = targetStructureId === 'DOME' || targetStructureId === 'GRID';

        for (let i = 0; i < pData.length; i++) {
          const p = pData[i];
          let speed = (p.speed + audioData.mid * 0.5) * moodLerp.particleSpeedScale * idleFactor;
          if (scatterIntensity > 0) {
            speed += scatterIntensity * 3.0;
            p.phi += (Math.PI / 2 - p.phi) * scatterIntensity * delta * 0.5;
          }
          p.theta += speed * delta;
          if (scatterIntensity === 0) {
            const targetPhi = targetPhiIsHorizontal ? (Math.PI / 2 + (Math.random() - 0.5) * 0.1) : p.basePhi;
            p.phi = THREE.MathUtils.lerp(p.phi, targetPhi, delta * 1.0);
          }
          let radTarget = p.baseRadius + scatterIntensity * 10.0;
          if (targetStructureId === 'GRID') radTarget += 8.0;
          p.radius = THREE.MathUtils.lerp(p.radius, radTarget, delta * 1.5);
          positions[i * 3] = p.radius * Math.sin(p.phi) * Math.cos(p.theta);
          positions[i * 3 + 1] = p.radius * Math.sin(p.phi) * Math.sin(p.theta);
          positions[i * 3 + 2] = p.radius * Math.cos(p.phi);
          if (targetStructureId !== 'EDEN') {
            colorsArr[i * 3] = moodLerp.accentColor.r;
            colorsArr[i * 3 + 1] = moodLerp.accentColor.g;
            colorsArr[i * 3 + 2] = moodLerp.accentColor.b;
          } else {
            colorsArr[i * 3] = 1.0; colorsArr[i * 3 + 1] = 0.0; colorsArr[i * 3 + 2] = 1.0;
          }
        }
        particleSystem.geometry.attributes.position.needsUpdate = true;
        particleSystem.geometry.attributes.color.needsUpdate = true;
        particleSystem.rotation.y += (0.001 + audioData.mid * 0.01) * idleFactor;
      }

      // ── Nebula clouds ───────────────────────────────────────────────
      nebulaClouds.forEach((cloud) => {
        if (!cloud?.material || !cloud.userData) return;
        const targetColor = cloud.userData.isAccent ? moodLerp.accentColor : moodLerp.baseColor;
        (cloud.material as THREE.SpriteMaterial).color.copy(targetColor);
        (cloud.material as THREE.SpriteMaterial).opacity = 0.03 + audioData.low * 0.05 + metamorphosisFlash * 0.1;
        cloud.rotation.z += cloud.userData.speed;
        cloud.position.z += delta * 0.5;
        if (cloud.position.z > 0) cloud.position.z = -100;
      });

      // ── Energy lines ────────────────────────────────────────────────
      energyLines.forEach((line) => {
        if (!line?.userData || !line.material) return;
        if (audioData.high > 0.3 && Math.random() > 0.98) line.userData.intensity = 1.0;
        line.userData.intensity *= 0.95;
        const pulse = (Math.sin(elapsed * line.userData.pulseSpeed) + 1) / 2;
        (line.material as THREE.LineBasicMaterial).opacity = (0.01 + line.userData.intensity * 0.4 + pulse * 0.03) * idleFactor;
        (line.material as THREE.LineBasicMaterial).color.copy(moodLerp.accentColor);
        if (line.userData.intensity > 0.1) {
          (line.material as THREE.LineBasicMaterial).color.lerp(new THREE.Color(0xffffff), line.userData.intensity);
        }
      });

      // ── Post-processing ─────────────────────────────────────────────
      const currentBloom = moodLerp.bloomStrength * (0.8 + audioData.low * 2.5) * idleFactor;
      bloomPass.strength = currentBloom + metamorphosisFlash * 4.0;
      const holoUniforms = (holoPass as any).uniforms;
      if (holoUniforms) {
        holoUniforms.amount.value = 0.003 + Math.sin(elapsed * 0.5) * 0.002 + metamorphosisFlash * 0.01;
        holoUniforms.angle.value = Math.sin(elapsed * 0.2);
        holoUniforms.grainAmount.value = moodLerp.grain + metamorphosisFlash * 0.05;
        holoUniforms.time.value = elapsed;
      }

      composer.render();
    }

    animate();

    // ── Cleanup ──────────────────────────────────────────────────────────────

    cleanupRef.current = () => {
      cancelAnimationFrame(animId);
      window.removeEventListener('resize', onResize);
      renderer.dispose();
      composer.dispose();
      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    initScene();
    return () => {
      cleanupRef.current?.();
      cleanupRef.current = null;
    };
  }, [initScene]);

  return (
    <div
      ref={containerRef}
      style={{
        position: 'absolute',
        inset: 0,
        overflow: 'hidden',
        pointerEvents: 'none',
        zIndex: 0,
      }}
    />
  );
}

// ── Exported Wrapper ─────────────────────────────────────────────────────────

export default function DesktopViz(props: DesktopVizProps) {
  return (
    <VizErrorBoundary>
      <DesktopVizInner {...props} />
    </VizErrorBoundary>
  );
}
