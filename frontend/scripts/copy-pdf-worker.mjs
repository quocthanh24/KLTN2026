import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
fs.mkdirSync(path.join(root, 'public'), { recursive: true });
fs.copyFileSync(path.join(root, 'node_modules/pdfjs-dist/build/pdf.worker.min.mjs'), path.join(root, 'public/pdf.worker.min.mjs'));
