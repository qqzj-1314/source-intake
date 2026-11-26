export const MAX_IMAGE_BYTES = 20 * 1024 * 1024;

const PNG = Buffer.from('89504e470d0a1a0a', 'hex');
const JPEG = Buffer.from('ffd8ff', 'hex');
const GIF87A = Buffer.from('GIF87a', 'latin1');
const GIF89A = Buffer.from('GIF89a', 'latin1');
const RIFF = Buffer.from('RIFF', 'latin1');
const WEBP = Buffer.from('WEBP', 'latin1');
const BMP = Buffer.from('BM', 'latin1');
const INVALID_BASE64_CHAR = /[^A-Za-z0-9+/=]/;
const MAX_ENCODED_CHARS = Math.ceil(MAX_IMAGE_BYTES / 3) * 4;

const has = (bytes, offset, signature) =>
  bytes.subarray(offset, offset + signature.length).equals(signature);

const FORMATS = [
  ['image/png', 'png', (bytes) => has(bytes, 0, PNG)],
  ['image/jpeg', 'jpg', (bytes) => has(bytes, 0, JPEG)],
  ['image/gif', 'gif', (bytes) => has(bytes, 0, GIF87A) || has(bytes, 0, GIF89A)],
  ['image/webp', 'webp', (bytes) => has(bytes, 0, RIFF) && has(bytes, 8, WEBP)],
  ['image/bmp', 'bmp', (bytes) => has(bytes, 0, BMP)],
];

// Signature sniffing only; this is not full image decoding or validation.
export function inspectRaster(bytes) {
  if (!Buffer.isBuffer(bytes)) throw new TypeError('图片内容无效。');
  for (const [mime_type, extension, matches] of FORMATS) {
    if (matches(bytes)) return { mime_type, extension };
  }
  throw new Error('暂不支持此图片格式，请使用 PNG、JPEG、GIF、WebP 或 BMP。');
}

export function decodeRaster(base64, mimeType) {
  if (typeof base64 !== 'string' || base64.length === 0) {
    throw new TypeError('图片编码为空或无效，请重新复制图片。');
  }
  if (base64.length > MAX_ENCODED_CHARS) {
    throw new RangeError('图片超过 20 MiB，请选择文件导入。');
  }
  // A repeated-group regexp over multi-megabyte screenshots can exhaust V8's stack.
  // Character validation plus the canonical round-trip below also validates padding.
  if (base64.length % 4 !== 0 || INVALID_BASE64_CHAR.test(base64)) {
    throw new TypeError('图片编码无效，请重新复制图片。');
  }

  const bytes = Buffer.from(base64, 'base64');
  if (bytes.length > MAX_IMAGE_BYTES) {
    throw new RangeError('图片超过 20 MiB，请选择文件导入。');
  }
  if (bytes.toString('base64') !== base64) {
    throw new TypeError('图片编码无效，请重新复制图片。');
  }

  const info = inspectRaster(bytes);
  if (mimeType !== undefined && mimeType !== info.mime_type) {
    throw new Error('图片类型与内容不一致，请另存后重新粘贴。');
  }
  return { bytes, ...info };
}
