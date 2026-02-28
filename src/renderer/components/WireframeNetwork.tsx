import { useRef, useEffect, useCallback } from 'react';

// ─── Configuration ───────────────────────────────────────────────────────────
const NODE_COUNT = 200;
const CONNECTION_DIST = 240;
const MOUSE_RADIUS = 280;
const PULSE_SPEED = 0.003;
const DRIFT_SPEED = 0.15;
const DEPTH_LAYERS = 3;
const IDLE_CYCLE_SPEED = 0.0002;  // ~31s full cyan→purple→cyan cycle
const MAX_PARTICLES = 60;

interface Node {
  x: number;
  y: number;
  z: number;           // 0 = front, 1 = mid, 2 = back (depth layer)
  vx: number;
  vy: number;
  baseVx: number;
  baseVy: number;
  size: number;
  baseOpacity: number;
  phase: number;       // unique phase offset for organic breathing
}

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number;
  maxLife: number;
  size: number;
  color: { r: number; g: number; b: number };
}

interface WireframeNetworkProps {
  getLevels?: () => { mic: number; output: number };
  isListening?: boolean;
  isSpeaking?: boolean;
}

export default function WireframeNetwork({
  getLevels,
  isListening,
  isSpeaking,
}: WireframeNetworkProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<Node[]>([]);
  const particlesRef = useRef<Particle[]>([]);
  const animRef = useRef<number>(0);
  const mouseRef = useRef({ x: -9999, y: -9999 });
  const pulseRef = useRef(0);          // global pulse timer
  const energyWaveRef = useRef(0);     // radial energy wave progress
  const prevWaveRadiusRef = useRef(0); // for glow trail tracking

  // Track mouse position for interactivity
  const handleMouseMove = useCallback((e: MouseEvent) => {
    mouseRef.current.x = e.clientX;
    mouseRef.current.y = e.clientY;
  }, []);

  const handleMouseLeave = useCallback(() => {
    mouseRef.current.x = -9999;
    mouseRef.current.y = -9999;
  }, []);

  useEffect(() => {
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseleave', handleMouseLeave);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseleave', handleMouseLeave);
    };
  }, [handleMouseMove, handleMouseLeave]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let W = 0;
    let H = 0;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      W = window.innerWidth;
      H = window.innerHeight;
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      canvas.style.width = `${W}px`;
      canvas.style.height = `${H}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    window.addEventListener('resize', resize);

    // ─── Initialize nodes ───────────────────────────────────────────────
    nodesRef.current = Array.from({ length: NODE_COUNT }, () => {
      const z = Math.floor(Math.random() * DEPTH_LAYERS);
      const depthScale = 1 - z * 0.3; // front=1.0, mid=0.7, back=0.4
      const speed = DRIFT_SPEED * depthScale;
      const vx = (Math.random() - 0.5) * speed;
      const vy = (Math.random() - 0.5) * speed;
      return {
        x: Math.random() * W,
        y: Math.random() * H,
        z,
        vx,
        vy,
        baseVx: vx,
        baseVy: vy,
        size: (Math.random() * 1.5 + 0.5) * depthScale + 0.5,
        baseOpacity: (Math.random() * 0.3 + 0.15) * depthScale + 0.1,
        phase: Math.random() * Math.PI * 2,
      };
    });

    particlesRef.current = [];

    // ─── Color palettes ─────────────────────────────────────────────────
    const CYAN = { r: 0, g: 240, b: 255 };
    const PURPLE = { r: 138, g: 43, b: 226 };
    const GOLD = { r: 212, g: 165, b: 116 };

    function lerpColor(
      a: { r: number; g: number; b: number },
      b: { r: number; g: number; b: number },
      t: number
    ) {
      return {
        r: Math.round(a.r + (b.r - a.r) * t),
        g: Math.round(a.g + (b.g - a.g) * t),
        b: Math.round(a.b + (b.b - a.b) * t),
      };
    }

    // ─── Particle helper ────────────────────────────────────────────────
    function spawnParticles(
      cx: number, cy: number, waveR: number, count: number,
      color: { r: number; g: number; b: number }
    ) {
      const particles = particlesRef.current;
      for (let i = 0; i < count; i++) {
        if (particles.length >= MAX_PARTICLES) {
          // Replace oldest dead particle or skip
          const deadIdx = particles.findIndex(p => p.life <= 0);
          if (deadIdx === -1) break;
          const angle = Math.random() * Math.PI * 2;
          const px = cx + Math.cos(angle) * waveR;
          const py = cy + Math.sin(angle) * waveR;
          const speed = 0.5 + Math.random() * 1.5;
          particles[deadIdx] = {
            x: px, y: py,
            vx: Math.cos(angle) * speed,
            vy: Math.sin(angle) * speed,
            life: 1.0, maxLife: 1.0,
            size: 1 + Math.random() * 2,
            color,
          };
        } else {
          const angle = Math.random() * Math.PI * 2;
          const px = cx + Math.cos(angle) * waveR;
          const py = cy + Math.sin(angle) * waveR;
          const speed = 0.5 + Math.random() * 1.5;
          particles.push({
            x: px, y: py,
            vx: Math.cos(angle) * speed,
            vy: Math.sin(angle) * speed,
            life: 1.0, maxLife: 1.0,
            size: 1 + Math.random() * 2,
            color,
          });
        }
      }
    }

    // ─── Main animation loop ────────────────────────────────────────────
    const animate = () => {
      if (document.hidden) {
        animRef.current = requestAnimationFrame(animate);
        return;
      }

      ctx.clearRect(0, 0, W, H);
      const nodes = nodesRef.current;
      const mx = mouseRef.current.x;
      const my = mouseRef.current.y;
      const time = Date.now();

      // Audio levels
      const levels = getLevels?.() ?? { mic: 0, output: 0 };
      const speakLevel = isSpeaking ? levels.output : 0;
      const listenLevel = isListening ? levels.mic : 0;
      const activeLevel = Math.max(speakLevel, listenLevel);

      // Global pulse (breathing effect)
      pulseRef.current += PULSE_SPEED;
      const globalPulse = Math.sin(pulseRef.current) * 0.5 + 0.5; // 0..1

      // Ambient idle color cycling (slow cyan → purple → cyan)
      const idleCycle = Math.sin(time * IDLE_CYCLE_SPEED) * 0.5 + 0.5; // 0..1 over ~31s

      // Energy wave radiating from center when speaking — 2x faster expansion
      if (isSpeaking && speakLevel > 0.05) {
        energyWaveRef.current = (energyWaveRef.current + 0.03 + speakLevel * 0.06) % 1.0;
      }
      const maxWaveDist = Math.max(W, H) * 0.8;
      const waveRadius = energyWaveRef.current * maxWaveDist;
      const waveActive = isSpeaking && speakLevel > 0.05;

      // Track previous wave radius for glow trail zone
      const trailMin = Math.min(prevWaveRadiusRef.current, waveRadius);
      const trailMax = waveRadius;
      prevWaveRadiusRef.current = waveRadius;

      // Determine accent color blend — with idle cycling
      const accentColor = isSpeaking
        ? lerpColor(CYAN, GOLD, 0.6 + speakLevel * 0.4)
        : isListening
          ? lerpColor(CYAN, PURPLE, 0.4 + listenLevel * 0.6)
          : lerpColor(CYAN, PURPLE, idleCycle * 0.4);

      // Screen center
      const cx = W / 2;
      const cy = H / 2;

      // Spawn particles at wave crest when speaking loudly
      if (waveActive && speakLevel > 0.25 && waveRadius > 30) {
        const count = Math.floor(speakLevel * 5); // 1-5 particles
        spawnParticles(cx, cy, waveRadius, count, accentColor);
      }

      // ─── Draw connections (back to front for proper layering) ────────
      for (let layer = DEPTH_LAYERS - 1; layer >= 0; layer--) {
        const layerNodes = nodes.filter(n => n.z === layer);
        const depthDim = 1 - layer * 0.3;
        const connectDist = CONNECTION_DIST * depthDim + activeLevel * 80;

        for (let i = 0; i < layerNodes.length; i++) {
          for (let j = i + 1; j < layerNodes.length; j++) {
            const a = layerNodes[i];
            const b = layerNodes[j];
            const dx = a.x - b.x;
            const dy = a.y - b.y;
            const dist = Math.sqrt(dx * dx + dy * dy);

            if (dist < connectDist) {
              const proximity = 1 - dist / connectDist;
              let alpha = proximity * (0.05 + activeLevel * 0.2) * depthDim;

              // Mouse proximity boost
              const midX = (a.x + b.x) / 2;
              const midY = (a.y + b.y) / 2;
              const mouseDist = Math.sqrt((midX - mx) ** 2 + (midY - my) ** 2);
              if (mouseDist < MOUSE_RADIUS) {
                const mouseProx = 1 - mouseDist / MOUSE_RADIUS;
                alpha += mouseProx * 0.15 * depthDim;
              }

              // Energy wave highlight — wider band + glow trail
              if (waveActive) {
                const midDist = Math.sqrt((midX - cx) ** 2 + (midY - cy) ** 2);
                // Primary wave front — wider band (120px instead of 80)
                const waveDelta = Math.abs(midDist - waveRadius);
                if (waveDelta < 120) {
                  const waveIntensity = (1 - waveDelta / 120) * speakLevel * 0.35 * (activeLevel * 1.5);
                  alpha += waveIntensity;
                }
                // Glow trail — fading afterglow behind the wave
                if (midDist >= trailMin && midDist <= trailMax + 60) {
                  const trailIntensity = 0.08 * speakLevel * depthDim;
                  alpha += trailIntensity;
                }
              }

              // Listening: subtle center-region glow
              if (isListening && listenLevel > 0.05) {
                const midDist = Math.sqrt((midX - cx) ** 2 + (midY - cy) ** 2);
                const haloRadius = Math.min(W, H) * 0.25 + listenLevel * 100;
                if (midDist < haloRadius) {
                  const haloIntensity = (1 - midDist / haloRadius) * listenLevel * 0.12;
                  alpha += haloIntensity;
                }
              }

              alpha = Math.min(alpha, 0.45);

              // Position-based gradient coloring — cyan on left, purple on right
              const positionT = midX / W; // 0 at left, 1 at right
              const positionColor = lerpColor(CYAN, PURPLE, positionT * 0.6);
              // Blend position color with accent color based on activity
              const blendT = Math.min(activeLevel * 2, 1);
              const c = lerpColor(positionColor, accentColor, blendT);

              // During speaking, add gold ripple wash across gradient
              if (isSpeaking && speakLevel > 0.1) {
                const ripplePhase = (time * 0.002 + positionT * 3) % (Math.PI * 2);
                const rippleStrength = Math.sin(ripplePhase) * 0.5 + 0.5;
                const goldBlend = rippleStrength * speakLevel * 0.3;
                c.r = Math.round(c.r + (GOLD.r - c.r) * goldBlend);
                c.g = Math.round(c.g + (GOLD.g - c.g) * goldBlend);
                c.b = Math.round(c.b + (GOLD.b - c.b) * goldBlend);
              }

              ctx.beginPath();
              ctx.strokeStyle = `rgba(${c.r}, ${c.g}, ${c.b}, ${alpha})`;
              ctx.lineWidth = 0.5 + proximity * 0.6 * depthDim;
              ctx.moveTo(a.x, a.y);
              ctx.lineTo(b.x, b.y);
              ctx.stroke();
            }
          }
        }

        // Cross-layer connections (sparser, dimmer)
        if (layer < DEPTH_LAYERS - 1) {
          const nextLayer = nodes.filter(n => n.z === layer + 1);
          const crossDist = connectDist * 0.6;
          for (let i = 0; i < layerNodes.length; i += 2) {
            for (let j = 0; j < nextLayer.length; j += 2) {
              const a = layerNodes[i];
              const b = nextLayer[j];
              const dx = a.x - b.x;
              const dy = a.y - b.y;
              const dist = Math.sqrt(dx * dx + dy * dy);
              if (dist < crossDist) {
                const proximity = 1 - dist / crossDist;
                const alpha = proximity * 0.03 * depthDim;
                const midX = (a.x + b.x) / 2;
                const positionT = midX / W;
                const c = lerpColor(CYAN, PURPLE, positionT * 0.5);
                ctx.beginPath();
                ctx.strokeStyle = `rgba(${c.r}, ${c.g}, ${c.b}, ${alpha})`;
                ctx.lineWidth = 0.3;
                ctx.moveTo(a.x, a.y);
                ctx.lineTo(b.x, b.y);
                ctx.stroke();
              }
            }
          }
        }
      }

      // ─── Draw nodes ─────────────────────────────────────────────────
      for (const node of nodes) {
        const depthDim = 1 - node.z * 0.3;
        const breathing = Math.sin(time * 0.001 + node.phase) * 0.15 + 0.85;
        let opacity = node.baseOpacity * breathing * depthDim;

        // Breathing node size variation — organic living feel
        let size = node.size * (1 + 0.15 * Math.sin(time * 0.002 + node.phase));

        // Mouse proximity glow
        const mouseDist = Math.sqrt((node.x - mx) ** 2 + (node.y - my) ** 2);
        if (mouseDist < MOUSE_RADIUS) {
          const mouseProx = 1 - mouseDist / MOUSE_RADIUS;
          opacity += mouseProx * 0.4 * depthDim;
          size += mouseProx * 1.8 * depthDim;
        }

        // Energy wave highlight — 50% stronger displacement
        if (waveActive) {
          const nodeDist = Math.sqrt((node.x - cx) ** 2 + (node.y - cy) ** 2);
          const waveDelta = Math.abs(nodeDist - waveRadius);
          if (waveDelta < 90) {
            const waveIntensity = (1 - waveDelta / 90) * speakLevel;
            opacity += waveIntensity * 0.6;
            size += waveIntensity * 3;
          }
          // Glow trail on nodes too
          if (nodeDist >= trailMin && nodeDist <= trailMax + 40) {
            opacity += 0.06 * speakLevel;
            size += 0.5 * speakLevel;
          }
        }

        // Listening: pulsing center halo
        if (isListening && listenLevel > 0.05) {
          const nodeDist = Math.sqrt((node.x - cx) ** 2 + (node.y - cy) ** 2);
          const haloRadius = Math.min(W, H) * 0.2 + listenLevel * 80;
          if (nodeDist < haloRadius) {
            const haloPulse = Math.sin(time * 0.004) * 0.5 + 0.5;
            const haloIntensity = (1 - nodeDist / haloRadius) * listenLevel * haloPulse * 0.2;
            opacity += haloIntensity;
            size += haloIntensity * 2;
          }
        }

        // Audio boost
        opacity += activeLevel * 0.18 * depthDim;
        size += activeLevel * 1.2 * depthDim;
        opacity = Math.min(opacity, 0.9);

        // Position-based color for nodes too
        const positionT = node.x / W;
        const baseNodeColor = lerpColor(CYAN, PURPLE, positionT * 0.5);
        const blendT = Math.min(activeLevel * 2, 1);
        const c = lerpColor(baseNodeColor, accentColor, blendT);

        // Outer glow
        if (size > 1.5 || mouseDist < MOUSE_RADIUS) {
          const glowSize = size * 3;
          const gradient = ctx.createRadialGradient(
            node.x, node.y, 0,
            node.x, node.y, glowSize
          );
          gradient.addColorStop(0, `rgba(${c.r}, ${c.g}, ${c.b}, ${opacity * 0.3})`);
          gradient.addColorStop(1, `rgba(${c.r}, ${c.g}, ${c.b}, 0)`);
          ctx.beginPath();
          ctx.fillStyle = gradient;
          ctx.arc(node.x, node.y, glowSize, 0, Math.PI * 2);
          ctx.fill();
        }

        // Core dot
        ctx.beginPath();
        ctx.fillStyle = `rgba(${c.r}, ${c.g}, ${c.b}, ${opacity})`;
        ctx.arc(node.x, node.y, size, 0, Math.PI * 2);
        ctx.fill();

        // Bright center
        if (opacity > 0.4) {
          ctx.beginPath();
          ctx.fillStyle = `rgba(255, 255, 255, ${(opacity - 0.4) * 0.8})`;
          ctx.arc(node.x, node.y, size * 0.4, 0, Math.PI * 2);
          ctx.fill();
        }

        // ─── Update node physics ──────────────────────────────────────
        const boost = 1 + activeLevel * 2;

        // Mouse repulsion (gentle push away from cursor)
        if (mouseDist < MOUSE_RADIUS && mouseDist > 1) {
          const force = (1 - mouseDist / MOUSE_RADIUS) * 0.3 * depthDim;
          node.vx += ((node.x - mx) / mouseDist) * force;
          node.vy += ((node.y - my) / mouseDist) * force;
        }

        // When speaking: outward push from energy wave — 50% stronger
        if (waveActive) {
          const nodeDist = Math.sqrt((node.x - cx) ** 2 + (node.y - cy) ** 2);
          const waveDelta = Math.abs(nodeDist - waveRadius);
          if (waveDelta < 100 && nodeDist > 1) {
            const pushForce = (1 - waveDelta / 100) * speakLevel * 0.45 * depthDim;
            node.vx += ((node.x - cx) / nodeDist) * pushForce;
            node.vy += ((node.y - cy) / nodeDist) * pushForce;
          }
        }

        // When listening: stronger attraction toward center (2x)
        if (isListening && activeLevel > 0.05) {
          const toCenterX = (cx - node.x) * 0.0008 * activeLevel * depthDim;
          const toCenterY = (cy - node.y) * 0.0008 * activeLevel * depthDim;
          node.vx = node.baseVx * boost + toCenterX;
          node.vy = node.baseVy * boost + toCenterY;
        } else {
          // Dampen velocity back toward base drift
          node.vx = node.vx * 0.95 + node.baseVx * boost * 0.05;
          node.vy = node.vy * 0.95 + node.baseVy * boost * 0.05;
        }

        node.x += node.vx;
        node.y += node.vy;

        // Wrap around edges with padding
        const pad = 40;
        if (node.x < -pad) node.x = W + pad;
        if (node.x > W + pad) node.x = -pad;
        if (node.y < -pad) node.y = H + pad;
        if (node.y > H + pad) node.y = -pad;
      }

      // ─── Draw & update particles ────────────────────────────────────
      const particles = particlesRef.current;
      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i];
        p.life -= 0.02; // ~50 frames (~833ms at 60fps)
        if (p.life <= 0) {
          particles.splice(i, 1);
          continue;
        }
        p.x += p.vx;
        p.y += p.vy;
        p.vx *= 0.98;
        p.vy *= 0.98;

        const pAlpha = p.life * 0.6;
        const pSize = p.size * p.life;

        // Particle glow
        const gradient = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, pSize * 3);
        gradient.addColorStop(0, `rgba(${p.color.r}, ${p.color.g}, ${p.color.b}, ${pAlpha * 0.5})`);
        gradient.addColorStop(1, `rgba(${p.color.r}, ${p.color.g}, ${p.color.b}, 0)`);
        ctx.beginPath();
        ctx.fillStyle = gradient;
        ctx.arc(p.x, p.y, pSize * 3, 0, Math.PI * 2);
        ctx.fill();

        // Particle core
        ctx.beginPath();
        ctx.fillStyle = `rgba(${p.color.r}, ${p.color.g}, ${p.color.b}, ${pAlpha})`;
        ctx.arc(p.x, p.y, pSize, 0, Math.PI * 2);
        ctx.fill();
      }

      // ─── Draw energy wave ring when speaking ──────────────────────────
      if (waveActive && waveRadius > 10) {
        const c = accentColor;
        const ringAlpha = (1 - energyWaveRef.current) * speakLevel * 0.2;

        // Outer glow ring
        ctx.beginPath();
        ctx.strokeStyle = `rgba(${c.r}, ${c.g}, ${c.b}, ${ringAlpha * 0.3})`;
        ctx.lineWidth = 12 + speakLevel * 10;
        ctx.arc(cx, cy, waveRadius, 0, Math.PI * 2);
        ctx.stroke();

        // Main ring
        ctx.beginPath();
        ctx.strokeStyle = `rgba(${c.r}, ${c.g}, ${c.b}, ${ringAlpha})`;
        ctx.lineWidth = 2 + speakLevel * 4;
        ctx.arc(cx, cy, waveRadius, 0, Math.PI * 2);
        ctx.stroke();

        // Inner softer ring
        ctx.beginPath();
        ctx.strokeStyle = `rgba(${c.r}, ${c.g}, ${c.b}, ${ringAlpha * 0.4})`;
        ctx.lineWidth = 8 + speakLevel * 6;
        ctx.arc(cx, cy, waveRadius, 0, Math.PI * 2);
        ctx.stroke();
      }

      // ─── Listening halo ring ────────────────────────────────────────
      if (isListening && listenLevel > 0.05) {
        const haloRadius = Math.min(W, H) * 0.15 + listenLevel * 60;
        const haloPulse = Math.sin(time * 0.003) * 0.5 + 0.5;
        const haloAlpha = listenLevel * haloPulse * 0.06;
        const c = lerpColor(CYAN, PURPLE, 0.5);

        const gradient = ctx.createRadialGradient(cx, cy, haloRadius * 0.3, cx, cy, haloRadius);
        gradient.addColorStop(0, `rgba(${c.r}, ${c.g}, ${c.b}, ${haloAlpha})`);
        gradient.addColorStop(1, `rgba(${c.r}, ${c.g}, ${c.b}, 0)`);
        ctx.beginPath();
        ctx.fillStyle = gradient;
        ctx.arc(cx, cy, haloRadius, 0, Math.PI * 2);
        ctx.fill();
      }

      // ─── Subtle vignette overlay ────────────────────────────────────
      const vignette = ctx.createRadialGradient(cx, cy, H * 0.3, cx, cy, H * 0.9);
      vignette.addColorStop(0, 'rgba(6, 11, 25, 0)');
      vignette.addColorStop(1, 'rgba(6, 11, 25, 0.4)');
      ctx.fillStyle = vignette;
      ctx.fillRect(0, 0, W, H);

      // ─── FutureSpeak.AI watermark ───────────────────────────────────
      ctx.save();
      ctx.font = '11px Inter, system-ui, sans-serif';
      ctx.letterSpacing = '2px';
      ctx.fillStyle = 'rgba(0, 240, 255, 0.08)';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'bottom';
      ctx.fillText('FutureSpeak.AI', 20, H - 16);
      ctx.restore();

      animRef.current = requestAnimationFrame(animate);
    };

    animate();

    return () => {
      cancelAnimationFrame(animRef.current);
      window.removeEventListener('resize', resize);
    };
  }, [getLevels, isListening, isSpeaking]);

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
        zIndex: 0,
      }}
    />
  );
}
