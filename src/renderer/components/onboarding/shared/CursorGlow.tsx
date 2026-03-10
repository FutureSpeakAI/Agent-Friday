/**
 * CursorGlow.tsx — Mouse-following radial gradient.
 *
 * Uses useRef + direct DOM manipulation to avoid re-render storms.
 * The glow follows the cursor with smooth easing via requestAnimationFrame.
 */

import React, { useEffect, useRef } from 'react';

const CursorGlow: React.FC = () => {
  const glowRef = useRef<HTMLDivElement>(null);
  const posRef = useRef({ x: 0, y: 0 });
  const targetRef = useRef({ x: 0, y: 0 });
  const rafRef = useRef<number>(0);

  useEffect(() => {
    const handleMove = (e: MouseEvent) => {
      targetRef.current.x = e.clientX;
      targetRef.current.y = e.clientY;
    };

    // Initialize position to center
    posRef.current = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
    targetRef.current = { ...posRef.current };

    let running = true;
    const animate = () => {
      if (!running) return;
      const ease = 0.08;
      posRef.current.x += (targetRef.current.x - posRef.current.x) * ease;
      posRef.current.y += (targetRef.current.y - posRef.current.y) * ease;
      if (glowRef.current) {
        glowRef.current.style.transform =
          `translate(${posRef.current.x - 200}px, ${posRef.current.y - 200}px)`;
      }
      rafRef.current = requestAnimationFrame(animate);
    };
    rafRef.current = requestAnimationFrame(animate);

    window.addEventListener('mousemove', handleMove);
    return () => {
      running = false;
      cancelAnimationFrame(rafRef.current);
      window.removeEventListener('mousemove', handleMove);
    };
  }, []);

  return (
    <div
      ref={glowRef}
      aria-hidden="true"
      style={styles.glow}
    />
  );
};

const styles: Record<string, React.CSSProperties> = {
  glow: {
    position: 'fixed',
    width: 400,
    height: 400,
    borderRadius: '50%',
    background: 'radial-gradient(circle, rgba(0, 240, 255, 0.06) 0%, transparent 60%)',
    pointerEvents: 'none',
    zIndex: 1,
    willChange: 'transform',
  },
};

export default CursorGlow;
