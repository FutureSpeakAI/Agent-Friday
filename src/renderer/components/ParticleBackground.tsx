import React, { useRef, useEffect } from 'react';

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  baseVx: number;
  baseVy: number;
  size: number;
  opacity: number;
  color: string;
}

interface ParticleBackgroundProps {
  getLevels?: () => { mic: number; output: number };
  isListening?: boolean;
  isSpeaking?: boolean;
}

export default function ParticleBackground({
  getLevels,
  isListening,
  isSpeaking,
}: ParticleBackgroundProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const particlesRef = useRef<Particle[]>([]);
  const animRef = useRef<number>(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    };
    resize();
    window.addEventListener('resize', resize);

    // Initialize particles — reduced count for performance
    const colors = [
      'rgba(0, 240, 255, ',   // cyan
      'rgba(168, 85, 247, ',   // purple
      'rgba(59, 130, 246, ',   // blue
    ];

    particlesRef.current = Array.from({ length: 50 }, () => {
      const vx = (Math.random() - 0.5) * 0.3;
      const vy = (Math.random() - 0.5) * 0.3;
      return {
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        vx,
        vy,
        baseVx: vx,
        baseVy: vy,
        size: Math.random() * 2 + 0.5,
        opacity: Math.random() * 0.5 + 0.1,
        color: colors[Math.floor(Math.random() * colors.length)],
      };
    });

    const animate = () => {
      // Pause when tab is hidden
      if (document.hidden) {
        animRef.current = requestAnimationFrame(animate);
        return;
      }

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const particles = particlesRef.current;

      // Get audio levels
      const levels = getLevels?.() ?? { mic: 0, output: 0 };
      const activeLevel = isSpeaking ? levels.output : isListening ? levels.mic : 0;
      const boost = 1 + activeLevel * 3; // speed multiplier 1x–4x
      const connectDist = 120 + activeLevel * 80; // 120–200px

      // Draw connections
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const dx = particles[i].x - particles[j].x;
          const dy = particles[i].y - particles[j].y;
          const dist = Math.sqrt(dx * dx + dy * dy);

          if (dist < connectDist) {
            const alpha = (1 - dist / connectDist) * (0.06 + activeLevel * 0.12);
            const lineColor = isSpeaking
              ? `rgba(212, 165, 116, ${alpha})`
              : isListening
                ? `rgba(168, 85, 247, ${alpha})`
                : `rgba(0, 240, 255, ${alpha})`;
            ctx.beginPath();
            ctx.strokeStyle = lineColor;
            ctx.lineWidth = 0.5;
            ctx.moveTo(particles[i].x, particles[i].y);
            ctx.lineTo(particles[j].x, particles[j].y);
            ctx.stroke();
          }
        }
      }

      // Center of screen for "attract to center" effect during listening
      const cx = canvas.width / 2;
      const cy = canvas.height / 2;

      // Draw and update particles
      for (const p of particles) {
        ctx.beginPath();
        const drawSize = p.size + activeLevel * 1.5;
        ctx.arc(p.x, p.y, drawSize, 0, Math.PI * 2);
        ctx.fillStyle = `${p.color}${p.opacity + activeLevel * 0.2})`;
        ctx.fill();

        // When listening: gently drift toward center
        if (isListening && activeLevel > 0.05) {
          const toCenterX = (cx - p.x) * 0.0003 * activeLevel;
          const toCenterY = (cy - p.y) * 0.0003 * activeLevel;
          p.vx = p.baseVx * boost + toCenterX;
          p.vy = p.baseVy * boost + toCenterY;
        } else {
          // Normal or speaking: use boost for speed
          p.vx = p.baseVx * boost;
          p.vy = p.baseVy * boost;
        }

        p.x += p.vx;
        p.y += p.vy;

        if (p.x < 0 || p.x > canvas.width) { p.vx *= -1; p.baseVx *= -1; }
        if (p.y < 0 || p.y > canvas.height) { p.vy *= -1; p.baseVy *= -1; }
      }

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
