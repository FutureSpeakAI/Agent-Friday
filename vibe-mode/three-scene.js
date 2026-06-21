/**
 * three-scene.js — Core Three.js Scene Setup for Vibe Mode
 * 
 * Consolidated from Agent Friday's DesktopViz.tsx
 * Source: Agent-Friday/src/renderer/components/desktop-viz/DesktopViz.tsx
 * 
 * This sets up the complete Three.js scene with:
 * - WebGL renderer with post-processing (bloom + holographic shader)
 * - 13 evolution structures (Genesis Lattice through Giga Earth)
 * - 800 background particles, 20 energy flares, 15 nebula clouds
 * - Mood-reactive color system with smooth lerping
 * - Audio-reactive animation driven by mic/output levels
 * - Camera tracking per structure type
 * - Idle detection with fade-out
 * - Structure transitions with scatter effect
 * 
 * Dependencies: three.js (r170+), three/addons for post-processing
 */

import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';

import { HolographicShader } from './shaders/holographic.js';
import { createGlowTexture, createCloudTexture, createMaterial, setGroupOpacity, smoothstep } from './materials.js';
import { buildAllStructures } from './structures.js';
import { animateAllStructures } from './animators.js';
import { EVOLUTION_PATH } from './evolution-path.js';
import { MOODS } from './mood-system.js';

/**
 * Initialize the Vibe Mode Three.js scene inside a container element.
 * 
 * @param {HTMLElement} container - DOM element to mount the renderer
 * @param {object} options - Configuration
 * @param {Function} options.getLevels - Returns { mic: number, output: number } audio levels
 * @param {number} options.evolutionIndex - Index into EVOLUTION_PATH (0-12)
 * @returns {object} Controls: { setMood, setEvolution, setSpeaking, setListening, destroy }
 */
export function initVibeScene(container, options = {}) {
  const {
    getLevels = () => ({ mic: 0, output: 0 }),
    evolutionIndex = 0,
  } = options;

  // ── Scene Setup ──────────────────────────────────────────────
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
  const bloomPass = new UnrealBloomPass(
    new THREE.Vector2(container.clientWidth, container.clientHeight), 1.2, 0.6, 0.15,
  );
  const holoPass = new ShaderPass(HolographicShader);
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  composer.addPass(bloomPass);
  composer.addPass(holoPass);

  // ── State ──────────────────────────────────────────────────────
  const timer = new THREE.Timer();
  let currentStructureId = EVOLUTION_PATH[evolutionIndex]?.id || 'CUBES';
  let targetStructureId = currentStructureId;
  let transitionProgress = 1.0;
  let metamorphosisFlash = 0.0;
  let semanticState = 'LISTENING';
  let isSpeaking = false;
  let isListening = false;
  let currentEvoIndex = evolutionIndex;

  const moodLerp = {
    baseColor: new THREE.Color(MOODS.LISTENING.baseColor),
    accentColor: new THREE.Color(MOODS.LISTENING.accentColor),
    rotationSpeed: MOODS.LISTENING.rotationSpeed,
    bloomStrength: MOODS.LISTENING.bloomStrength,
    particleSpeedScale: MOODS.LISTENING.particleSpeedScale,
    grain: MOODS.LISTENING.grain,
  };

  let idleFactor = 0.4;
  let lastSoundTime = -10;
  const audioData = { low: 0, mid: 0, high: 0, total: 0 };

  const targetCamPos = new THREE.Vector3(0, 5, 15);
  const targetCamLook = new THREE.Vector3(0, 0, 0);
  const baseCamPos = new THREE.Vector3(0, 5, 15);
  camera.position.copy(targetCamPos);
  camera.lookAt(targetCamLook);

  let particleSystem = null;
  const energyLines = [];
  const nebulaClouds = [];

  // ── Build Background ───────────────────────────────────────────
  const particleCount = 800;
  const positions = new Float32Array(particleCount * 3);
  const colors = new Float32Array(particleCount * 3);
  const particleData = [];

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

  // ── Build Structures ───────────────────────────────────────────
  const structureRefs = buildAllStructures({ glowTexture, cloudTexture });
  Object.values(structureRefs.structures).forEach((g) => {
    scene.add(g);
    g.visible = false;
    setGroupOpacity(g, 0);
  });
  const startId = EVOLUTION_PATH[evolutionIndex]?.id || 'CUBES';
  if (structureRefs.structures[startId]) {
    structureRefs.structures[startId].visible = true;
    setGroupOpacity(structureRefs.structures[startId], 1);
  }

  // ── Color Update ───────────────────────────────────────────────
  function updateGroupColors(group) {
    group.traverse((child) => {
      if (child?.material?.userData) {
        const targetColor = (child.material.userData.isAccent || child.userData?.isAccent)
          ? moodLerp.accentColor : moodLerp.baseColor;
        if (child.material.userData.isTunnelSolid) {
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

  // ── Set Evolution ──────────────────────────────────────────────
  function setEvolution(idx) {
    if (idx < 0 || idx >= EVOLUTION_PATH.length) return;
    const newId = EVOLUTION_PATH[idx].id;
    if (newId === targetStructureId && transitionProgress >= 1.0) return;
    targetStructureId = newId;
    transitionProgress = 0;
    metamorphosisFlash = 1.0;
    currentEvoIndex = idx;
  }

  // ── Resize ─────────────────────────────────────────────────────
  function onResize() {
    const w = container.clientWidth;
    const h = container.clientHeight;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    composer.setSize(w, h);
  }
  window.addEventListener('resize', onResize);

  // ── Animation Loop ─────────────────────────────────────────────
  let animId = 0;

  function animate() {
    animId = requestAnimationFrame(animate);
    if (document.hidden) return;

    timer.update();
    const delta = Math.min(timer.getDelta(), 0.1);
    const elapsed = timer.getElapsed() * 0.5;

    const moodKey = semanticState || 'LISTENING';
    const tMood = MOODS[moodKey] || MOODS.LISTENING;

    // Audio
    const levels = getLevels();
    const activeLevel = isSpeaking ? levels.output : isListening ? levels.mic : 0;
    const heartbeat = (Math.sin(elapsed * Math.PI) + 1) / 2;
    audioData.low = activeLevel * 0.8 + heartbeat * 0.05;
    audioData.mid = activeLevel * 0.5;
    audioData.high = activeLevel * 0.3;
    audioData.total = (audioData.low + audioData.mid + audioData.high) / 3;

    if (audioData.total > 0.02) lastSoundTime = elapsed;
    const isQuiet = elapsed - lastSoundTime > 6.0;
    const targetIdle = isQuiet ? 0.2 : 1.0;
    idleFactor = THREE.MathUtils.lerp(idleFactor, targetIdle, delta * (isQuiet ? 0.3 : 2.0));

    // Mood lerping
    const ml = delta * 0.5;
    moodLerp.baseColor.lerp(new THREE.Color(tMood.baseColor), ml);
    moodLerp.accentColor.lerp(new THREE.Color(tMood.accentColor), ml);
    moodLerp.rotationSpeed = THREE.MathUtils.lerp(moodLerp.rotationSpeed, tMood.rotationSpeed, ml);
    moodLerp.bloomStrength = THREE.MathUtils.lerp(moodLerp.bloomStrength, tMood.bloomStrength, ml);
    moodLerp.particleSpeedScale = THREE.MathUtils.lerp(moodLerp.particleSpeedScale, tMood.particleSpeedScale, ml);
    moodLerp.grain = THREE.MathUtils.lerp(moodLerp.grain, tMood.grain, ml);

    if (metamorphosisFlash > 0) metamorphosisFlash = Math.max(0, metamorphosisFlash - delta * 0.3);

    // Structure transitions
    if (transitionProgress < 1.0) transitionProgress = Math.min(1.0, transitionProgress + delta * 0.125);
    const ease = smoothstep(transitionProgress);
    const scatterIntensity = Math.sin(transitionProgress * Math.PI) + metamorphosisFlash * 1.5;

    Object.keys(structureRefs.structures).forEach((key) => {
      const g = structureRefs.structures[key];
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

    // Camera
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

    // Animate structures
    animateAllStructures(structureRefs, { elapsed, delta, idleFactor, audioData, moodLerp, targetStructureId });

    // Global particles
    if (particleSystem?.geometry?.attributes?.position && particleSystem.geometry.attributes.color) {
      const pos = particleSystem.geometry.attributes.position.array;
      const pData = particleSystem.userData.data;
      const cols = particleSystem.geometry.attributes.color.array;
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
        pos[i * 3] = p.radius * Math.sin(p.phi) * Math.cos(p.theta);
        pos[i * 3 + 1] = p.radius * Math.sin(p.phi) * Math.sin(p.theta);
        pos[i * 3 + 2] = p.radius * Math.cos(p.phi);
        if (targetStructureId !== 'EDEN') {
          cols[i * 3] = moodLerp.accentColor.r;
          cols[i * 3 + 1] = moodLerp.accentColor.g;
          cols[i * 3 + 2] = moodLerp.accentColor.b;
        } else {
          cols[i * 3] = 1.0; cols[i * 3 + 1] = 0.0; cols[i * 3 + 2] = 1.0;
        }
      }
      particleSystem.geometry.attributes.position.needsUpdate = true;
      particleSystem.geometry.attributes.color.needsUpdate = true;
      particleSystem.rotation.y += (0.001 + audioData.mid * 0.01) * idleFactor;
    }

    // Nebula clouds
    nebulaClouds.forEach((cloud) => {
      if (!cloud?.material || !cloud.userData) return;
      const targetColor = cloud.userData.isAccent ? moodLerp.accentColor : moodLerp.baseColor;
      cloud.material.color.copy(targetColor);
      cloud.material.opacity = 0.03 + audioData.low * 0.05 + metamorphosisFlash * 0.1;
      cloud.rotation.z += cloud.userData.speed;
      cloud.position.z += delta * 0.5;
      if (cloud.position.z > 0) cloud.position.z = -100;
    });

    // Energy lines
    energyLines.forEach((line) => {
      if (!line?.userData || !line.material) return;
      if (audioData.high > 0.3 && Math.random() > 0.98) line.userData.intensity = 1.0;
      line.userData.intensity *= 0.95;
      const pulse = (Math.sin(elapsed * line.userData.pulseSpeed) + 1) / 2;
      line.material.opacity = (0.01 + line.userData.intensity * 0.4 + pulse * 0.03) * idleFactor;
      line.material.color.copy(moodLerp.accentColor);
      if (line.userData.intensity > 0.1) {
        line.material.color.lerp(new THREE.Color(0xffffff), line.userData.intensity);
      }
    });

    // Post-processing
    const currentBloom = moodLerp.bloomStrength * (0.8 + audioData.low * 2.5) * idleFactor;
    bloomPass.strength = currentBloom + metamorphosisFlash * 4.0;
    if (holoPass.uniforms) {
      holoPass.uniforms.amount.value = 0.003 + Math.sin(elapsed * 0.5) * 0.002 + metamorphosisFlash * 0.01;
      holoPass.uniforms.angle.value = Math.sin(elapsed * 0.2);
      holoPass.uniforms.grainAmount.value = moodLerp.grain + metamorphosisFlash * 0.05;
      holoPass.uniforms.time.value = elapsed;
    }

    composer.render();
  }

  animate();

  // ── Public API ─────────────────────────────────────────────────
  return {
    setMood(mood) { semanticState = mood; },
    setEvolution(idx) { setEvolution(idx); },
    setSpeaking(val) { isSpeaking = val; },
    setListening(val) { isListening = val; },
    getEvolutionIndex() { return currentEvoIndex; },
    getStructureId() { return currentStructureId; },
    destroy() {
      cancelAnimationFrame(animId);
      window.removeEventListener('resize', onResize);
      renderer.dispose();
      composer.dispose();
      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }
    },
  };
}
