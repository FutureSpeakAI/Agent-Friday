/**
 * DesktopViz.tsx — Holographic Neural Hub Visualization
 *
 * Designed by Gemini, adapted for Agent Friday.
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

import type { DesktopVizProps } from './types';
import { MOODS } from './types';
import { EVOLUTION_PATH } from './evolution-path';
import { HolographicShader } from './shaders';
import { createGlowTexture, createCloudTexture, setGroupOpacity, smoothstep } from './materials';
import { buildAllStructures, type AllStructureRefs } from './structures';
import { animateAllStructures, type AnimContext } from './animators';

// ── Error Boundary ───────────────────────────────────────────────────────────

class VizErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  state = { hasError: false };
  static getDerivedStateFromError() { return { hasError: true }; }
  componentDidCatch(err: Error, info: ErrorInfo) {
    console.error('[DesktopViz] Error boundary caught:', err, info);
  }
  render() { return this.state.hasError ? null : this.props.children; }
}

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
    const timer = new THREE.Timer();

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

    // Background refs
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

    // ── Color update ─────────────────────────────────────────────────────────

    function updateGroupColors(group: THREE.Group) {
      group.traverse((child: any) => {
        if (child?.material?.userData) {
          const targetColor = (child.material.userData.isAccent || child.userData?.isAccent)
            ? moodLerp.accentColor : moodLerp.baseColor;
          if (child === structureRefs.mandelbrotSystem && structureRefs.mandelbrotSystem?.geometry?.attributes?.color) {
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
    const structureRefs: AllStructureRefs = buildAllStructures({ glowTexture, cloudTexture });

    // Add all structures to scene, hide all except starting one
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

      timer.update();
      const delta = Math.min(timer.getDelta(), 0.1);
      const elapsed = timer.getElapsed() * 0.5;
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

      // ── Animate active structures ──────────────────────────────────
      const animCtx: AnimContext = {
        elapsed, delta, idleFactor, audioData, moodLerp, targetStructureId,
      };
      animateAllStructures(structureRefs, animCtx);

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
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- stable scene init callback

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
