// Generates simple solid teal (#0f766e) PNG icons with no external dependencies.
// Uses Node's built-in zlib for PNG compression (no download, no canvas lib).
import { deflateSync } from 'node:zlib'
import { writeFileSync } from 'node:fs'

const TEAL = [0x0f, 0x76, 0x6e] // #0f766e

function crc32(buf) {
  let crc = ~0
  for (let i = 0; i < buf.length; i++) {
    crc ^= buf[i]
    for (let j = 0; j < 8; j++) {
      crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1
    }
  }
  return ~crc >>> 0
}

function chunk(type, data) {
  const typeBuf = Buffer.from(type, 'ascii')
  const lenBuf = Buffer.alloc(4)
  lenBuf.writeUInt32BE(data.length, 0)
  const crcBuf = Buffer.alloc(4)
  crcBuf.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])), 0)
  return Buffer.concat([lenBuf, typeBuf, data, crcBuf])
}

function makeSolidPng(size, [r, g, b]) {
  const sig = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

  const ihdrData = Buffer.alloc(13)
  ihdrData.writeUInt32BE(size, 0)
  ihdrData.writeUInt32BE(size, 4)
  ihdrData[8] = 8 // bit depth
  ihdrData[9] = 2 // color type: RGB
  ihdrData[10] = 0
  ihdrData[11] = 0
  ihdrData[12] = 0
  const ihdr = chunk('IHDR', ihdrData)

  // Raw scanlines: each row prefixed with filter byte 0 (none).
  const rowLen = size * 3
  const raw = Buffer.alloc((rowLen + 1) * size)
  for (let y = 0; y < size; y++) {
    const rowStart = y * (rowLen + 1)
    raw[rowStart] = 0 // filter: none
    for (let x = 0; x < size; x++) {
      const px = rowStart + 1 + x * 3
      raw[px] = r
      raw[px + 1] = g
      raw[px + 2] = b
    }
  }
  const idatData = deflateSync(raw)
  const idat = chunk('IDAT', idatData)

  const iend = chunk('IEND', Buffer.alloc(0))

  return Buffer.concat([sig, ihdr, idat, iend])
}

writeFileSync('public/icon-192.png', makeSolidPng(192, TEAL))
writeFileSync('public/icon-512.png', makeSolidPng(512, TEAL))
console.log('Generated public/icon-192.png and public/icon-512.png')
