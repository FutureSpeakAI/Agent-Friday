/**
 * generate-icons.js — Generate PNG icon from SVG for electron-builder.
 *
 * Usage: node scripts/generate-icons.js
 *
 * Requirements: npm install sharp png-to-ico --save-dev
 *
 * This generates:
 *   build/icon.png  (512x512 PNG)
 *   build/icon.ico  (multi-size ICO for Windows)
 */

const fs = require('fs');
const path = require('path');

async function main() {
  const svgPath = path.join(__dirname, '..', 'build', 'icon.svg');
  const pngPath = path.join(__dirname, '..', 'build', 'icon.png');

  try {
    const sharp = require('sharp');

    // Generate 512x512 PNG from SVG
    await sharp(svgPath)
      .resize(512, 512)
      .png()
      .toFile(pngPath);

    console.log('Generated build/icon.png (512x512)');

    // Try to generate ICO
    try {
      const pngToIco = require('png-to-ico');

      // Generate multiple sizes for ICO
      const sizes = [16, 32, 48, 64, 128, 256];
      const pngBuffers = await Promise.all(
        sizes.map(size =>
          sharp(svgPath).resize(size, size).png().toBuffer()
        )
      );

      const icoBuffer = await pngToIco(pngBuffers);
      fs.writeFileSync(path.join(__dirname, '..', 'build', 'icon.ico'), icoBuffer);
      console.log('Generated build/icon.ico (multi-size)');
    } catch (e) {
      console.log('png-to-ico not available, skipping ICO generation.');
      console.log('Install with: npm install png-to-ico --save-dev');
    }
  } catch (e) {
    console.log('sharp not available. Install with: npm install sharp --save-dev');
    console.log('For now, you can manually convert build/icon.svg to build/icon.png and build/icon.ico');
    console.log('Online tools: https://svgtopng.com/ and https://icoconvert.com/');
  }
}

main().catch(console.error);
