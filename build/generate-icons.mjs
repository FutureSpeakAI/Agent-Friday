import sharp from 'sharp';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const source = join(__dirname, 'icon.png');

async function generate() {
  // Generate tray icons
  for (const size of [16, 32, 64]) {
    await sharp(source)
      .resize(size, size, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
      .png()
      .toFile(join(__dirname, `tray-icon-${size}.png`));
    console.log(`Generated tray-icon-${size}.png`);
  }

  // Generate 256px clean version
  await sharp(source)
    .resize(256, 256, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .png()
    .toFile(join(__dirname, 'icon-256.png'));
  console.log('Generated icon-256.png');

  // Generate ICO using png-to-ico
  const pngToIco = (await import('png-to-ico')).default;

  // Generate multiple sizes for ICO
  const sizes = [16, 32, 48, 64, 128, 256];
  const buffers = [];
  for (const size of sizes) {
    const buf = await sharp(source)
      .resize(size, size, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
      .png()
      .toBuffer();
    buffers.push(buf);
  }

  const ico = await pngToIco(buffers);
  const { writeFileSync } = await import('fs');
  const icoPath = join(__dirname, '.icon-ico', 'icon.ico');
  writeFileSync(icoPath, ico);
  console.log('Generated .icon-ico/icon.ico');

  console.log('All icons generated successfully!');
}

generate().catch(err => {
  console.error('Error generating icons:', err);
  process.exit(1);
});
