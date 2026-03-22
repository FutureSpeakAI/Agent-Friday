/**
 * desktop-viz/shaders.ts — Holographic post-processing shader.
 */

import * as THREE from 'three';

export const HolographicShader = {
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
