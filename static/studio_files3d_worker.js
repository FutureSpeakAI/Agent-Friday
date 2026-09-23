/* Studio › Files: builds thumbnail tiles off the main thread.
 *
 * In: {id, url|null, name, ext, dir, kids, size, color, token}
 * Out: {id, px} — a 128×128 RGBA tile (transferred ArrayBuffer), or
 *      {id, px: null} when it could not be built.
 * Fetching, decoding, drawing and the pixel read-back all happen here, so
 * none of them can hold up a frame of the 3D view.
 */
'use strict';
const TILE = 128, H = 100;
const cv = new OffscreenCanvas(TILE, TILE);
const x = cv.getContext('2d', { willReadFrequently: true, alpha: false });

const fmtSize = b => {
  if (!b) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(u.length - 1, Math.floor(Math.log(b) / Math.log(1024)));
  return (b / Math.pow(1024, i)).toFixed(i ? 1 : 0) + ' ' + u[i];
};

async function bitmapFor(job) {
  if (!job.url) return null;
  try {
    const r = await fetch(job.url, { headers: job.token ? { 'X-Friday-Token': job.token } : {} });
    if (r.status !== 200) return null;
    return await createImageBitmap(await r.blob());
  } catch (_) {
    return null;
  }
}

function draw(job, bmp) {
  const col = job.color;
  x.fillStyle = '#0b111c'; x.fillRect(0, 0, TILE, TILE);
  if (bmp) {
    const s = Math.max(TILE / bmp.width, H / bmp.height), dw = bmp.width * s, dh = bmp.height * s;
    x.save(); x.beginPath(); x.rect(0, 0, TILE, H); x.clip();
    x.drawImage(bmp, (TILE - dw) / 2, (H - dh) / 2, dw, dh);
    x.restore();
    bmp.close();
  } else {
    const g = x.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, col + '55'); g.addColorStop(1, col + '14');
    x.fillStyle = g; x.fillRect(0, 0, TILE, H);
    x.textAlign = 'center'; x.textBaseline = 'alphabetic';
    if (job.dir) {
      x.fillStyle = col;
      x.beginPath(); x.moveTo(24, 30); x.lineTo(54, 30); x.lineTo(62, 38); x.lineTo(104, 38); x.lineTo(104, 80); x.lineTo(24, 80); x.closePath(); x.fill();
      x.fillStyle = '#0b111c'; x.font = '700 15px sans-serif';
      x.fillText(String(job.kids), 64, 66);
    } else {
      const label = (job.ext || '?').toUpperCase().slice(0, 5);
      x.fillStyle = col; x.font = '800 ' + (label.length > 3 ? 24 : 32) + 'px sans-serif';
      x.fillText(label, 64, 62);
      x.font = '600 11px sans-serif'; x.fillStyle = '#9fb0c8';
      x.fillText(fmtSize(job.size), 64, 84);
    }
  }
  x.fillStyle = col; x.fillRect(0, H, TILE, 3);
  x.fillStyle = '#0d1420'; x.fillRect(0, H + 3, TILE, TILE - H - 3);
  x.fillStyle = '#e8eef8'; x.font = '600 12px sans-serif'; x.textAlign = 'left'; x.textBaseline = 'middle';
  let name = job.name;
  if (x.measureText(name).width > TILE - 10) {
    while (name.length > 3 && x.measureText(name + '…').width > TILE - 10) name = name.slice(0, -1);
    name += '…';
  }
  x.fillText(name, 5, H + 3 + (TILE - H - 3) / 2);
  return x.getImageData(0, 0, TILE, TILE).data.buffer;
}

self.onmessage = async e => {
  const job = e.data;
  try {
    const bmp = job.bitmap || await bitmapFor(job);
    const buf = draw(job, bmp);
    self.postMessage({ id: job.id, px: buf }, [buf]);
  } catch (_) {
    self.postMessage({ id: job.id, px: null });
  }
};
