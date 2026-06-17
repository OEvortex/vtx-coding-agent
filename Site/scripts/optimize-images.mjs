/**
 * Compress the public/ images. PNGs get re-encoded with palette quantization,
 * the og-image and twitter-card get a JPEG/WebP variant for faster first paint.
 */

import { readFileSync, writeFileSync, existsSync, statSync, readdirSync } from "node:fs";
import { join, dirname, extname, basename } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PUBLIC = join(__dirname, "..", "public");
const OUT = join(__dirname, "..", "public");

const QUALITY = {
  jpeg: 82,
  webp: 82,
  png: { compressionLevel: 9, palette: true, quality: 80 },
};

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(p));
    else if (/\.(png|jpe?g)$/i.test(entry.name)) out.push(p);
  }
  return out;
}

async function reencodePng(input, output) {
  await sharp(input)
    .png({ compressionLevel: 9, palette: true, quality: 80, effort: 10 })
    .toFile(output);
}

async function toJpeg(input, output, w, h) {
  await sharp(input)
    .resize({ width: w, height: h, fit: "cover" })
    .jpeg({ quality: QUALITY.jpeg, mozjpeg: true, progressive: true })
    .toFile(output);
}

async function toWebp(input, output, w, h) {
  await sharp(input)
    .resize({ width: w, height: h, fit: "cover" })
    .webp({ quality: QUALITY.webp, effort: 6 })
    .toFile(output);
}

function kb(b) {
  return (b / 1024).toFixed(1) + " KB";
}

function pct(orig, neu) {
  return (((orig - neu) / orig) * 100).toFixed(1) + "% smaller";
}

async function main() {
  const images = walk(PUBLIC);
  let totalSaved = 0;

  for (const img of images) {
    const ext = extname(img).toLowerCase();
    const orig = statSync(img).size;

    if (ext === ".png") {
      // Re-encode the PNG with palette quantization
      const tmp = img + ".tmp";
      await reencodePng(img, tmp);
      const neu = statSync(tmp).size;
      if (neu < orig) {
        // Replace with the smaller version
        const buf = readFileSync(tmp);
        writeFileSync(img, buf);
        totalSaved += orig - neu;
        console.log(`  ✓ ${basename(img)}: ${kb(orig)} → ${kb(neu)}  (${pct(orig, neu)})`);
      } else {
        console.log(`  · ${basename(img)}: ${kb(orig)} (already optimal)`);
      }
      // Sharp creates the file with original atime/mtime; remove tmp
      try {
        const { unlinkSync } = await import("node:fs");
        unlinkSync(tmp);
      } catch {}

      // For the big social images, also emit JPEG + WebP variants
      const name = basename(img);
      if (name === "og-image.png") {
        const jpgOut = img.replace(/\.png$/i, ".jpg");
        await toJpeg(img, jpgOut, 1200, 630);
        const jpgSize = statSync(jpgOut).size;
        console.log(`  + ${basename(jpgOut)}: ${kb(jpgSize)}`);

        const webpOut = img.replace(/\.png$/i, ".webp");
        await toWebp(img, webpOut, 1200, 630);
        const webpSize = statSync(webpOut).size;
        console.log(`  + ${basename(webpOut)}: ${kb(webpSize)}`);
      } else if (name === "twitter-card.png") {
        const jpgOut = img.replace(/\.png$/i, ".jpg");
        await toJpeg(img, jpgOut, 1200, 600);
        const jpgSize = statSync(jpgOut).size;
        console.log(`  + ${basename(jpgOut)}: ${kb(jpgSize)}`);

        const webpOut = img.replace(/\.png$/i, ".webp");
        await toWebp(img, webpOut, 1200, 600);
        const webpSize = statSync(webpOut).size;
        console.log(`  + ${basename(webpOut)}: ${kb(webpSize)}`);
      }
    } else if (ext === ".jpg" || ext === ".jpeg") {
      // Re-encode the JPEG with mozjpeg
      const tmp = img + ".tmp";
      await sharp(img)
        .jpeg({ quality: QUALITY.jpeg, mozjpeg: true, progressive: true })
        .toFile(tmp);
      const neu = statSync(tmp).size;
      if (neu < orig) {
        const buf = readFileSync(tmp);
        writeFileSync(img, buf);
        totalSaved += orig - neu;
        console.log(`  ✓ ${basename(img)}: ${kb(orig)} → ${kb(neu)}  (${pct(orig, neu)})`);
      } else {
        console.log(`  · ${basename(img)}: ${kb(orig)} (already optimal)`);
      }
      try {
        const { unlinkSync } = await import("node:fs");
        unlinkSync(tmp);
      } catch {}
    }
  }

  console.log(`\nTotal bytes saved: ${kb(totalSaved)}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
