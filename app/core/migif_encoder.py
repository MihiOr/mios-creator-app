#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import struct
from fractions import Fraction
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageSequence


# =========================
# MIGIF constants
# =========================

LE = "<"
MIGIF_VERSION_MAJOR = 2

MIGIF_FLAG_LOOP = 0x01
MIGIF_FLAG_HAS_PLTE = 0x02

MIGIF_COLOR_RGBA8888 = 0

MIGIF_FRAME_FULL = 1
MIGIF_FRAME_DELTA = 2
MIGIF_FRAME_TRANSFORM = 3

MIGIF_ENCODING_RAW_RGBA8888 = 0
MIGIF_ENCODING_RLE_RGBA8888 = 1
MIGIF_ENCODING_RAW_INDEX8 = 2
MIGIF_ENCODING_RLE_INDEX8 = 3

MIGIF_DELTA_RAW_RGBA8888 = 0
MIGIF_DELTA_RLE_RGBA8888 = 1
MIGIF_DELTA_RAW_INDEX8 = 2
MIGIF_DELTA_RLE_INDEX8 = 3

MIGIF_TF_POS_X = 1 << 0
MIGIF_TF_POS_Y = 1 << 1
MIGIF_TF_ALPHA = 1 << 2
MIGIF_TF_SCALE_X = 1 << 3
MIGIF_TF_SCALE_Y = 1 << 4
MIGIF_TF_ROTATION = 1 << 5

FILE_HEADER_FMT = "<5sBBBBIIIIIIIIII"
FILE_HEADER_SIZE = struct.calcsize(FILE_HEADER_FMT)

BLOCK_HEADER_FMT = "<4sI"
BLOCK_HEADER_SIZE = struct.calcsize(BLOCK_HEADER_FMT)

FRAME_HEADER_FMT = "<IHHIIIII"
FRAME_HEADER_SIZE = struct.calcsize(FRAME_HEADER_FMT)

FULL_PAYLOAD_HEADER_FMT = "<HHIII"
FULL_PAYLOAD_HEADER_SIZE = struct.calcsize(FULL_PAYLOAD_HEADER_FMT)

DELTA_PAYLOAD_HEADER_FMT = "<HH"
DELTA_PAYLOAD_HEADER_SIZE = struct.calcsize(DELTA_PAYLOAD_HEADER_FMT)

DELTA_RECT_HEADER_FMT = "<IIIII"
DELTA_RECT_HEADER_SIZE = struct.calcsize(DELTA_RECT_HEADER_FMT)

TRANSFORM_PAYLOAD_FMT = "<IiiIiii"
TRANSFORM_PAYLOAD_SIZE = struct.calcsize(TRANSFORM_PAYLOAD_FMT)


# =========================
# Utility
# =========================

def gcd_reduce(num: int, den: int) -> Tuple[int, int]:
    g = math.gcd(num, den)
    return num // g, den // g


def ms_to_fraction(ms: int) -> Tuple[int, int]:
    frac = Fraction(ms, 1000).limit_denominator(100000)
    return frac.numerator, frac.denominator


def bbox_from_mask(mask: List[bool], width: int, height: int) -> Optional[Tuple[int, int, int, int]]:
    min_x = width
    min_y = height
    max_x = -1
    max_y = -1

    for y in range(height):
        row_off = y * width
        for x in range(width):
            if mask[row_off + x]:
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y

    if max_x < min_x or max_y < min_y:
        return None

    return min_x, min_y, (max_x - min_x + 1), (max_y - min_y + 1)


def crop_pixels_rgba(
    pixels: List[Tuple[int, int, int, int]],
    width: int,
    x: int,
    y: int,
    w: int,
    h: int,
) -> List[Tuple[int, int, int, int]]:
    out: List[Tuple[int, int, int, int]] = []
    for yy in range(y, y + h):
        start = yy * width + x
        out.extend(pixels[start:start + w])
    return out


def encode_rle_rgba(pixels: List[Tuple[int, int, int, int]]) -> bytes:
    if not pixels:
        return b""

    out = bytearray()
    run_color = pixels[0]
    run_len = 1

    for px in pixels[1:]:
        if px == run_color and run_len < 65535:
            run_len += 1
        else:
            out += struct.pack(LE + "HBBBB", run_len, *run_color)
            run_color = px
            run_len = 1

    out += struct.pack(LE + "HBBBB", run_len, *run_color)
    return bytes(out)


def encode_rle_index8(indices: List[int]) -> bytes:
    if not indices:
        return b""

    out = bytearray()
    run_val = indices[0]
    run_len = 1

    for idx in indices[1:]:
        if idx == run_val and run_len < 65535:
            run_len += 1
        else:
            out += struct.pack(LE + "HB", run_len, run_val)
            run_val = idx
            run_len = 1

    out += struct.pack(LE + "HB", run_len, run_val)
    return bytes(out)


def rgba_pixels_to_bytes(pixels: List[Tuple[int, int, int, int]]) -> bytes:
    out = bytearray()
    for r, g, b, a in pixels:
        out += bytes((r, g, b, a))
    return bytes(out)


def choose_full_encoding_rgba(pixels: List[Tuple[int, int, int, int]]) -> Tuple[int, bytes]:
    raw_b = rgba_pixels_to_bytes(pixels)
    rle_b = encode_rle_rgba(pixels)

    if len(rle_b) < len(raw_b):
        return MIGIF_ENCODING_RLE_RGBA8888, rle_b
    return MIGIF_ENCODING_RAW_RGBA8888, raw_b


def choose_delta_encoding_rgba(pixels: List[Tuple[int, int, int, int]]) -> Tuple[int, bytes]:
    raw_b = rgba_pixels_to_bytes(pixels)
    rle_b = encode_rle_rgba(pixels)

    if len(rle_b) < len(raw_b):
        return MIGIF_DELTA_RLE_RGBA8888, rle_b
    return MIGIF_DELTA_RAW_RGBA8888, raw_b


def build_palette_if_possible(
    frames_rgba: List[List[Tuple[int, int, int, int]]]
) -> Optional[List[Tuple[int, int, int, int]]]:
    colors: Dict[Tuple[int, int, int, int], bool] = {}
    for frame in frames_rgba:
        for px in frame:
            colors[px] = True
            if len(colors) > 256:
                return None
    return list(colors.keys())


def pixels_to_indices(
    pixels: List[Tuple[int, int, int, int]],
    palette_map: Dict[Tuple[int, int, int, int], int],
) -> List[int]:
    return [palette_map[px] for px in pixels]


def choose_full_encoding_indexed(indices: List[int]) -> Tuple[int, bytes]:
    raw_b = bytes(indices)
    rle_b = encode_rle_index8(indices)

    if len(rle_b) < len(raw_b):
        return MIGIF_ENCODING_RLE_INDEX8, rle_b
    return MIGIF_ENCODING_RAW_INDEX8, raw_b


def choose_delta_encoding_indexed(indices: List[int]) -> Tuple[int, bytes]:
    raw_b = bytes(indices)
    rle_b = encode_rle_index8(indices)

    if len(rle_b) < len(raw_b):
        return MIGIF_DELTA_RLE_INDEX8, rle_b
    return MIGIF_DELTA_RAW_INDEX8, raw_b


def detect_uniform_alpha_change(
    prev_pixels: List[Tuple[int, int, int, int]],
    curr_pixels: List[Tuple[int, int, int, int]],
) -> Optional[int]:
    if len(prev_pixels) != len(curr_pixels):
        return None

    factor: Optional[float] = None

    for (pr, pg, pb, pa), (cr, cg, cb, ca) in zip(prev_pixels, curr_pixels):
        if (pr, pg, pb) != (cr, cg, cb):
            return None

        if pa == 0 and ca == 0:
            continue

        if pa == 0 and ca != 0:
            return None

        current_factor = ca / pa if pa != 0 else 0.0

        if factor is None:
            factor = current_factor
        else:
            if abs(current_factor - factor) > (1.0 / 255.0):
                return None

    if factor is None:
        return 255

    return int(round(max(0.0, min(1.0, factor)) * 255))


# =========================
# MIGIF binary writers
# =========================

def pack_file_header(
    canvas_width: int,
    canvas_height: int,
    frame_count: int,
    fps_num: int,
    fps_den: int,
    blocks_size: int,
    frames_offset: int,
    file_size: int,
    flags: int,
) -> bytes:
    return struct.pack(
        FILE_HEADER_FMT,
        b"MIGIF",
        MIGIF_VERSION_MAJOR,
        0,
        FILE_HEADER_SIZE,
        flags,
        canvas_width,
        canvas_height,
        frame_count,
        fps_num,
        fps_den,
        blocks_size,
        frames_offset,
        file_size,
        0,
        0,
    )


def pack_plte_block(palette: List[Tuple[int, int, int, int]]) -> bytes:
    colors_data = bytearray()
    for r, g, b, a in palette:
        colors_data += struct.pack("BBBB", r, g, b, a)

    block_size = BLOCK_HEADER_SIZE + 4 + 1 + 1 + 2 + len(colors_data)
    header = struct.pack(
        LE + "4sIIBBH",
        b"PLTE",
        block_size,
        len(palette),
        MIGIF_COLOR_RGBA8888,
        0,
        0,
    )
    return header + bytes(colors_data)


def pack_frame(
    frame_type: int,
    duration_num: int,
    duration_den: int,
    payload: bytes,
    frame_flags: int = 0,
) -> bytes:
    payload_size = len(payload)
    frame_size = FRAME_HEADER_SIZE + payload_size

    header = struct.pack(
        FRAME_HEADER_FMT,
        frame_size,
        frame_type,
        frame_flags,
        duration_num,
        duration_den,
        payload_size,
        0,
        0,
    )
    return header + payload


def pack_full_payload(encoding: int, width: int, height: int, data: bytes) -> bytes:
    hdr = struct.pack(FULL_PAYLOAD_HEADER_FMT, encoding, 0, width, height, len(data))
    return hdr + data


def pack_delta_payload(
    encoding: int,
    rects: List[Tuple[int, int, int, int, bytes]],
) -> bytes:
    out = bytearray()
    out += struct.pack(DELTA_PAYLOAD_HEADER_FMT, encoding, len(rects))
    for x, y, w, h, rect_data in rects:
        out += struct.pack(DELTA_RECT_HEADER_FMT, x, y, w, h, len(rect_data))
        out += rect_data
    return bytes(out)


def pack_transform_payload(alpha: Optional[int] = None) -> bytes:
    flags = 0
    pos_x = 0
    pos_y = 0
    scale_x = 65536
    scale_y = 65536
    rotation_deg = 0
    alpha_val = 255

    if alpha is not None:
        flags |= MIGIF_TF_ALPHA
        alpha_val = alpha

    return struct.pack(
        TRANSFORM_PAYLOAD_FMT,
        flags,
        pos_x,
        pos_y,
        alpha_val,
        scale_x,
        scale_y,
        rotation_deg,
    )


# =========================
# GIF loading
# =========================

def load_gif_frames(
    path: Path
) -> Tuple[int, int, List[List[Tuple[int, int, int, int]]], List[int], bool]:
    img = Image.open(path)

    width, height = img.size
    frames: List[List[Tuple[int, int, int, int]]] = []
    durations_ms: List[int] = []

    loop = img.info.get("loop", 1) == 0

    for frame in ImageSequence.Iterator(img):
        rgba = frame.convert("RGBA")
        pixels = list(rgba.getdata())
        frames.append(pixels)

        duration = frame.info.get("duration", img.info.get("duration", 100))
        if not duration or duration <= 0:
            duration = 100
        durations_ms.append(duration)

    if not frames:
        raise ValueError("GIF nema nijedan frame.")

    return width, height, frames, durations_ms, loop


# =========================
# Conversion logic
# =========================

def build_frames(
    width: int,
    height: int,
    frames_rgba: List[List[Tuple[int, int, int, int]]],
    durations_ms: List[int],
    palette: Optional[List[Tuple[int, int, int, int]]],
    force_full_every: int,
) -> List[bytes]:
    out_frames: List[bytes] = []

    palette_map: Optional[Dict[Tuple[int, int, int, int], int]] = None
    if palette is not None:
        palette_map = {c: i for i, c in enumerate(palette)}

    prev_pixels: Optional[List[Tuple[int, int, int, int]]] = None

    for i, (pixels, dur_ms) in enumerate(zip(frames_rgba, durations_ms)):
        dur_num, dur_den = ms_to_fraction(dur_ms)
        use_full = (i == 0) or (force_full_every > 0 and i % force_full_every == 0)

        if prev_pixels is not None and not use_full:
            maybe_alpha = detect_uniform_alpha_change(prev_pixels, pixels)
            if maybe_alpha is not None:
                payload = pack_transform_payload(alpha=maybe_alpha)
                out_frames.append(pack_frame(
                    MIGIF_FRAME_TRANSFORM,
                    dur_num,
                    dur_den,
                    payload,
                ))
                prev_pixels = pixels
                continue

            diff_mask = [a != b for a, b in zip(prev_pixels, pixels)]
            bbox = bbox_from_mask(diff_mask, width, height)

            if bbox is None:
                payload = pack_transform_payload(alpha=None)
                out_frames.append(pack_frame(
                    MIGIF_FRAME_TRANSFORM,
                    dur_num,
                    dur_den,
                    payload,
                ))
                prev_pixels = pixels
                continue

            x, y, w, h = bbox
            rect_pixels = crop_pixels_rgba(pixels, width, x, y, w, h)

            if palette_map is not None:
                rect_indices = pixels_to_indices(rect_pixels, palette_map)
                enc, data = choose_delta_encoding_indexed(rect_indices)
            else:
                enc, data = choose_delta_encoding_rgba(rect_pixels)

            delta_payload = pack_delta_payload(enc, [(x, y, w, h, data)])

            if palette_map is not None:
                full_indices = pixels_to_indices(pixels, palette_map)
                full_enc, full_data = choose_full_encoding_indexed(full_indices)
            else:
                full_enc, full_data = choose_full_encoding_rgba(pixels)

            full_payload = pack_full_payload(full_enc, width, height, full_data)

            if len(delta_payload) < len(full_payload):
                out_frames.append(pack_frame(
                    MIGIF_FRAME_DELTA,
                    dur_num,
                    dur_den,
                    delta_payload,
                ))
            else:
                out_frames.append(pack_frame(
                    MIGIF_FRAME_FULL,
                    dur_num,
                    dur_den,
                    full_payload,
                ))
        else:
            if palette_map is not None:
                indices = pixels_to_indices(pixels, palette_map)
                enc, data = choose_full_encoding_indexed(indices)
            else:
                enc, data = choose_full_encoding_rgba(pixels)

            full_payload = pack_full_payload(enc, width, height, data)
            out_frames.append(pack_frame(
                MIGIF_FRAME_FULL,
                dur_num,
                dur_den,
                full_payload,
            ))

        prev_pixels = pixels

    return out_frames


def validate_frame_blobs(frame_blobs: List[bytes]) -> None:
    for i, blob in enumerate(frame_blobs):
        if len(blob) < FRAME_HEADER_SIZE:
            raise ValueError(f"Frame {i}: blob je prekratak.")

        (
            frame_size,
            frame_type,
            frame_flags,
            duration_num,
            duration_den,
            payload_size,
            reserved0,
            reserved1,
        ) = struct.unpack(FRAME_HEADER_FMT, blob[:FRAME_HEADER_SIZE])

        if frame_size != len(blob):
            raise ValueError(
                f"Frame {i}: frame_size={frame_size}, a stvarna veličina je {len(blob)}"
            )

        if frame_size != FRAME_HEADER_SIZE + payload_size:
            raise ValueError(
                f"Frame {i}: frame_size={frame_size}, a očekivano je "
                f"{FRAME_HEADER_SIZE + payload_size}"
            )

        if duration_den == 0:
            raise ValueError(f"Frame {i}: duration_den ne smije biti 0.")

        if reserved0 != 0 or reserved1 != 0:
            raise ValueError(f"Frame {i}: reserved polja moraju biti 0.")


def convert_gif_to_migif(
    input_path: Path,
    output_path: Path,
    force_rgba: bool = False,
    force_full_every: int = 0,
    fps_override: Optional[Tuple[int, int]] = None,
) -> None:
    width, height, frames_rgba, durations_ms, loop = load_gif_frames(input_path)
    palette = None if force_rgba else build_palette_if_possible(frames_rgba)

    blocks = bytearray()
    flags = 0

    if loop:
        flags |= MIGIF_FLAG_LOOP

    if palette is not None:
        flags |= MIGIF_FLAG_HAS_PLTE
        blocks += pack_plte_block(palette)

    frame_blobs = build_frames(
        width=width,
        height=height,
        frames_rgba=frames_rgba,
        durations_ms=durations_ms,
        palette=palette,
        force_full_every=force_full_every,
    )

    validate_frame_blobs(frame_blobs)

    if fps_override is not None:
        fps_num, fps_den = gcd_reduce(*fps_override)
    else:
        first_ms = durations_ms[0] if durations_ms else 100
        fps_frac = Fraction(1000, first_ms).limit_denominator(100000)
        fps_num, fps_den = fps_frac.numerator, fps_frac.denominator

    blocks_size = len(blocks)
    frames_offset = FILE_HEADER_SIZE + blocks_size
    frames_data = b"".join(frame_blobs)
    file_size = frames_offset + len(frames_data)

    header = pack_file_header(
        canvas_width=width,
        canvas_height=height,
        frame_count=len(frame_blobs),
        fps_num=fps_num,
        fps_den=fps_den,
        blocks_size=blocks_size,
        frames_offset=frames_offset,
        file_size=file_size,
        flags=flags,
    )

    with open(output_path, "wb") as f:
        f.write(header)
        f.write(blocks)
        f.write(frames_data)

    print("FILE_HEADER_SIZE =", FILE_HEADER_SIZE)
    print("FRAME_HEADER_SIZE =", FRAME_HEADER_SIZE)
    print("blocks_size =", blocks_size)
    print("frames_offset =", frames_offset)
    print("frames_data =", len(frames_data))
    print("file_size =", file_size)


# =========================
# CLI
# =========================

def parse_fps(value: str) -> Tuple[int, int]:
    if "/" in value:
        a, b = value.split("/", 1)
        num = int(a)
        den = int(b)
    else:
        num = int(value)
        den = 1

    if num <= 0 or den <= 0:
        raise argparse.ArgumentTypeError(
            "FPS mora biti pozitivan, npr. 30 ili 30000/1001."
        )
    return num, den


def main() -> None:
    parser = argparse.ArgumentParser(description="Pretvara GIF u MIGIF v1.0.")
    parser.add_argument("input", type=Path, help="Ulazni GIF file")
    parser.add_argument("output", type=Path, nargs="?", help="Izlazni .migif file")
    parser.add_argument(
        "--force-rgba",
        action="store_true",
        help="Nemoj koristiti globalnu paletu čak ni ako stane u 256 boja.",
    )
    parser.add_argument(
        "--force-full-every",
        type=int,
        default=0,
        help="Svaki N-ti frame prisili kao FULL frame. 0 = nikad.",
    )
    parser.add_argument(
        "--fps",
        type=parse_fps,
        default=None,
        help="Override FPS, npr. 30 ili 30000/1001",
    )

    args = parser.parse_args()

    input_path: Path = args.input
    output_path: Path = args.output or input_path.with_suffix(".migif")

    convert_gif_to_migif(
        input_path=input_path,
        output_path=output_path,
        force_rgba=args.force_rgba,
        force_full_every=args.force_full_every,
        fps_override=args.fps,
    )

    print(f"Napravljen: {output_path}")


if __name__ == "__main__":
    main()
