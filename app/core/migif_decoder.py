#!/usr/bin/env python3
from __future__ import annotations

import argparse
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import pygame


LE = "<"
MIGIF_VERSION_MAJOR = 2

# =========================
# MIGIF constants
# =========================

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
# Data classes
# =========================

@dataclass
class TransformState:
    pos_x: int = 0
    pos_y: int = 0
    alpha: int = 255
    scale_x: int = 65536
    scale_y: int = 65536
    rotation_deg: int = 0


@dataclass
class Frame:
    frame_type: int
    duration_num: int
    duration_den: int
    payload: bytes


@dataclass
class MigifHeader:
    version_major: int
    version_minor: int
    header_size: int
    flags: int
    canvas_width: int
    canvas_height: int
    frame_count: int
    fps_num: int
    fps_den: int
    blocks_size: int
    frames_offset: int
    file_size: int


@dataclass
class MigifFile:
    header: MigifHeader
    palette: Optional[List[Tuple[int, int, int, int]]]
    frames: List[Frame]


# =========================
# Binary reader
# =========================

class Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def seek(self, pos: int) -> None:
        if pos < 0 or pos > len(self.data):
            raise ValueError("Seek izvan granica datoteke.")
        self.pos = pos

    def tell(self) -> int:
        return self.pos

    def read(self, n: int) -> bytes:
        if n < 0:
            raise ValueError("Negativan broj bajtova za čitanje.")
        if self.pos + n > len(self.data):
            raise ValueError("Neočekivan kraj datoteke.")
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.read(size))


# =========================
# MIGIF parsing
# =========================

def parse_header(r: Reader) -> MigifHeader:
    (
        magic,
        version_major,
        version_minor,
        header_size,
        flags,
        canvas_width,
        canvas_height,
        frame_count,
        fps_num,
        fps_den,
        blocks_size,
        frames_offset,
        file_size,
        reserved0,
        reserved1,
    ) = r.unpack(FILE_HEADER_FMT)

    if magic != b"MIGIF":
        raise ValueError("Nije MIGIF datoteka.")

    if version_major not in (1, MIGIF_VERSION_MAJOR):
        raise ValueError(f"Nepodržan MIGIF major version: {version_major}")

    if header_size < FILE_HEADER_SIZE:
        raise ValueError(
            f"Premalen header_size: {header_size}, očekivano barem {FILE_HEADER_SIZE}"
        )

    if fps_den == 0:
        raise ValueError("fps_den ne smije biti 0.")

    if canvas_width == 0 or canvas_height == 0:
        raise ValueError("Neispravne canvas dimenzije.")

    if frame_count == 0:
        raise ValueError("Frame count ne smije biti 0.")

    if file_size != len(r.data):
        raise ValueError(
            f"Header file_size={file_size}, stvarna veličina={len(r.data)}"
        )

    if frames_offset > file_size:
        raise ValueError("frames_offset izlazi iz datoteke.")

    return MigifHeader(
        version_major=version_major,
        version_minor=version_minor,
        header_size=header_size,
        flags=flags,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        frame_count=frame_count,
        fps_num=fps_num,
        fps_den=fps_den,
        blocks_size=blocks_size,
        frames_offset=frames_offset,
        file_size=file_size,
    )


def parse_blocks(r: Reader, header: MigifHeader) -> Optional[List[Tuple[int, int, int, int]]]:
    palette: Optional[List[Tuple[int, int, int, int]]] = None
    blocks_end = header.header_size + header.blocks_size

    if blocks_end > header.file_size:
        raise ValueError("blocks_size izlazi iz datoteke.")

    r.seek(header.header_size)

    while r.tell() < blocks_end:
        block_start = r.tell()

        magic, block_size = r.unpack(BLOCK_HEADER_FMT)

        if block_size < BLOCK_HEADER_SIZE:
            raise ValueError("Neispravan block_size.")

        if block_start + block_size > blocks_end:
            raise ValueError("Block izlazi iz područja blokova.")

        if magic == b"PLTE":
            color_count, color_format, reserved0, reserved1 = r.unpack(LE + "IBBH")
            if color_format != MIGIF_COLOR_RGBA8888:
                raise ValueError(f"Nepodržan PLTE color_format: {color_format}")

            palette = []
            for _ in range(color_count):
                rr, gg, bb, aa = r.unpack("BBBB")
                palette.append((rr, gg, bb, aa))

        r.seek(block_start + block_size)

    if r.tell() != blocks_end:
        raise ValueError("Parsiranje blokova nije završilo točno na blocks_end.")

    return palette


def parse_frames(r: Reader, header: MigifHeader) -> List[Frame]:
    frames: List[Frame] = []
    r.seek(header.frames_offset)

    for i in range(header.frame_count):
        frame_start = r.tell()

        if frame_start + FRAME_HEADER_SIZE > len(r.data):
            raise ValueError(
                f"Frame {i}: nema dovoljno bajtova ni za frame header."
            )

        (
            frame_size,
            frame_type,
            frame_flags,
            duration_num,
            duration_den,
            payload_size,
            reserved0,
            reserved1,
        ) = r.unpack(FRAME_HEADER_FMT)

        if frame_size < FRAME_HEADER_SIZE:
            raise ValueError(f"Frame {i}: neispravan frame_size.")
        if duration_den == 0:
            raise ValueError(f"Frame {i}: duration_den ne smije biti 0.")
        if frame_size != FRAME_HEADER_SIZE + payload_size:
            raise ValueError(f"Frame {i}: frame_size ne odgovara payload_size.")
        if frame_start + frame_size > len(r.data):
            raise ValueError(f"Frame {i}: frame izlazi iz datoteke.")

        payload = r.read(payload_size)
        frames.append(Frame(
            frame_type=frame_type,
            duration_num=duration_num,
            duration_den=duration_den,
            payload=payload,
        ))

        r.seek(frame_start + frame_size)

    return frames


def load_migif(path: Path) -> MigifFile:
    data = path.read_bytes()
    r = Reader(data)
    header = parse_header(r)
    palette = parse_blocks(r, header)
    frames = parse_frames(r, header)
    return MigifFile(header=header, palette=palette, frames=frames)


# =========================
# Decoding helpers
# =========================

def decode_rle_rgba(data: bytes, pixel_count: int) -> List[Tuple[int, int, int, int]]:
    r = Reader(data)
    out: List[Tuple[int, int, int, int]] = []

    while len(out) < pixel_count:
        run_length, rr, gg, bb, aa = r.unpack("HBBBB")
        if run_length == 0:
            raise ValueError("RLE RGBA run_length ne smije biti 0.")
        out.extend([(rr, gg, bb, aa)] * run_length)

    if len(out) != pixel_count:
        raise ValueError("RLE RGBA dekodirao krivi broj piksela.")

    return out


def decode_rle_rgba_bytes(data: bytes, pixel_count: int) -> bytes:
    r = Reader(data)
    out = bytearray(pixel_count * 4)
    pos = 0

    while pos < len(out):
        run_length, rr, gg, bb, aa = r.unpack("HBBBB")
        if run_length == 0:
            raise ValueError("RLE RGBA run_length ne smije biti 0.")

        chunk = bytes((rr, gg, bb, aa))
        for _ in range(run_length):
            if pos >= len(out):
                raise ValueError("RLE RGBA dekodirao krivi broj piksela.")
            out[pos:pos + 4] = chunk
            pos += 4

    return bytes(out)


def decode_rle_index8(data: bytes, pixel_count: int) -> List[int]:
    r = Reader(data)
    out: List[int] = []

    while len(out) < pixel_count:
        run_length, idx = r.unpack("HB")
        if run_length == 0:
            raise ValueError("RLE INDEX8 run_length ne smije biti 0.")
        out.extend([idx] * run_length)

    if len(out) != pixel_count:
        raise ValueError("RLE INDEX8 dekodirao krivi broj piksela.")

    return out


def decode_rle_index8_bytes(data: bytes, pixel_count: int) -> bytes:
    r = Reader(data)
    out = bytearray(pixel_count)
    pos = 0

    while pos < pixel_count:
        run_length, idx = r.unpack("HB")
        if run_length == 0:
            raise ValueError("RLE INDEX8 run_length ne smije biti 0.")
        if pos + run_length > pixel_count:
            raise ValueError("RLE INDEX8 dekodirao krivi broj piksela.")
        out[pos:pos + run_length] = bytes((idx,)) * run_length
        pos += run_length

    return bytes(out)


def indices_to_rgba(
    indices: List[int],
    palette: Optional[List[Tuple[int, int, int, int]]]
) -> List[Tuple[int, int, int, int]]:
    if palette is None:
        raise ValueError("Indexed frame postoji, ali PLTE ne postoji.")

    out: List[Tuple[int, int, int, int]] = []
    for idx in indices:
        if idx >= len(palette):
            raise ValueError(f"Indeks palete izvan raspona: {idx}")
        out.append(palette[idx])
    return out


def indices_to_rgba_bytes(
    indices: bytes,
    palette: Optional[List[Tuple[int, int, int, int]]]
) -> bytes:
    if palette is None:
        raise ValueError("Indexed frame postoji, ali PLTE ne postoji.")

    out = bytearray(len(indices) * 4)
    pos = 0
    for idx in indices:
        if idx >= len(palette):
            raise ValueError(f"Indeks palete izvan raspona: {idx}")
        rr, gg, bb, aa = palette[idx]
        out[pos:pos + 4] = bytes((rr, gg, bb, aa))
        pos += 4
    return bytes(out)


def decode_full_payload(
    payload: bytes,
    palette: Optional[List[Tuple[int, int, int, int]]]
) -> Tuple[int, int, List[Tuple[int, int, int, int]]]:
    r = Reader(payload)

    if len(payload) < FULL_PAYLOAD_HEADER_SIZE:
        raise ValueError("FULL payload je prekratak.")

    encoding, reserved, width, height, data_size = r.unpack(FULL_PAYLOAD_HEADER_FMT)
    data = r.read(data_size)

    if r.tell() != len(payload):
        raise ValueError("FULL payload ima višak ili manjak podataka.")

    pixel_count = width * height

    if encoding == MIGIF_ENCODING_RAW_RGBA8888:
        if len(data) != pixel_count * 4:
            raise ValueError("RAW_RGBA8888 ima krivu veličinu.")
        pixels = [tuple(data[i:i + 4]) for i in range(0, len(data), 4)]  # type: ignore
        return width, height, pixels

    if encoding == MIGIF_ENCODING_RLE_RGBA8888:
        pixels = decode_rle_rgba(data, pixel_count)
        return width, height, pixels

    if encoding == MIGIF_ENCODING_RAW_INDEX8:
        if len(data) != pixel_count:
            raise ValueError("RAW_INDEX8 ima krivu veličinu.")
        indices = list(data)
        pixels = indices_to_rgba(indices, palette)
        return width, height, pixels

    if encoding == MIGIF_ENCODING_RLE_INDEX8:
        indices = decode_rle_index8(data, pixel_count)
        pixels = indices_to_rgba(indices, palette)
        return width, height, pixels

    raise ValueError(f"Nepodržan FULL encoding: {encoding}")


def decode_full_payload_bytes(
    payload: bytes,
    palette: Optional[List[Tuple[int, int, int, int]]]
) -> Tuple[int, int, bytes]:
    r = Reader(payload)

    if len(payload) < FULL_PAYLOAD_HEADER_SIZE:
        raise ValueError("FULL payload je prekratak.")

    encoding, reserved, width, height, data_size = r.unpack(FULL_PAYLOAD_HEADER_FMT)
    data = r.read(data_size)

    if r.tell() != len(payload):
        raise ValueError("FULL payload ima visak ili manjak podataka.")

    pixel_count = width * height

    if encoding == MIGIF_ENCODING_RAW_RGBA8888:
        if len(data) != pixel_count * 4:
            raise ValueError("RAW_RGBA8888 ima krivu velicinu.")
        return width, height, data

    if encoding == MIGIF_ENCODING_RLE_RGBA8888:
        return width, height, decode_rle_rgba_bytes(data, pixel_count)

    if encoding == MIGIF_ENCODING_RAW_INDEX8:
        if len(data) != pixel_count:
            raise ValueError("RAW_INDEX8 ima krivu velicinu.")
        return width, height, indices_to_rgba_bytes(data, palette)

    if encoding == MIGIF_ENCODING_RLE_INDEX8:
        indices = decode_rle_index8_bytes(data, pixel_count)
        return width, height, indices_to_rgba_bytes(indices, palette)

    raise ValueError(f"Nepodrzan FULL encoding: {encoding}")


def apply_full_to_canvas(
    canvas: List[Tuple[int, int, int, int]],
    canvas_width: int,
    canvas_height: int,
    img_width: int,
    img_height: int,
    pixels: List[Tuple[int, int, int, int]],
) -> None:
    if img_width > canvas_width or img_height > canvas_height:
        raise ValueError("FULL frame veći od canvasa.")

    clear = (0, 0, 0, 0)
    for i in range(len(canvas)):
        canvas[i] = clear

    for y in range(img_height):
        dst_row = y * canvas_width
        src_row = y * img_width
        for x in range(img_width):
            canvas[dst_row + x] = pixels[src_row + x]


def apply_full_to_canvas_bytes(
    canvas: bytearray,
    canvas_width: int,
    canvas_height: int,
    img_width: int,
    img_height: int,
    pixels: bytes,
) -> None:
    if img_width > canvas_width or img_height > canvas_height:
        raise ValueError("FULL frame veci od canvasa.")
    if len(pixels) != img_width * img_height * 4:
        raise ValueError("FULL frame ima krivi broj bajtova piksela.")

    canvas[:] = b"\x00" * len(canvas)

    src_stride = img_width * 4
    dst_stride = canvas_width * 4
    row_bytes = img_width * 4

    for y in range(img_height):
        src_start = y * src_stride
        dst_start = y * dst_stride
        canvas[dst_start:dst_start + row_bytes] = pixels[src_start:src_start + row_bytes]


def decode_delta_rect_pixels(
    encoding: int,
    w: int,
    h: int,
    data: bytes,
    palette: Optional[List[Tuple[int, int, int, int]]],
) -> List[Tuple[int, int, int, int]]:
    pixel_count = w * h

    if encoding == MIGIF_DELTA_RAW_RGBA8888:
        if len(data) != pixel_count * 4:
            raise ValueError("DELTA RAW_RGBA8888 ima krivu veličinu.")
        return [tuple(data[i:i + 4]) for i in range(0, len(data), 4)]  # type: ignore

    if encoding == MIGIF_DELTA_RLE_RGBA8888:
        return decode_rle_rgba(data, pixel_count)

    if encoding == MIGIF_DELTA_RAW_INDEX8:
        if len(data) != pixel_count:
            raise ValueError("DELTA RAW_INDEX8 ima krivu veličinu.")
        return indices_to_rgba(list(data), palette)

    if encoding == MIGIF_DELTA_RLE_INDEX8:
        indices = decode_rle_index8(data, pixel_count)
        return indices_to_rgba(indices, palette)

    raise ValueError(f"Nepodržan DELTA encoding: {encoding}")


def decode_delta_rect_pixels_bytes(
    encoding: int,
    w: int,
    h: int,
    data: bytes,
    palette: Optional[List[Tuple[int, int, int, int]]],
) -> bytes:
    pixel_count = w * h

    if encoding == MIGIF_DELTA_RAW_RGBA8888:
        if len(data) != pixel_count * 4:
            raise ValueError("DELTA RAW_RGBA8888 ima krivu velicinu.")
        return data

    if encoding == MIGIF_DELTA_RLE_RGBA8888:
        return decode_rle_rgba_bytes(data, pixel_count)

    if encoding == MIGIF_DELTA_RAW_INDEX8:
        if len(data) != pixel_count:
            raise ValueError("DELTA RAW_INDEX8 ima krivu velicinu.")
        return indices_to_rgba_bytes(data, palette)

    if encoding == MIGIF_DELTA_RLE_INDEX8:
        indices = decode_rle_index8_bytes(data, pixel_count)
        return indices_to_rgba_bytes(indices, palette)

    raise ValueError(f"Nepodrzan DELTA encoding: {encoding}")


def apply_delta_payload(
    payload: bytes,
    canvas: List[Tuple[int, int, int, int]],
    canvas_width: int,
    canvas_height: int,
    palette: Optional[List[Tuple[int, int, int, int]]],
) -> None:
    r = Reader(payload)

    if len(payload) < DELTA_PAYLOAD_HEADER_SIZE:
        raise ValueError("DELTA payload je prekratak.")

    encoding, rect_count = r.unpack(DELTA_PAYLOAD_HEADER_FMT)

    for _ in range(rect_count):
        if len(payload) - r.tell() < DELTA_RECT_HEADER_SIZE:
            raise ValueError("DELTA rect header izlazi iz payloada.")

        x, y, w, h, data_size = r.unpack(DELTA_RECT_HEADER_FMT)
        data = r.read(data_size)

        if w == 0 or h == 0:
            raise ValueError("DELTA rect ne smije imati 0 dimenziju.")
        if x + w > canvas_width or y + h > canvas_height:
            raise ValueError("DELTA rect izlazi iz canvasa.")

        rect_pixels = decode_delta_rect_pixels(encoding, w, h, data, palette)

        for yy in range(h):
            dst_row = (y + yy) * canvas_width
            src_row = yy * w
            for xx in range(w):
                canvas[dst_row + x + xx] = rect_pixels[src_row + xx]

    if r.tell() != len(payload):
        raise ValueError("DELTA payload ima višak ili manjak podataka.")


def apply_delta_payload_bytes(
    payload: bytes,
    canvas: bytearray,
    canvas_width: int,
    canvas_height: int,
    palette: Optional[List[Tuple[int, int, int, int]]],
) -> None:
    r = Reader(payload)

    if len(payload) < DELTA_PAYLOAD_HEADER_SIZE:
        raise ValueError("DELTA payload je prekratak.")

    encoding, rect_count = r.unpack(DELTA_PAYLOAD_HEADER_FMT)
    canvas_stride = canvas_width * 4

    for _ in range(rect_count):
        if len(payload) - r.tell() < DELTA_RECT_HEADER_SIZE:
            raise ValueError("DELTA rect header izlazi iz payloada.")

        x, y, w, h, data_size = r.unpack(DELTA_RECT_HEADER_FMT)
        data = r.read(data_size)

        if w == 0 or h == 0:
            raise ValueError("DELTA rect ne smije imati 0 dimenziju.")
        if x + w > canvas_width or y + h > canvas_height:
            raise ValueError("DELTA rect izlazi iz canvasa.")

        rect_pixels = decode_delta_rect_pixels_bytes(encoding, w, h, data, palette)
        row_bytes = w * 4

        for yy in range(h):
            src_start = yy * row_bytes
            dst_start = (y + yy) * canvas_stride + x * 4
            canvas[dst_start:dst_start + row_bytes] = rect_pixels[src_start:src_start + row_bytes]

    if r.tell() != len(payload):
        raise ValueError("DELTA payload ima visak ili manjak podataka.")


def apply_transform_payload(payload: bytes, state: TransformState) -> None:
    if len(payload) != TRANSFORM_PAYLOAD_SIZE:
        raise ValueError("TRANSFORM payload ima krivu veličinu.")

    r = Reader(payload)
    transform_flags, pos_x, pos_y, alpha, scale_x, scale_y, rotation_deg = r.unpack(
        TRANSFORM_PAYLOAD_FMT
    )

    if transform_flags & MIGIF_TF_POS_X:
        state.pos_x = pos_x
    if transform_flags & MIGIF_TF_POS_Y:
        state.pos_y = pos_y
    if transform_flags & MIGIF_TF_ALPHA:
        if alpha > 255:
            raise ValueError("Transform alpha mora biti 0..255.")
        state.alpha = alpha
    if transform_flags & MIGIF_TF_SCALE_X:
        state.scale_x = scale_x
    if transform_flags & MIGIF_TF_SCALE_Y:
        state.scale_y = scale_y
    if transform_flags & MIGIF_TF_ROTATION:
        state.rotation_deg = rotation_deg


# =========================
# Rendering
# =========================

def canvas_to_surface(
    canvas: List[Tuple[int, int, int, int]],
    width: int,
    height: int,
) -> pygame.Surface:
    surf = pygame.Surface((width, height), pygame.SRCALPHA, 32)

    i = 0
    for y in range(height):
        for x in range(width):
            surf.set_at((x, y), canvas[i])
            i += 1

    return surf


def canvas_bytes_to_surface(
    canvas: bytearray,
    width: int,
    height: int,
) -> pygame.Surface:
    return pygame.image.frombuffer(canvas, (width, height), "RGBA")


def compose_transformed_surface(
    base_surface: pygame.Surface,
    transform: TransformState,
) -> Tuple[pygame.Surface, Tuple[int, int]]:
    surf = base_surface.copy()

    alpha = max(0, min(255, transform.alpha))
    surf.set_alpha(alpha)

    scale_x = transform.scale_x / 65536.0
    scale_y = transform.scale_y / 65536.0

    if scale_x <= 0 or scale_y <= 0:
        tiny = pygame.Surface((1, 1), pygame.SRCALPHA, 32)
        tiny.fill((0, 0, 0, 0))
        return tiny, (0, 0)

    width, height = surf.get_size()
    new_w = max(1, int(round(width * scale_x)))
    new_h = max(1, int(round(height * scale_y)))

    if new_w != width or new_h != height:
        surf = pygame.transform.smoothscale(surf, (new_w, new_h))

    rotation_deg = transform.rotation_deg / 65536.0
    if rotation_deg != 0.0:
        surf = pygame.transform.rotate(surf, -rotation_deg)

    pos_x = int(round(transform.pos_x / 65536.0))
    pos_y = int(round(transform.pos_y / 65536.0))
    return surf, (pos_x, pos_y)


def render_frame_to_surface(
    target: pygame.Surface,
    base_surface: pygame.Surface,
    transform: TransformState,
    background: Tuple[int, int, int],
) -> None:
    target.fill(background)
    if (
        transform.pos_x == 0
        and transform.pos_y == 0
        and transform.alpha == 255
        and transform.scale_x == 65536
        and transform.scale_y == 65536
        and transform.rotation_deg == 0
    ):
        target.blit(base_surface, (0, 0))
        return
    surf, pos = compose_transformed_surface(base_surface, transform)
    target.blit(surf, pos)


# =========================
# Player
# =========================

def average_timeline_fps(migif: MigifFile) -> float:
    total_duration = 0.0
    for frame in migif.frames:
        total_duration += frame.duration_num / frame.duration_den
    if total_duration <= 0.0:
        return 0.0
    return len(migif.frames) / total_duration


def play_migif(
    migif: MigifFile,
    scale_window: int = 1,
    background: Tuple[int, int, int] = (0, 0, 0),
) -> None:
    pygame.init()

    canvas_w = migif.header.canvas_width
    canvas_h = migif.header.canvas_height

    window_w = canvas_w * scale_window
    window_h = canvas_h * scale_window

    screen = pygame.display.set_mode((window_w, window_h))
    header_fps = migif.header.fps_num / migif.header.fps_den
    timeline_fps = average_timeline_fps(migif)
    pygame.display.set_caption(
        f"MIGIF Player | actual FPS: -- | timeline FPS: {timeline_fps:.2f} | "
        f"header FPS: {header_fps:.2f}"
    )

    unscaled_present = pygame.Surface((canvas_w, canvas_h), pygame.SRCALPHA, 32)

    canvas = bytearray(canvas_w * canvas_h * 4)
    transform = TransformState()

    clock = pygame.time.Clock()
    running = True
    loop = bool(migif.header.flags & MIGIF_FLAG_LOOP)
    fps_counter_start = time.perf_counter()
    fps_counter_frames = 0

    while running:
        for frame in migif.frames:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False

            if not running:
                break

            if frame.frame_type == MIGIF_FRAME_FULL:
                img_w, img_h, pixels = decode_full_payload_bytes(frame.payload, migif.palette)
                apply_full_to_canvas_bytes(canvas, canvas_w, canvas_h, img_w, img_h, pixels)
                transform = TransformState()

            elif frame.frame_type == MIGIF_FRAME_DELTA:
                apply_delta_payload_bytes(frame.payload, canvas, canvas_w, canvas_h, migif.palette)

            elif frame.frame_type == MIGIF_FRAME_TRANSFORM:
                apply_transform_payload(frame.payload, transform)

            else:
                raise ValueError(f"Nepodržan frame type: {frame.frame_type}")

            canvas_surface = canvas_bytes_to_surface(canvas, canvas_w, canvas_h)
            render_frame_to_surface(unscaled_present, canvas_surface, transform, background)

            if scale_window == 1:
                screen.blit(unscaled_present, (0, 0))
            else:
                scaled = pygame.transform.scale(unscaled_present, (window_w, window_h))
                screen.blit(scaled, (0, 0))

            pygame.display.flip()
            fps_counter_frames += 1

            now = time.perf_counter()
            elapsed_fps_window = now - fps_counter_start
            if elapsed_fps_window >= 0.5:
                actual_fps = fps_counter_frames / elapsed_fps_window
                pygame.display.set_caption(
                    f"MIGIF Player | actual FPS: {actual_fps:.2f} | "
                    f"timeline FPS: {timeline_fps:.2f} | header FPS: {header_fps:.2f}"
                )
                fps_counter_start = now
                fps_counter_frames = 0

            duration_sec = frame.duration_num / frame.duration_den
            end_time = time.perf_counter() + duration_sec

            while running and time.perf_counter() < end_time:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        running = False
                clock.tick(240)

        if not running or not loop:
            break

    pygame.quit()


# =========================
# CLI
# =========================

def parse_bg(value: str) -> Tuple[int, int, int]:
    parts = value.split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("Pozadina mora biti npr. 0,0,0")
    try:
        rgb = tuple(int(x) for x in parts)
    except ValueError as e:
        raise argparse.ArgumentTypeError("Pozadina mora biti npr. 0,0,0") from e
    if any(v < 0 or v > 255 for v in rgb):
        raise argparse.ArgumentTypeError("RGB vrijednosti moraju biti 0..255")
    return rgb  # type: ignore


def main() -> None:
    parser = argparse.ArgumentParser(description="Prikazuje MIGIF na ekranu.")
    parser.add_argument("input", type=Path, help="Ulazni .migif file")
    parser.add_argument("--scale", type=int, default=1, help="Skaliranje prozora, npr. 2")
    parser.add_argument("--bg", type=parse_bg, default=(0, 0, 0), help="Pozadina npr. 0,0,0")
    args = parser.parse_args()

    if args.scale <= 0:
        print("Greška: --scale mora biti >= 1", file=sys.stderr)
        sys.exit(1)

    migif = load_migif(args.input)
    play_migif(migif, scale_window=args.scale, background=args.bg)


if __name__ == "__main__":
    main()
