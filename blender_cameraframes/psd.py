# SPDX-License-Identifier: GPL-3.0-or-later
"""Guide-only RGB/8 PSD writer. No render, external executable or pip package.

One alpha bitmap is rasterized at a time. Layers are spooled to temporary files;
the merged preview is composed in strips. ZIP layers and RLE merged channels
follow the Adobe PSD specification. Colour is unassociated in individual layers,
and white-matted in the merged preview as required by Photoshop.
"""
import math
import os
import shutil
import struct
import tempfile
import zlib
from pathlib import Path
import numpy as np
from .geometry import clip_segment

MAX_PIXELS = 64_000_000
STRIP = 64


def pack(fmt, *values):
    return struct.pack('>' + fmt, *values)


def packbits(row):
    """PackBits with vectorized run detection; rows must fit PSD's uint16 size."""
    data = row.tobytes()
    ends = np.r_[0, np.flatnonzero(row[1:] != row[:-1]) + 1, len(row)]
    out = bytearray()
    pending = 0
    def literal(start, end):
        for i in range(start, end, 128):
            chunk = data[i:min(end, i + 128)]
            out.append(len(chunk) - 1)
            out.extend(chunk)
    for start, end in zip(ends[:-1], ends[1:]):
        start, end = int(start), int(end)
        if end - start < 3:
            continue
        literal(pending, start)
        while end - start >= 3:
            count = min(128, end - start)
            out.extend((257 - count, data[start]))
            start += count
        pending = start
    literal(pending, len(data))
    return bytes(out)


def rasterize(width, height, strokes, line_width):
    margin = line_width / 2 + 1
    segments = []
    for stroke in strokes:
        for a, b in zip(stroke, stroke[1:]):
            clipped = clip_segment((a[0] + margin, a[1] + margin), (b[0] + margin, b[1] + margin),
                                   width + 2 * margin, height + 2 * margin)
            if clipped:
                segments.append(tuple((x - margin, y - margin) for x, y in clipped))
    if not segments:
        return (0, 0, 1, 1), np.zeros((1, 1), dtype=np.uint8)
    points = [point for segment in segments for point in segment]
    left = max(0, math.floor(min(p[0] for p in points) - margin))
    top = max(0, math.floor(min(p[1] for p in points) - margin))
    right = min(width, math.ceil(max(p[0] for p in points) + margin))
    bottom = min(height, math.ceil(max(p[1] for p in points) + margin))
    if right <= left or bottom <= top:
        return (0, 0, 1, 1), np.zeros((1, 1), dtype=np.uint8)
    alpha = np.zeros((bottom - top, right - left), dtype=np.uint8)
    for (ax, ay), (bx, by) in segments:
        x0, x1 = max(left, math.floor(min(ax, bx) - margin)), min(right, math.ceil(max(ax, bx) + margin))
        y0, y1 = max(top, math.floor(min(ay, by) - margin)), min(bottom, math.ceil(max(ay, by) + margin))
        if x1 <= x0 or y1 <= y0:
            continue
        dx, dy = bx - ax, by - ay
        den = dx * dx + dy * dy
        xx = np.arange(x0, x1, dtype=np.float32)[None, :] + 0.5 - ax
        for y in range(y0, y1, STRIP):
            yy = np.arange(y, min(y1, y + STRIP), dtype=np.float32)[:, None] + 0.5 - ay
            tt = np.clip((xx * dx + yy * dy) / den, 0, 1) if den else np.zeros((len(yy), len(xx[0])), dtype=np.float32)
            distance = np.hypot(xx - tt * dx, yy - tt * dy)
            coverage = np.rint(np.clip(line_width / 2 + 0.5 - distance, 0, 1) * 255).astype(np.uint8)
            dest = alpha[y - top:y - top + len(yy), x0 - left:x1 - left]
            np.maximum(dest, coverage, out=dest)
    return (left, top, right, bottom), alpha


def zip_channel(stream, chunks):
    start = stream.tell()
    stream.write(pack('H', 2))
    compressor = zlib.compressobj(6)
    for chunk in chunks:
        stream.write(compressor.compress(chunk))
    stream.write(compressor.flush())
    return stream.tell() - start


def constant_chunks(value, size):
    block = bytes([value]) * min(size, 1024 * 1024)
    while size:
        count = min(size, len(block))
        yield block[:count]
        size -= count


def layer_record(name, box, lengths):
    left, top, right, bottom = box
    # ASCII fallback plus the authoritative UTF-16 layer name (including non-BMP).
    fallback = name.encode('ascii', 'replace')[:255]
    pascal = bytes([len(fallback)]) + fallback
    pascal += b'\0' * (-len(pascal) % 4)
    unicode_name = name.encode('utf-16-be', 'replace')
    payload = pack('I', len(unicode_name) // 2) + unicode_name
    tagged = b'8BIMluni' + pack('I', len(payload)) + payload + b'\0' * (len(payload) % 2)
    extra = pack('II', 0, 0) + pascal + tagged
    record = pack('iiiiH', top, left, bottom, right, 4)
    record += b''.join(pack('hI', channel, length) for channel, length in zip((0, 1, 2, -1), lengths))
    return record + b'8BIMnorm' + bytes((255, 0, 8, 0)) + pack('I', len(extra)) + extra


def write_psd(path, width, height, ppi, layers, progress=None):
    """Atomically replace destination only after a complete file was written.

    `layers` are ordered bottom-to-top. Dimensions are the actual output pixels.
    """
    if not (4 <= width <= 30000 and 4 <= height <= 30000 and width * height <= MAX_PIXELS):
        raise ValueError('PSDは各辺4～30000px、合計6400万画素までです')
    if not math.isfinite(ppi) or not 1 <= ppi <= 10000:
        raise ValueError('PPIは1～10000です')
    if not layers or len(layers) > 131:
        raise ValueError('出力するガイドを1～131レイヤーにしてください')
    path = Path(path)
    output_tmp = None
    try:
        with tempfile.TemporaryDirectory(prefix='camera-frames-') as folder:
            records, images, channels = [], [], []
            for i, (name, color, line_width, strokes) in enumerate(layers):
                box, alpha = rasterize(width, height, strokes, line_width)
                rgb = tuple(round(max(0, min(1, c)) * 255) for c in color)
                alpha_path = Path(folder) / f'{i}.alpha'
                alpha.tofile(alpha_path)
                channel_path = Path(folder) / f'{i}.channels'
                with channel_path.open('wb') as out:
                    lengths = [zip_channel(out, constant_chunks(c, alpha.size)) for c in rgb]
                    lengths.append(zip_channel(out, (alpha[y:y + STRIP].tobytes() for y in range(0, alpha.shape[0], STRIP))))
                records.append(layer_record(name, box, lengths))
                channels.append(channel_path)
                images.append((box, rgb, alpha_path))
                del alpha
                if progress:
                    progress(0.6 * (i + 1) / len(layers))

            merged_paths = [Path(folder) / f'merged-{c}' for c in range(4)]
            counts = [bytearray() for _ in range(4)]
            from contextlib import ExitStack
            with ExitStack() as stack:
                merged = [stack.enter_context(p.open('wb')) for p in merged_paths]
                alpha_files = [stack.enter_context(p.open('rb')) for _, _, p in images]
                for y in range(0, height, STRIP):
                    rows = min(STRIP, height - y)
                    comp = np.zeros((rows, width, 4), dtype=np.float32)
                    for (box, rgb, _), source in zip(images, alpha_files):
                        left, top, right, bottom = box
                        start, end = max(y, top), min(y + rows, bottom)
                        if start >= end:
                            continue
                        source.seek((start - top) * (right - left))
                        aa = np.frombuffer(source.read((end - start) * (right - left)), dtype=np.uint8).reshape(end - start, right - left).astype(np.float32) / 255
                        dest = comp[start - y:end - y, left:right]
                        dest[:, :, :3] *= (1 - aa[:, :, None])
                        dest[:, :, :3] += np.asarray(rgb, dtype=np.float32)[None, None, :] * aa[:, :, None]
                        dest[:, :, 3] *= (1 - aa)
                        dest[:, :, 3] += aa
                    # Merged RGB carries a white matte; individual layers do not.
                    comp[:, :, :3] += (1 - comp[:, :, 3:4]) * 255
                    comp[:, :, 3] *= 255
                    pixels = np.rint(np.clip(comp, 0, 255)).astype(np.uint8)
                    for c in range(4):
                        for row in pixels[:, :, c]:
                            encoded = packbits(row)
                            counts[c].extend(pack('H', len(encoded)))
                            merged[c].write(encoded)
                    if progress:
                        progress(0.6 + 0.35 * (y + rows) / height)

            layer_size = 2 + sum(map(len, records)) + sum(p.stat().st_size for p in channels)
            layer_pad = layer_size % 2
            resolution = pack('IHHIHH', round(ppi * 65536), 1, 1, round(ppi * 65536), 1, 1)
            resources = b'8BIM' + pack('H', 1005) + b'\0\0' + pack('I', len(resolution)) + resolution
            fd, output_tmp = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
            with os.fdopen(fd, 'wb') as out:
                out.write(b'8BPS' + pack('H', 1) + b'\0' * 6 + pack('HIIHH', 4, height, width, 8, 3))
                out.write(pack('I', 0))
                out.write(pack('I', len(resources)) + resources)
                out.write(pack('IIh', 4 + layer_size + layer_pad + 4, layer_size + layer_pad, -len(layers)))
                for record in records:
                    out.write(record)
                for channel in channels:
                    with channel.open('rb') as source:
                        shutil.copyfileobj(source, out)
                out.write(b'\0' * layer_pad + pack('I', 0))
                out.write(pack('H', 1))
                for count in counts:
                    out.write(count)
                for merged in merged_paths:
                    with merged.open('rb') as source:
                        shutil.copyfileobj(source, out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(output_tmp, path)
            output_tmp = None
            if progress:
                progress(1.0)
    finally:
        if output_tmp and os.path.exists(output_tmp):
            os.unlink(output_tmp)
