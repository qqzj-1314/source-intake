import test from 'node:test';
import assert from 'node:assert/strict';
import { MAX_IMAGE_BYTES, decodeRaster, inspectRaster } from '../image-format.mjs';

const PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6s8AAAAASUVORK5CYII=';
const PNG = Buffer.from(PNG_BASE64, 'base64');
const PNG_SIGNATURE = Buffer.from('89504e470d0a1a0a', 'hex');
const HEADERS = [
  [PNG_SIGNATURE, 'image/png', 'png'],
  [Buffer.from([0xff, 0xd8, 0xff]), 'image/jpeg', 'jpg'],
  [Buffer.from('GIF87a'), 'image/gif', 'gif'],
  [Buffer.from('GIF89a'), 'image/gif', 'gif'],
  [Buffer.from('524946460000000057454250', 'hex'), 'image/webp', 'webp'],
  [Buffer.from('BM'), 'image/bmp', 'bmp'],
];

test('inspectRaster identifies a real PNG and every supported container header', () => {
  assert.equal(PNG.toString('latin1', 12, 16), 'IHDR');
  assert.equal(PNG.readUInt32BE(16), 1);
  assert.equal(PNG.readUInt32BE(20), 1);
  assert.deepEqual(inspectRaster(PNG), { mime_type: 'image/png', extension: 'png' });
  for (const [bytes, mime_type, extension] of HEADERS) {
    assert.deepEqual(inspectRaster(bytes), { mime_type, extension });
  }
});

test('inspectRaster rejects empty, non-Buffer, SVG and HTML input', () => {
  for (const value of [
    Buffer.alloc(0),
    null,
    Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"></svg>'),
    Buffer.from('<!doctype html><html></html>'),
  ]) {
    assert.throws(() => inspectRaster(value));
  }
});

test('decodeRaster round-trips canonical base64 and checks the declared MIME type', () => {
  const decoded = decodeRaster(PNG_BASE64, 'image/png');
  assert.deepEqual(decoded.bytes, PNG);
  assert.equal(decoded.bytes.toString('base64'), PNG_BASE64);
  assert.equal(decoded.mime_type, 'image/png');
  assert.equal(decoded.extension, 'png');

  assert.throws(() => decodeRaster(PNG_BASE64, 'image/jpeg'));
  assert.throws(() => decodeRaster(PNG_BASE64, 'image/svg+xml'));
});

test('decodeRaster rejects empty and noncanonical base64 input', () => {
  const noncanonical = `${PNG_BASE64.slice(0, -2)}J=`;
  assert.notEqual(noncanonical, PNG_BASE64);
  assert.deepEqual(Buffer.from(noncanonical, 'base64'), PNG);

  const invalid = [
    undefined,
    null,
    '',
    ' ',
    `data:image/png;base64,${PNG_BASE64}`,
    PNG_BASE64.slice(0, -1),
    `${PNG_BASE64}=`,
    `${PNG_BASE64.slice(0, 16)}\n${PNG_BASE64.slice(16)}`,
    'AA-_',
    'AAAA====',
    noncanonical,
  ];
  for (const value of invalid) assert.throws(() => decodeRaster(value));
});

test('decodeRaster rejects valid base64 markup that is not a raster container', () => {
  for (const markup of [
    '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
    '<!doctype html><html><body>not an image</body></html>',
  ]) {
    assert.throws(() => decodeRaster(Buffer.from(markup).toString('base64')));
  }
});

test('decodeRaster enforces the 20 MiB decoded byte budget', () => {
  assert.equal(MAX_IMAGE_BYTES, 20 * 1024 * 1024);
  const oversized = Buffer.alloc(MAX_IMAGE_BYTES + 1);
  PNG_SIGNATURE.copy(oversized);
  assert.throws(() => decodeRaster(oversized.toString('base64')), RangeError);
});

test('canonical screenshots up to the allowed boundary do not exhaust the regexp stack', () => {
  const bytes = Buffer.alloc(MAX_IMAGE_BYTES);
  PNG_SIGNATURE.copy(bytes);
  assert.deepEqual(decodeRaster(bytes.toString('base64')).bytes, bytes);
});
