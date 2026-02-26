import React, { useRef, useEffect, useState, Component, ErrorInfo, ReactNode } from 'react';
import type { MoodPalette } from '../contexts/MoodContext';

// ─── Types ───────────────────────────────────────────────────────────────────
export type SemanticState = 'LISTENING' | 'REASONING' | 'SUB_AGENTS' | 'EXECUTING';

export interface PersonalityEvolutionVisuals {
  sessionCount: number;
  primaryHue: number;         // 0-360
  secondaryHue: number;       // 0-360
  particleSpeed: number;      // 0.5-2.0
  cubeFragmentation: number;  // 0-1
  coreScale: number;          // 0.8-1.5
  dustDensity: number;        // 0.5-2.0
  glowIntensity: number;      // 0.5-2.0
}

interface NexusCoreProps {
  getLevels?: () => { mic: number; output: number };
  semanticState?: SemanticState;
  isSpeaking?: boolean;
  /** Mood-derived visual parameters from MoodContext */
  moodPalette?: MoodPalette;
  moodIntensity?: number;    // 0–1
  moodTurbulence?: number;   // 0–1
  /** Personality evolution — makes each agent's desktop visually unique over time */
  evolutionState?: PersonalityEvolutionVisuals | null;
}

// ─── Error Boundary ──────────────────────────────────────────────────────────
class NexusErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  state = { hasError: false };
  static getDerivedStateFromError(_err: Error) {
    return { hasError: true };
  }
  componentDidCatch(err: Error, info: ErrorInfo) {
    console.error('[NexusCore] Error boundary caught:', err, info);
  }
  render() {
    if (this.state.hasError) return null; // Fail silently — app still usable
    return this.props.children;
  }
}

// ─── Constants ───────────────────────────────────────────────────────────────
const NETWORK_COUNT = 150;
const DUST_COUNT = 400;
const CUBE_COUNT = 600;
const RING_POINTS = 64;
const GRID_SIZE = 3;
const GRID_SPACING = 1.1;
const GRID_OFFSET = (GRID_SIZE * GRID_SPACING) / 2 - GRID_SPACING / 2;
const CORE_BASE_SCALE = 3.5;
const ANIM_SPEED = 0.25;

function NexusCoreInner({
  getLevels,
  semanticState = 'LISTENING',
  isSpeaking = false,
  moodPalette,
  moodIntensity = 0.4,
  moodTurbulence = 0.2,
  evolutionState,
}: NexusCoreProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cleanupRef = useRef<(() => void) | null>(null);
  const [initError, setInitError] = useState<string | null>(null);

  // Keep props in refs so RAF can read without stale closures
  const semanticStateRef = useRef<SemanticState>(semanticState);
  useEffect(() => { semanticStateRef.current = semanticState; }, [semanticState]);

  const isSpeakingRef = useRef(isSpeaking);
  useEffect(() => { isSpeakingRef.current = isSpeaking; }, [isSpeaking]);

  const getLevelsRef = useRef(getLevels);
  useEffect(() => { getLevelsRef.current = getLevels; }, [getLevels]);

  // Mood visual parameter refs (updated from MoodContext)
  const moodPaletteRef = useRef(moodPalette);
  useEffect(() => { moodPaletteRef.current = moodPalette; }, [moodPalette]);

  const moodIntensityRef = useRef(moodIntensity);
  useEffect(() => { moodIntensityRef.current = moodIntensity; }, [moodIntensity]);

  const moodTurbulenceRef = useRef(moodTurbulence);
  useEffect(() => { moodTurbulenceRef.current = moodTurbulence; }, [moodTurbulence]);

  // Personality evolution ref (visual uniqueness that builds over sessions)
  const evolutionRef = useRef(evolutionState);
  useEffect(() => { evolutionRef.current = evolutionState; }, [evolutionState]);

  // ─── Main Three.js setup & animation ─────────────────────────────────────
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    let THREE: typeof import('three');
    let renderer: any;
    let rafId = 0;

    // Dynamic import to prevent module-level crash
    import('three').then((mod) => {
      THREE = mod;
      console.log('[NexusCore] Three.js loaded successfully');
      try {
        initScene(THREE);
      } catch (err: any) {
        console.error('[NexusCore] Scene init error:', err);
        setInitError(err.message || 'Unknown init error');
      }
    }).catch((err) => {
      console.error('[NexusCore] Failed to import three:', err);
      setInitError('Failed to load Three.js: ' + (err.message || err));
    });

    function initScene(THREE: typeof import('three')) {

    // Prevent double-init in StrictMode
    if (cleanupRef.current) {
      cleanupRef.current();
      cleanupRef.current = null;
    }

    // ════════════════════════════════════════════════════════════════════════
    // SEMANTIC COLOR MAP
    // ════════════════════════════════════════════════════════════════════════
    const StateColors: Record<SemanticState, THREE.Color> = {
      LISTENING:  new THREE.Color(0x00e5ff),  // Cyan — Gemini/Voice
      REASONING:  new THREE.Color(0xb026ff),  // Purple — Claude Opus
      SUB_AGENTS: new THREE.Color(0xffaa00),  // Amber — Agent Team
      EXECUTING:  new THREE.Color(0x00ff66),  // Emerald — Tools/World Monitor
    };
    let targetColor = StateColors.LISTENING.clone();
    let currentColor = StateColors.LISTENING.clone();
    // Pre-allocated scratch colors — reused every frame to avoid GC pressure
    const _scratchColor = new THREE.Color();
    const _scratchBg = new THREE.Color();

    // ── Renderer ──
    renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
      powerPreference: 'high-performance',
    });
    renderer.setSize(el.clientWidth, el.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);

    // ── Scene ──
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x050508);
    scene.fog = new THREE.FogExp2(0x050508, 0.03);

    // ── Camera ──
    const camera = new THREE.PerspectiveCamera(
      50,
      el.clientWidth / el.clientHeight,
      0.1,
      1000
    );
    camera.position.z = 35;

    // ── Lighting ──
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.2);
    scene.add(ambientLight);
    const pointLight = new THREE.PointLight(0xffffff, 400, 100);
    scene.add(pointLight);

    // ════════════════════════════════════════════════════════════════════════
    // 0. AI Network — connected reactive wires weaving through background
    // ════════════════════════════════════════════════════════════════════════
    const maxLines = (NETWORK_COUNT * (NETWORK_COUNT - 1)) / 2;
    const networkPositions = new Float32Array(NETWORK_COUNT * 3);
    const networkVelocities: { x: number; y: number; z: number }[] = [];

    for (let i = 0; i < NETWORK_COUNT; i++) {
      networkPositions[i * 3] = (Math.random() - 0.5) * 80;
      networkPositions[i * 3 + 1] = (Math.random() - 0.5) * 50;
      networkPositions[i * 3 + 2] = -5 - Math.random() * 40;
      networkVelocities.push({
        x: (Math.random() - 0.5) * 0.03 * ANIM_SPEED,
        y: (Math.random() - 0.5) * 0.03 * ANIM_SPEED,
        z: (Math.random() - 0.5) * 0.03 * ANIM_SPEED,
      });
    }

    const networkGeo = new THREE.BufferGeometry();
    networkGeo.setAttribute('position', new THREE.BufferAttribute(networkPositions, 3));
    const networkPointsMat = new THREE.PointsMaterial({
      color: 0xffffff,
      size: 0.15,
      transparent: true,
      opacity: 0.6,
    });
    const networkPoints = new THREE.Points(networkGeo, networkPointsMat);
    scene.add(networkPoints);

    const linePositions = new Float32Array(maxLines * 6);
    const linesGeo = new THREE.BufferGeometry();
    linesGeo.setAttribute('position', new THREE.BufferAttribute(linePositions, 3));
    const linesMat = new THREE.LineBasicMaterial({
      color: 0xffffff,
      transparent: true,
      opacity: 0.25,
    });
    const networkLines = new THREE.LineSegments(linesGeo, linesMat);
    scene.add(networkLines);

    // ════════════════════════════════════════════════════════════════════════
    // 1. 3D Audio Equalizer Background (Instanced Cubes + Wireframe Overlay)
    // ════════════════════════════════════════════════════════════════════════
    const bgGroup = new THREE.Group();
    const bgGeo = new THREE.BoxGeometry(1, 1, 1);
    const bgMat = new THREE.MeshStandardMaterial({
      color: 0x111111,
      roughness: 0.8,
      metalness: 0.2,
      flatShading: true,
    });
    // Wireframe overlay for architectural detail
    const bgWireMat = new THREE.MeshBasicMaterial({
      color: 0xffffff,
      wireframe: true,
      transparent: true,
      opacity: 0.05,
    });

    const bgInstancedMesh = new THREE.InstancedMesh(bgGeo, bgMat, CUBE_COUNT);
    const bgWireInstancedMesh = new THREE.InstancedMesh(bgGeo, bgWireMat, CUBE_COUNT);
    const bgEqIndices = new Int32Array(CUBE_COUNT);
    const bgBasePos = new Float32Array(CUBE_COUNT * 3);

    const dummy = new THREE.Object3D();
    const wireDummy = new THREE.Object3D();
    let bgIdx = 0;

    for (let x = -18; x < 18; x++) {
      for (let y = -12; y < 12; y++) {
        if (Math.abs(x) < 4 && Math.abs(y) < 4) continue; // Space for core
        if (bgIdx >= CUBE_COUNT) break;
        const posX = x * 3.5;
        const posY = y * 3.5;
        const posZ = -20;
        bgBasePos[bgIdx * 3] = posX;
        bgBasePos[bgIdx * 3 + 1] = posY;
        bgBasePos[bgIdx * 3 + 2] = posZ;

        // Solid cube
        dummy.position.set(posX, posY, posZ);
        dummy.scale.set(3.0, 3.0, 1);
        dummy.updateMatrix();
        bgInstancedMesh.setMatrixAt(bgIdx, dummy.matrix);

        // Wireframe overlay (slightly larger to prevent Z-fighting)
        wireDummy.position.set(posX, posY, posZ);
        wireDummy.scale.set(3.01, 3.01, 1.01);
        wireDummy.updateMatrix();
        bgWireInstancedMesh.setMatrixAt(bgIdx, wireDummy.matrix);

        // Map distance from center to pseudo-frequency bin
        const dist = Math.sqrt(x * x + y * y);
        bgEqIndices[bgIdx] = Math.floor(Math.min(127, dist * 2.5));
        bgIdx++;
      }
    }

    bgGroup.add(bgInstancedMesh);
    bgGroup.add(bgWireInstancedMesh);
    scene.add(bgGroup);

    // ════════════════════════════════════════════════════════════════════════
    // 1.5. Ambient Data Dust (Fine particles for atmosphere)
    // ════════════════════════════════════════════════════════════════════════
    const dustGeo = new THREE.BufferGeometry();
    const dustPos = new Float32Array(DUST_COUNT * 3);
    const dustSpeeds = new Float32Array(DUST_COUNT);
    for (let d = 0; d < DUST_COUNT; d++) {
      dustPos[d * 3] = (Math.random() - 0.5) * 100;
      dustPos[d * 3 + 1] = (Math.random() - 0.5) * 60;
      dustPos[d * 3 + 2] = -10 - Math.random() * 40;
      dustSpeeds[d] = (0.005 + Math.random() * 0.01) * ANIM_SPEED;
    }
    dustGeo.setAttribute('position', new THREE.BufferAttribute(dustPos, 3));
    const dustMat = new THREE.PointsMaterial({
      color: 0xffffff,
      size: 0.06,
      transparent: true,
      opacity: 0.3,
    });
    const dustPoints = new THREE.Points(dustGeo, dustMat);
    scene.add(dustPoints);

    // ════════════════════════════════════════════════════════════════════════
    // 2. The Core (Agent Friday Brain) — 3×3×3 fractured cube
    // ════════════════════════════════════════════════════════════════════════
    const coreGroup = new THREE.Group();
    const coreGeo = new THREE.BoxGeometry(1, 1, 1);
    const coreMat = new THREE.MeshStandardMaterial({
      color: 0x1a1a1c,
      roughness: 1.0,
      flatShading: true,
    });
    const edgesGeo = new THREE.EdgesGeometry(coreGeo);
    const edgesMat = new THREE.LineBasicMaterial({
      color: 0xffffff,
      transparent: true,
      opacity: 0.8,
    });

    interface CorePiece extends THREE.Mesh {
      userData: {
        basePos: THREE.Vector3;
        randomDir: THREE.Vector3;
        noiseOffset: number;
        rotX: number;
        rotY: number;
      };
    }

    const corePieces: CorePiece[] = [];
    for (let x = 0; x < GRID_SIZE; x++) {
      for (let y = 0; y < GRID_SIZE; y++) {
        for (let z = 0; z < GRID_SIZE; z++) {
          if (Math.random() > 0.8) continue; // fractured look
          const piece = new THREE.Mesh(coreGeo, coreMat.clone()) as CorePiece;
          const wireframe = new THREE.LineSegments(edgesGeo, edgesMat.clone());
          piece.add(wireframe);
          const posX = x * GRID_SPACING - GRID_OFFSET;
          const posY = y * GRID_SPACING - GRID_OFFSET;
          const posZ = z * GRID_SPACING - GRID_OFFSET;
          piece.userData = {
            basePos: new THREE.Vector3(posX, posY, posZ),
            randomDir: new THREE.Vector3(posX, posY, posZ).normalize(),
            noiseOffset: Math.random() * Math.PI * 2,
            rotX: 0,
            rotY: 0,
          };
          piece.position.copy(piece.userData.basePos);
          coreGroup.add(piece);
          corePieces.push(piece);
        }
      }
    }
    coreGroup.scale.set(CORE_BASE_SCALE, CORE_BASE_SCALE, CORE_BASE_SCALE);
    scene.add(coreGroup);

    // ════════════════════════════════════════════════════════════════════════
    // 3. Voice Waveform Ring — audio-reactive LineLoop around the core
    // ════════════════════════════════════════════════════════════════════════
    const ringGeo = new THREE.BufferGeometry();
    ringGeo.setAttribute(
      'position',
      new THREE.BufferAttribute(new Float32Array(RING_POINTS * 3), 3)
    );
    const waveformMat = new THREE.LineBasicMaterial({
      color: 0xffffff,
      transparent: true,
      opacity: 0.4,
    });
    const waveformRing = new THREE.LineLoop(ringGeo, waveformMat);
    scene.add(waveformRing);

    // ════════════════════════════════════════════════════════════════════════
    // Interactivity — mouse parallax
    // ════════════════════════════════════════════════════════════════════════
    let mouseX = 0;
    let mouseY = 0;
    let clickPulse = 0;

    const onMouseMove = (e: MouseEvent) => {
      mouseX = e.clientX - window.innerWidth / 2;
      mouseY = e.clientY - window.innerHeight / 2;
    };
    const onClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.classList?.contains('os-btn') && target.id !== 'start-btn') {
        clickPulse = 1.0;
      }
    };
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('click', onClick);

    // ════════════════════════════════════════════════════════════════════════
    // Animation Loop
    // ════════════════════════════════════════════════════════════════════════
    const startTime = performance.now() / 1000;
    let currentBass = 0;
    let currentTreble = 0;

    // Pseudo frequency-bin data (simulated from overall audio level)
    const pseudoDataArray = new Float32Array(128);

    const animate = () => {
      rafId = requestAnimationFrame(animate);
      const time = performance.now() / 1000 - startTime;

      // ── Audio levels from the app ──
      const levels = getLevelsRef.current?.() ?? { mic: 0, output: 0 };
      const bassLevel = Math.max(levels.mic, levels.output) * 0.8;
      const trebleLevel = levels.output * 0.6;

      // Extreme smoothing for fluid transitions
      currentBass += (bassLevel - currentBass) * 0.03;
      currentTreble += (trebleLevel - currentTreble) * 0.03;

      // Generate pseudo frequency data from overall levels
      for (let f = 0; f < 128; f++) {
        const freqNorm = f / 128;
        // Bass-heavy bins get more energy from mic, treble from output
        const bassWeight = 1 - freqNorm;
        const trebleWeight = freqNorm;
        const baseVal = (currentBass * bassWeight + currentTreble * trebleWeight);
        // Add spatial wave for visual interest
        const wave = Math.sin(time * 2 + f * 0.15) * 0.3 + 0.7;
        pseudoDataArray[f] = baseVal * wave;
      }

      // ── Click pulse decay ──
      clickPulse += (0 - clickPulse) * 0.05;

      // ══════════════════════════════════════════════════════════════════════
      // Semantic Color System — lerp towards target state color, blended with mood
      // Pre-allocated scratch colors to avoid per-frame allocations (~180/sec saved)
      // ══════════════════════════════════════════════════════════════════════
      targetColor.copy(StateColors[semanticStateRef.current]);

      // Blend mood palette color into the semantic target (30% mood, 70% semantic)
      const mp = moodPaletteRef.current;
      if (mp) {
        _scratchColor.set(mp.primary);
        targetColor.lerp(_scratchColor, 0.3);
      }

      // Personality evolution — gradually shift color toward agent's unique hue
      const evo = evolutionRef.current;
      const maturity = evo ? Math.min(evo.sessionCount / 50, 1) : 0;
      if (evo && maturity > 0) {
        _scratchColor.setHSL(evo.primaryHue / 360, 0.6, 0.5);
        targetColor.lerp(_scratchColor, maturity * 0.35);
      }

      currentColor.lerp(targetColor, 0.02); // Smooth elegant transition

      // Mood intensity modulates environment brightness
      const mInt = moodIntensityRef.current ?? 0.4;
      const mTurb = moodTurbulenceRef.current ?? 0.2;

      // Apply semantic+mood blended color to environment
      const bgScale = 0.03 + (mInt * 0.02); // Brighter background for high-intensity moods
      _scratchBg.copy(currentColor).multiplyScalar(bgScale);
      (scene.background as THREE.Color).copy(_scratchBg);
      scene.fog!.color.copy(scene.background as THREE.Color);
      scene.fog!.density = 0.03 + (currentBass * 0.005) - (mTurb * 0.005); // Turbulence opens up fog

      // Apply to geometry
      edgesMat.color.copy(currentColor);
      waveformMat.color.copy(currentColor);
      pointLight.color.copy(currentColor);
      bgWireMat.color.copy(currentColor);
      dustMat.color.copy(currentColor);
      networkPointsMat.color.copy(currentColor);
      linesMat.color.copy(currentColor);

      // Dynamic opacity based on audio + mood intensity + evolution glow
      const evoGlow = evo ? 1 + (evo.glowIntensity - 1) * maturity : 1; // 0.5-2.0 blended by maturity
      linesMat.opacity = (0.15 + (currentBass * 0.15) + (mInt * 0.05)) * evoGlow;
      const pulseLight = 0.4 + (currentBass * 0.4) + clickPulse + (mInt * 0.1);
      edgesMat.opacity = Math.min(1.0, 0.4 * pulseLight * evoGlow);
      bgWireMat.opacity = (0.05 + (currentBass * 0.1) + (clickPulse * 0.1) + (mInt * 0.03)) * evoGlow;

      // Orbiting point light
      pointLight.position.x = Math.sin(time * 0.3 * ANIM_SPEED) * 15;
      pointLight.position.z = Math.cos(time * 0.3 * ANIM_SPEED) * 15;
      pointLight.position.y = Math.sin(time * 0.2 * ANIM_SPEED) * 5;

      // ══════════════════════════════════════════════════════════════════════
      // Camera Parallax
      // ══════════════════════════════════════════════════════════════════════
      const finalCamX = mouseX * 0.006;
      const finalCamY = mouseY * 0.006;
      camera.position.x += (finalCamX - camera.position.x) * 0.03;
      camera.position.y += (-finalCamY - camera.position.y) * 0.03;
      camera.lookAt(scene.position);

      // ══════════════════════════════════════════════════════════════════════
      // Ambient Data Dust animation — turbulence modulates speed + drift
      // ══════════════════════════════════════════════════════════════════════
      // Evolution modulates particle speed and dust density
      const evoSpeedMult = evo ? 1 + (evo.particleSpeed - 1) * maturity : 1;
      const evoDustMult = evo ? 1 + (evo.dustDensity - 1) * maturity : 1;
      const turbSpeedMult = (1.0 + mTurb * 2.0) * evoSpeedMult; // Turbulent moods + evolution → faster dust
      const dPos = dustPoints.geometry.attributes.position.array as Float32Array;
      for (let d = 0; d < DUST_COUNT; d++) {
        dPos[d * 3 + 1] += (dustSpeeds[d] + (currentTreble * 0.02 * ANIM_SPEED)) * turbSpeedMult;
        dPos[d * 3] += Math.sin(time * 0.5 * ANIM_SPEED + d) * (0.005 + mTurb * 0.01);
        if (dPos[d * 3 + 1] > 30) dPos[d * 3 + 1] = -30;
      }
      dustPoints.geometry.attributes.position.needsUpdate = true;
      dustMat.opacity = (0.2 + (currentTreble * 0.3) + (mInt * 0.1)) * evoDustMult;

      // ══════════════════════════════════════════════════════════════════════
      // Network animation
      // ══════════════════════════════════════════════════════════════════════
      const positions = networkPoints.geometry.attributes.position.array as Float32Array;
      let lineIndex = 0;
      const connectDistance = 10.0 + (currentBass * 8.0);
      const connectDistSq = connectDistance * connectDistance;

      for (let i = 0; i < NETWORK_COUNT; i++) {
        const netSpeed = (1 + currentTreble * 3) * evoSpeedMult;
        positions[i * 3] += networkVelocities[i].x * netSpeed;
        positions[i * 3 + 1] += networkVelocities[i].y * netSpeed;
        positions[i * 3 + 2] += networkVelocities[i].z * netSpeed;

        // Boundary bounce
        if (positions[i * 3] > 40 || positions[i * 3] < -40) networkVelocities[i].x *= -1;
        if (positions[i * 3 + 1] > 25 || positions[i * 3 + 1] < -25) networkVelocities[i].y *= -1;
        if (positions[i * 3 + 2] > 5 || positions[i * 3 + 2] < -45) networkVelocities[i].z *= -1;

        // Connection lines
        for (let j = i + 1; j < NETWORK_COUNT; j++) {
          const dx = positions[i * 3] - positions[j * 3];
          const dy = positions[i * 3 + 1] - positions[j * 3 + 1];
          const dz = positions[i * 3 + 2] - positions[j * 3 + 2];
          const distSq = dx * dx + dy * dy + dz * dz;
          if (distSq < connectDistSq) {
            linePositions[lineIndex++] = positions[i * 3];
            linePositions[lineIndex++] = positions[i * 3 + 1];
            linePositions[lineIndex++] = positions[i * 3 + 2];
            linePositions[lineIndex++] = positions[j * 3];
            linePositions[lineIndex++] = positions[j * 3 + 1];
            linePositions[lineIndex++] = positions[j * 3 + 2];
          }
        }
      }

      networkPoints.geometry.attributes.position.needsUpdate = true;
      networkLines.geometry.setDrawRange(0, lineIndex / 3);
      networkLines.geometry.attributes.position.needsUpdate = true;

      // ══════════════════════════════════════════════════════════════════════
      // 3D Audio Equalizer Background — Z-scale + organic float
      // ══════════════════════════════════════════════════════════════════════
      for (let j = 0; j < bgIdx; j++) {
        bgInstancedMesh.getMatrixAt(j, dummy.matrix);
        dummy.matrix.decompose(dummy.position, dummy.quaternion, dummy.scale);

        bgWireInstancedMesh.getMatrixAt(j, wireDummy.matrix);
        wireDummy.matrix.decompose(wireDummy.position, wireDummy.quaternion, wireDummy.scale);

        // Audio-driven Z-scale from pseudo frequency data
        const freqVal = pseudoDataArray[bgEqIndices[j]];
        const targetZScale = 1.0 + (freqVal * 25.0);

        // Buttery smooth lerp
        dummy.scale.z += (targetZScale - dummy.scale.z) * 0.06;
        wireDummy.scale.z = dummy.scale.z + 0.01;

        // Organic time-based floating offset
        const waveOffset = Math.sin(time * 1.0 * ANIM_SPEED + j * 0.08) * 0.8;
        const targetY = bgBasePos[j * 3 + 1] + waveOffset;

        dummy.position.set(bgBasePos[j * 3], targetY, bgBasePos[j * 3 + 2]);
        wireDummy.position.set(bgBasePos[j * 3], targetY, bgBasePos[j * 3 + 2]);
        dummy.updateMatrix();
        wireDummy.updateMatrix();

        bgInstancedMesh.setMatrixAt(j, dummy.matrix);
        bgWireInstancedMesh.setMatrixAt(j, wireDummy.matrix);
      }
      bgInstancedMesh.instanceMatrix.needsUpdate = true;
      bgWireInstancedMesh.instanceMatrix.needsUpdate = true;

      // ══════════════════════════════════════════════════════════════════════
      // Voice Waveform Ring — audio-reactive circle
      // ══════════════════════════════════════════════════════════════════════
      const ringPos = waveformRing.geometry.attributes.position.array as Float32Array;
      const radiusBase = 6.0;
      for (let r = 0; r < RING_POINTS; r++) {
        const angle = (r / RING_POINTS) * Math.PI * 2;
        const audioIdx = Math.floor(r * (128 / RING_POINTS));
        const audioBoost = pseudoDataArray[audioIdx] * 3.0;

        const radius = radiusBase + audioBoost;
        ringPos[r * 3] = Math.cos(angle + time * ANIM_SPEED) * radius;
        ringPos[r * 3 + 1] = Math.sin(angle + time * ANIM_SPEED) * radius;
        ringPos[r * 3 + 2] = 0;
      }
      waveformRing.geometry.attributes.position.needsUpdate = true;
      waveformMat.opacity = isSpeakingRef.current ? 0.8 : 0.2;

      // ══════════════════════════════════════════════════════════════════════
      // AI Core animation
      // ══════════════════════════════════════════════════════════════════════
      // Evolution coreScale: lerp from 1.0 (neutral) toward agent's unique scale
      const evoCoreScaleMult = evo ? 1 + (evo.coreScale - 1) * maturity : 1;
      const coreScale = (CORE_BASE_SCALE + (clickPulse * 0.3) + (mInt * 0.15)) * evoCoreScaleMult;
      coreGroup.scale.set(coreScale, coreScale, coreScale);
      // Turbulence adds chaotic rotation variation
      const turbRotBoost = mTurb * 0.08;
      coreGroup.rotation.y = time * (0.05 + turbRotBoost) * ANIM_SPEED + (currentBass * 0.2);
      coreGroup.rotation.x = time * (0.03 + turbRotBoost * 0.5) * ANIM_SPEED;

      corePieces.forEach((piece) => {
        const organicBreathe = Math.sin(time * 2 * ANIM_SPEED + piece.userData.noiseOffset) * 0.05;
        const expandAmt = organicBreathe + (currentBass * 0.8) + (clickPulse * 1.5);

        const targetX = piece.userData.basePos.x + (piece.userData.randomDir.x * expandAmt);
        const targetY = piece.userData.basePos.y + (piece.userData.randomDir.y * expandAmt);
        const targetZ = piece.userData.basePos.z + (piece.userData.randomDir.z * expandAmt);

        piece.position.x += (targetX - piece.position.x) * 0.1;
        piece.position.y += (targetY - piece.position.y) * 0.1;
        piece.position.z += (targetZ - piece.position.z) * 0.1;

        piece.rotation.x += 0.01 * ANIM_SPEED;
        piece.rotation.y += 0.01 * ANIM_SPEED;
      });

      // ── Render ──
      renderer.render(scene, camera);
    };

    // ── Resize handler ──
    const onResize = () => {
      const w = el.clientWidth;
      const h = el.clientHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    window.addEventListener('resize', onResize);

    // Start
    animate();

    // ── Cleanup ──
    const cleanup = () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('click', onClick);
      window.removeEventListener('resize', onResize);

      // Dispose geometries & materials
      scene.traverse((obj) => {
        if ((obj as THREE.Mesh).geometry) {
          (obj as THREE.Mesh).geometry.dispose();
        }
        if ((obj as THREE.Mesh).material) {
          const mat = (obj as THREE.Mesh).material;
          if (Array.isArray(mat)) {
            mat.forEach((m) => m.dispose());
          } else {
            (mat as THREE.Material).dispose();
          }
        }
      });

      renderer.dispose();
      if (el.contains(renderer.domElement)) {
        el.removeChild(renderer.domElement);
      }
    };

    cleanupRef.current = cleanup;

    } // end initScene

    return () => {
      cancelAnimationFrame(rafId);
      if (cleanupRef.current) {
        cleanupRef.current();
        cleanupRef.current = null;
      }
    };
  }, []); // Mount once — animation reads refs for dynamic values

  if (initError) {
    console.warn('[NexusCore] Rendering fallback due to error:', initError);
    return null;
  }

  return (
    <div
      ref={containerRef}
      style={{
        position: 'absolute',
        inset: 0,
        zIndex: 0,
        pointerEvents: 'none',
      }}
    />
  );
}

// Export wrapped in error boundary
export default function NexusCore(props: NexusCoreProps) {
  return (
    <NexusErrorBoundary>
      <NexusCoreInner {...props} />
    </NexusErrorBoundary>
  );
}
