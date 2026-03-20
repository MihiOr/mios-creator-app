#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
except ImportError as exc:
    raise SystemExit("This editor requires Pillow: pip install pillow") from exc


LE = "<"
MIGIF_VERSION_MAJOR = 2
PROJECT_MAGIC = b"MIPRJ"
PROJECT_VERSION_MAJOR = 1

# =========================
# MIGIF constants / formats
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

DEFAULT_FPS_NUM = 30
DEFAULT_FPS_DEN = 1
PREVIEW_CANVAS_BG = (28, 30, 34, 255)


# =========================
# Reader / data classes
# =========================

class Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def read(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise ValueError("Unexpected end of file")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def seek(self, pos: int) -> None:
        if pos < 0 or pos > len(self.data):
            raise ValueError("Seek out of range")
        self.pos = pos

    def tell(self) -> int:
        return self.pos

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.read(size))


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
class Frame:
    frame_type: int
    duration_num: int
    duration_den: int
    payload: bytes


@dataclass
class MigifFile:
    header: MigifHeader
    palette: Optional[List[Tuple[int, int, int, int]]]
    frames: List[Frame]


@dataclass
class Keyframe:
    frame_index: int
    x: float = 0.0
    y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation_deg: float = 0.0
    alpha: int = 255


@dataclass
class SourceAsset:
    source_id: int
    path: Path
    migif: MigifFile
    frames: List[Image.Image]
    durations: List[Tuple[int, int]]


@dataclass
class Clip:
    clip_id: int
    source_id: int
    timeline_start_frame: int = 0
    source_start_frame: int = 0
    frame_count: int = 0
    name: str = ""
    keyframes: List[Keyframe] = field(default_factory=list)


@dataclass
class Project:
    sources: List[SourceAsset] = field(default_factory=list)
    clips: List[Clip] = field(default_factory=list)
    out_width: int = 0
    out_height: int = 0
    loop: bool = False
    preview_fps_num: int = DEFAULT_FPS_NUM
    preview_fps_den: int = DEFAULT_FPS_DEN
    load_warnings: List[str] = field(default_factory=list)


@dataclass
class TimelineContext:
    clip: Clip
    clip_index: int
    local_frame_index: int
    source: SourceAsset
    source_frame_index: int
    duration_num: int
    duration_den: int


@dataclass
class ProjectHeader:
    version_major: int
    version_minor: int
    header_size: int
    flags: int
    out_width: int
    out_height: int
    total_frames: int
    preview_fps_num: int
    preview_fps_den: int
    blocks_size: int
    blocks_offset: int
    file_size: int
    source_count: int
    clip_count: int


def load_source_asset(path: Path, source_id: int) -> SourceAsset:
    migif = load_migif(path)
    frames, durations = render_source_timeline(migif)
    if not frames:
        raise ValueError("No renderable frames in MIGIF")
    return SourceAsset(
        source_id=source_id,
        path=path,
        migif=migif,
        frames=frames,
        durations=durations,
    )


def clip_frame_count(project: Project, clip: Clip) -> int:
    source = source_by_id(project, clip.source_id)
    available = max(0, len(source.frames) - clip.source_start_frame)
    if clip.frame_count <= 0:
        return available
    return min(clip.frame_count, available)


def project_total_frames(project: Project) -> int:
    max_end = 0
    for clip in project.clips:
        max_end = max(max_end, clip.timeline_start_frame + clip_frame_count(project, clip))
    return max_end


def clip_start_frame(project: Project, clip_index: int) -> int:
    if clip_index < 0 or clip_index >= len(project.clips):
        return 0
    return max(0, project.clips[clip_index].timeline_start_frame)


def source_by_id(project: Project, source_id: int) -> SourceAsset:
    for source in project.sources:
        if source.source_id == source_id:
            return source
    raise ValueError(f"Unknown source_id: {source_id}")


def clip_label(project: Project, clip: Clip) -> str:
    source = source_by_id(project, clip.source_id)
    return clip.name or source.path.name


def make_default_clip(project: Project, source: SourceAsset) -> Clip:
    return Clip(
        clip_id=len(project.clips),
        source_id=source.source_id,
        timeline_start_frame=0,
        source_start_frame=0,
        frame_count=len(source.frames),
        name=source.path.stem,
        keyframes=[Keyframe(frame_index=0)],
    )


def create_project_from_source(path: Path) -> Project:
    source = load_source_asset(path, source_id=0)
    project = Project(
        sources=[source],
        out_width=max(source.migif.header.canvas_width, 800),
        out_height=max(source.migif.header.canvas_height, 600),
        loop=bool(source.migif.header.flags & MIGIF_FLAG_LOOP),
        preview_fps_num=source.migif.header.fps_num or DEFAULT_FPS_NUM,
        preview_fps_den=source.migif.header.fps_den or DEFAULT_FPS_DEN,
    )
    project.clips.append(make_default_clip(project, source))
    return project


def append_source_to_project(project: Project, path: Path, timeline_start_frame: int = 0) -> None:
    source = load_source_asset(path, source_id=len(project.sources))
    project.sources.append(source)
    clip = make_default_clip(project, source)
    clip.timeline_start_frame = max(0, timeline_start_frame)
    project.clips.append(clip)
    project.out_width = max(project.out_width, source.migif.header.canvas_width)
    project.out_height = max(project.out_height, source.migif.header.canvas_height)


def clip_context_at_frame(project: Project, clip_index: int, frame_index: int) -> Optional[TimelineContext]:
    if clip_index < 0 or clip_index >= len(project.clips):
        return None

    clip = project.clips[clip_index]
    length = clip_frame_count(project, clip)
    local_frame_index = frame_index - clip.timeline_start_frame
    if local_frame_index < 0 or local_frame_index >= length:
        return None

    source = source_by_id(project, clip.source_id)
    source_frame_index = clip.source_start_frame + local_frame_index
    if source_frame_index < 0 or source_frame_index >= len(source.frames):
        return None
    duration_num, duration_den = source.durations[source_frame_index]
    return TimelineContext(
        clip=clip,
        clip_index=clip_index,
        local_frame_index=local_frame_index,
        source=source,
        source_frame_index=source_frame_index,
        duration_num=duration_num,
        duration_den=duration_den,
    )


def active_timeline_contexts(project: Project, frame_index: int) -> List[TimelineContext]:
    contexts: List[TimelineContext] = []
    for clip_index, clip in enumerate(project.clips):
        context = clip_context_at_frame(project, clip_index, frame_index)
        if context is not None:
            contexts.append(context)
    return contexts


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
        _reserved0,
        _reserved1,
    ) = r.unpack(FILE_HEADER_FMT)

    if magic != b"MIGIF":
        raise ValueError("Not a MIGIF file")
    if version_major not in (1, MIGIF_VERSION_MAJOR):
        raise ValueError(f"Unsupported MIGIF major version: {version_major}")
    if fps_den == 0:
        raise ValueError("fps_den must not be 0")
    if file_size != len(r.data):
        raise ValueError(f"Header file_size={file_size}, actual={len(r.data)}")
    if frames_offset > file_size:
        raise ValueError("frames_offset out of bounds")

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
        raise ValueError("blocks section out of bounds")

    r.seek(header.header_size)

    while r.tell() < blocks_end:
        block_start = r.tell()
        magic, block_size = r.unpack(BLOCK_HEADER_FMT)

        if block_size < BLOCK_HEADER_SIZE:
            raise ValueError("Invalid block_size")

        if block_start + block_size > blocks_end:
            raise ValueError("Block out of bounds")

        if magic == b"PLTE":
            color_count, color_format, _reserved0, _reserved1 = r.unpack("<IBBH")
            if color_format != MIGIF_COLOR_RGBA8888:
                raise ValueError("Unsupported palette color format")
            palette = []
            for _ in range(color_count):
                rr, gg, bb, aa = r.unpack("BBBB")
                palette.append((rr, gg, bb, aa))

        r.seek(block_start + block_size)

    return palette


def parse_frames(r: Reader, header: MigifHeader) -> List[Frame]:
    frames: List[Frame] = []
    r.seek(header.frames_offset)

    for i in range(header.frame_count):
        frame_start = r.tell()

        if frame_start + FRAME_HEADER_SIZE > len(r.data):
            raise ValueError(f"Frame {i}: missing frame header")

        (
            frame_size,
            frame_type,
            _frame_flags,
            duration_num,
            duration_den,
            payload_size,
            _reserved0,
            _reserved1,
        ) = r.unpack(FRAME_HEADER_FMT)

        if duration_den == 0:
            raise ValueError(f"Frame {i}: duration_den must not be 0")
        if frame_size != FRAME_HEADER_SIZE + payload_size:
            raise ValueError(f"Frame {i}: frame_size mismatch")
        if frame_start + frame_size > len(r.data):
            raise ValueError(f"Frame {i}: out of file bounds")

        payload = r.read(payload_size)
        frames.append(Frame(frame_type, duration_num, duration_den, payload))
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
# Decode helpers
# =========================

def decode_rle_rgba(data: bytes, pixel_count: int) -> List[Tuple[int, int, int, int]]:
    r = Reader(data)
    out: List[Tuple[int, int, int, int]] = []

    while len(out) < pixel_count:
        run_length, rr, gg, bb, aa = r.unpack("<HBBBB")
        if run_length == 0:
            raise ValueError("Invalid RLE RGBA token")
        out.extend([(rr, gg, bb, aa)] * run_length)

    if len(out) != pixel_count:
        raise ValueError("RLE RGBA decoded wrong pixel count")

    return out


def decode_rle_index8(data: bytes, pixel_count: int) -> List[int]:
    r = Reader(data)
    out: List[int] = []

    while len(out) < pixel_count:
        run_length, idx = r.unpack("<HB")
        if run_length == 0:
            raise ValueError("Invalid RLE INDEX8 token")
        out.extend([idx] * run_length)

    if len(out) != pixel_count:
        raise ValueError("RLE INDEX8 decoded wrong pixel count")

    return out


def indices_to_rgba(indices: List[int], palette: Optional[List[Tuple[int, int, int, int]]]) -> List[Tuple[int, int, int, int]]:
    if palette is None:
        raise ValueError("Indexed frame without palette")
    out: List[Tuple[int, int, int, int]] = []
    for idx in indices:
        if idx >= len(palette):
            raise ValueError(f"Palette index out of range: {idx}")
        out.append(palette[idx])
    return out


def decode_full_payload(payload: bytes, palette: Optional[List[Tuple[int, int, int, int]]]) -> Tuple[int, int, List[Tuple[int, int, int, int]]]:
    r = Reader(payload)

    if len(payload) < FULL_PAYLOAD_HEADER_SIZE:
        raise ValueError("FULL payload too short")

    encoding, _reserved, width, height, data_size = r.unpack(FULL_PAYLOAD_HEADER_FMT)
    data = r.read(data_size)
    pixel_count = width * height

    if encoding == MIGIF_ENCODING_RAW_RGBA8888:
        if len(data) != pixel_count * 4:
            raise ValueError("Invalid RAW_RGBA8888 size")
        pixels = [tuple(data[i:i + 4]) for i in range(0, len(data), 4)]  # type: ignore[list-item]
        return width, height, pixels

    if encoding == MIGIF_ENCODING_RLE_RGBA8888:
        return width, height, decode_rle_rgba(data, pixel_count)

    if encoding == MIGIF_ENCODING_RAW_INDEX8:
        if len(data) != pixel_count:
            raise ValueError("Invalid RAW_INDEX8 size")
        return width, height, indices_to_rgba(list(data), palette)

    if encoding == MIGIF_ENCODING_RLE_INDEX8:
        return width, height, indices_to_rgba(decode_rle_index8(data, pixel_count), palette)

    raise ValueError(f"Unsupported FULL encoding: {encoding}")


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
            raise ValueError("Invalid DELTA RAW_RGBA8888 size")
        return [tuple(data[i:i + 4]) for i in range(0, len(data), 4)]  # type: ignore[list-item]

    if encoding == MIGIF_DELTA_RLE_RGBA8888:
        return decode_rle_rgba(data, pixel_count)

    if encoding == MIGIF_DELTA_RAW_INDEX8:
        if len(data) != pixel_count:
            raise ValueError("Invalid DELTA RAW_INDEX8 size")
        return indices_to_rgba(list(data), palette)

    if encoding == MIGIF_DELTA_RLE_INDEX8:
        return indices_to_rgba(decode_rle_index8(data, pixel_count), palette)

    raise ValueError(f"Unsupported DELTA encoding: {encoding}")


def apply_full_to_canvas(
    canvas: List[Tuple[int, int, int, int]],
    canvas_width: int,
    canvas_height: int,
    img_width: int,
    img_height: int,
    pixels: List[Tuple[int, int, int, int]],
) -> None:
    if img_width > canvas_width or img_height > canvas_height:
        raise ValueError("FULL frame larger than canvas")

    clear = (0, 0, 0, 0)
    for i in range(len(canvas)):
        canvas[i] = clear

    for y in range(img_height):
        dst_row = y * canvas_width
        src_row = y * img_width
        for x in range(img_width):
            canvas[dst_row + x] = pixels[src_row + x]


def apply_delta_payload(
    payload: bytes,
    canvas: List[Tuple[int, int, int, int]],
    canvas_width: int,
    canvas_height: int,
    palette: Optional[List[Tuple[int, int, int, int]]],
) -> None:
    r = Reader(payload)

    if len(payload) < DELTA_PAYLOAD_HEADER_SIZE:
        raise ValueError("DELTA payload too short")

    encoding, rect_count = r.unpack(DELTA_PAYLOAD_HEADER_FMT)

    for _ in range(rect_count):
        if len(payload) - r.tell() < DELTA_RECT_HEADER_SIZE:
            raise ValueError("Invalid DELTA rect header")

        x, y, w, h, data_size = r.unpack(DELTA_RECT_HEADER_FMT)
        data = r.read(data_size)
        rect_pixels = decode_delta_rect_pixels(encoding, w, h, data, palette)

        for yy in range(h):
            dst_row = (y + yy) * canvas_width
            src_row = yy * w
            for xx in range(w):
                canvas[dst_row + x + xx] = rect_pixels[src_row + xx]


def render_source_timeline(
    migif: MigifFile,
) -> Tuple[List[Image.Image], List[Tuple[int, int]]]:
    canvas_w = migif.header.canvas_width
    canvas_h = migif.header.canvas_height
    canvas: List[Tuple[int, int, int, int]] = [(0, 0, 0, 0)] * (canvas_w * canvas_h)

    rendered: List[Image.Image] = []
    durations: List[Tuple[int, int]] = []
    transform_alpha = 255

    for frame in migif.frames:
        if frame.frame_type == MIGIF_FRAME_FULL:
            img_w, img_h, pixels = decode_full_payload(frame.payload, migif.palette)
            apply_full_to_canvas(canvas, canvas_w, canvas_h, img_w, img_h, pixels)
            transform_alpha = 255

        elif frame.frame_type == MIGIF_FRAME_DELTA:
            apply_delta_payload(frame.payload, canvas, canvas_w, canvas_h, migif.palette)

        elif frame.frame_type == MIGIF_FRAME_TRANSFORM:
            if len(frame.payload) == TRANSFORM_PAYLOAD_SIZE:
                flags, _x, _y, alpha, _sx, _sy, _rot = struct.unpack(
                    TRANSFORM_PAYLOAD_FMT, frame.payload
                )
                if flags & MIGIF_TF_ALPHA:
                    transform_alpha = max(0, min(255, alpha))

        else:
            raise ValueError(f"Unsupported source frame type: {frame.frame_type}")

        raw = bytearray()
        for r, g, b, a in canvas:
            final_a = (a * transform_alpha) // 255
            raw += bytes((r, g, b, final_a))

        rendered.append(Image.frombytes("RGBA", (canvas_w, canvas_h), bytes(raw)))
        durations.append((frame.duration_num, frame.duration_den))

    return rendered, durations


# =========================
# Export helpers
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


def pack_frame(frame_type: int, duration_num: int, duration_den: int, payload: bytes) -> bytes:
    payload_size = len(payload)
    frame_size = FRAME_HEADER_SIZE + payload_size
    header = struct.pack(
        FRAME_HEADER_FMT,
        frame_size,
        frame_type,
        0,
        duration_num,
        duration_den,
        payload_size,
        0,
        0,
    )
    return header + payload


def encode_rle_rgba_bytes(data: bytes) -> bytes:
    if not data:
        return b""
    if len(data) % 4 != 0:
        raise ValueError("RGBA buffer length must be divisible by 4")

    out = bytearray()
    run_color = data[:4]
    run_len = 1

    for i in range(4, len(data), 4):
        color = data[i:i + 4]
        if color == run_color and run_len < 65535:
            run_len += 1
        else:
            out += struct.pack(LE + "HBBBB", run_len, *run_color)
            run_color = color
            run_len = 1

    out += struct.pack(LE + "HBBBB", run_len, *run_color)
    return bytes(out)


def choose_full_encoding_rgba_bytes(data: bytes) -> Tuple[int, bytes]:
    rle_data = encode_rle_rgba_bytes(data)
    if len(rle_data) < len(data):
        return MIGIF_ENCODING_RLE_RGBA8888, rle_data
    return MIGIF_ENCODING_RAW_RGBA8888, data


def choose_delta_encoding_rgba_bytes(data: bytes) -> Tuple[int, bytes]:
    rle_data = encode_rle_rgba_bytes(data)
    if len(rle_data) < len(data):
        return MIGIF_DELTA_RLE_RGBA8888, rle_data
    return MIGIF_DELTA_RAW_RGBA8888, data


def bbox_from_rgba_diff(
    prev_data: bytes,
    curr_data: bytes,
    width: int,
    height: int,
) -> Optional[Tuple[int, int, int, int]]:
    if len(prev_data) != len(curr_data):
        raise ValueError("Frame size mismatch while building delta export")

    min_x = width
    min_y = height
    max_x = -1
    max_y = -1
    stride = width * 4

    for y in range(height):
        row_start = y * stride
        for x in range(width):
            px_start = row_start + x * 4
            if prev_data[px_start:px_start + 4] != curr_data[px_start:px_start + 4]:
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


def crop_rgba_bytes(
    data: bytes,
    width: int,
    x: int,
    y: int,
    w: int,
    h: int,
) -> bytes:
    stride = width * 4
    row_bytes = w * 4
    out = bytearray(row_bytes * h)

    for yy in range(h):
        src_start = (y + yy) * stride + x * 4
        dst_start = yy * row_bytes
        out[dst_start:dst_start + row_bytes] = data[src_start:src_start + row_bytes]

    return bytes(out)


def pack_full_payload(encoding: int, width: int, height: int, data: bytes) -> bytes:
    payload = struct.pack(
        FULL_PAYLOAD_HEADER_FMT,
        encoding,
        0,
        width,
        height,
        len(data),
    ) + data
    return payload


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


def pack_full_rgba_frame(
    rgba_data: bytes,
    width: int,
    height: int,
    duration_num: int,
    duration_den: int,
) -> bytes:
    raw_data = rgba_data
    encoding, data = choose_full_encoding_rgba_bytes(raw_data)
    payload = pack_full_payload(encoding, width, height, data)
    return pack_frame(MIGIF_FRAME_FULL, duration_num, duration_den, payload)


def pack_delta_rgba_frame(
    prev_data: bytes,
    curr_data: bytes,
    width: int,
    height: int,
    duration_num: int,
    duration_den: int,
) -> bytes:
    bbox = bbox_from_rgba_diff(prev_data, curr_data, width, height)
    if bbox is None:
        payload = struct.pack(DELTA_PAYLOAD_HEADER_FMT, MIGIF_DELTA_RAW_RGBA8888, 0)
        return pack_frame(MIGIF_FRAME_DELTA, duration_num, duration_den, payload)

    x, y, w, h = bbox
    rect_rgba = crop_rgba_bytes(curr_data, width, x, y, w, h)
    encoding, rect_data = choose_delta_encoding_rgba_bytes(rect_rgba)
    payload = pack_delta_payload(encoding, [(x, y, w, h, rect_data)])
    return pack_frame(MIGIF_FRAME_DELTA, duration_num, duration_den, payload)


def export_project(
    project: Project,
    out_path: Path,
    progress_callback: Optional[Callable[[int, int, float], None]] = None,
) -> None:
    total_frames = project_total_frames(project)
    if total_frames <= 0:
        raise ValueError("Project has no renderable frames")

    frames_out: List[bytes] = []
    flags = MIGIF_FLAG_LOOP if project.loop else 0
    prev_rgba: Optional[bytes] = None

    export_start = time.perf_counter()

    for i in range(total_frames):
        rendered = render_project_frame(project, i)
        rgba = rendered.convert("RGBA")
        rgba_data = rgba.tobytes()
        width, height = rgba.size
        duration_num = project.preview_fps_den or DEFAULT_FPS_DEN
        duration_den = project.preview_fps_num or DEFAULT_FPS_NUM

        full_frame = pack_full_rgba_frame(
            rgba_data,
            width,
            height,
            duration_num,
            duration_den,
        )
        if prev_rgba is None:
            frames_out.append(full_frame)
        else:
            delta_frame = pack_delta_rgba_frame(
                prev_rgba,
                rgba_data,
                width,
                height,
                duration_num,
                duration_den,
            )
            if len(delta_frame) < len(full_frame):
                frames_out.append(delta_frame)
            else:
                frames_out.append(full_frame)

        prev_rgba = rgba_data

        if progress_callback is not None:
            done = i + 1
            elapsed = max(0.001, time.perf_counter() - export_start)
            eta_seconds = (elapsed / done) * max(0, total_frames - done)
            progress_callback(done, total_frames, eta_seconds)

    blocks = bytearray()
    blocks_size = len(blocks)
    frames_offset = FILE_HEADER_SIZE + blocks_size
    frames_data = b"".join(frames_out)
    file_size = frames_offset + len(frames_data)

    header = pack_file_header(
        canvas_width=project.out_width,
        canvas_height=project.out_height,
        frame_count=len(frames_out),
        fps_num=project.preview_fps_num or DEFAULT_FPS_NUM,
        fps_den=project.preview_fps_den or DEFAULT_FPS_DEN,
        blocks_size=blocks_size,
        frames_offset=frames_offset,
        file_size=file_size,
        flags=flags,
    )

    out_path.write_bytes(header + bytes(blocks) + frames_data)


def pack_project_header(
    out_width: int,
    out_height: int,
    total_frames: int,
    preview_fps_num: int,
    preview_fps_den: int,
    blocks_size: int,
    blocks_offset: int,
    file_size: int,
    source_count: int,
    clip_count: int,
) -> bytes:
    return struct.pack(
        FILE_HEADER_FMT,
        PROJECT_MAGIC,
        PROJECT_VERSION_MAJOR,
        0,
        FILE_HEADER_SIZE,
        0,
        out_width,
        out_height,
        total_frames,
        preview_fps_num,
        preview_fps_den,
        blocks_size,
        blocks_offset,
        file_size,
        source_count,
        clip_count,
    )


def parse_project_header(r: Reader) -> ProjectHeader:
    (
        magic,
        version_major,
        version_minor,
        header_size,
        flags,
        out_width,
        out_height,
        total_frames,
        preview_fps_num,
        preview_fps_den,
        blocks_size,
        blocks_offset,
        file_size,
        source_count,
        clip_count,
    ) = r.unpack(FILE_HEADER_FMT)

    if magic != PROJECT_MAGIC:
        raise ValueError("Not a project file")
    if version_major != PROJECT_VERSION_MAJOR:
        raise ValueError(f"Unsupported project major version: {version_major}")
    if preview_fps_den == 0:
        raise ValueError("Project preview fps denominator must not be 0")
    if file_size != len(r.data):
        raise ValueError(f"Project file_size={file_size}, actual={len(r.data)}")
    if blocks_offset > file_size:
        raise ValueError("Project blocks_offset out of bounds")

    return ProjectHeader(
        version_major=version_major,
        version_minor=version_minor,
        header_size=header_size,
        flags=flags,
        out_width=out_width,
        out_height=out_height,
        total_frames=total_frames,
        preview_fps_num=preview_fps_num,
        preview_fps_den=preview_fps_den,
        blocks_size=blocks_size,
        blocks_offset=blocks_offset,
        file_size=file_size,
        source_count=source_count,
        clip_count=clip_count,
    )


def serialize_project_path(project_path: Path, source_path: Path) -> str:
    try:
        return str(source_path.resolve().relative_to(project_path.parent.resolve()))
    except ValueError:
        return str(source_path.resolve())


def resolve_project_path(project_path: Path, stored_path: str) -> Path:
    candidate = Path(stored_path)
    if candidate.is_absolute():
        return candidate
    return (project_path.parent / candidate).resolve()


def project_to_payload(project: Project, project_path: Path) -> bytes:
    data = {
        "out_width": project.out_width,
        "out_height": project.out_height,
        "loop": project.loop,
        "preview_fps_num": project.preview_fps_num,
        "preview_fps_den": project.preview_fps_den,
        "sources": [
            {
                "source_id": source.source_id,
                "path": serialize_project_path(project_path, source.path),
            }
            for source in project.sources
        ],
        "clips": [
            {
                "clip_id": clip.clip_id,
                "source_id": clip.source_id,
                "timeline_start_frame": clip.timeline_start_frame,
                "source_start_frame": clip.source_start_frame,
                "frame_count": clip.frame_count,
                "name": clip.name,
                "keyframes": [
                    {
                        "frame_index": kf.frame_index,
                        "x": kf.x,
                        "y": kf.y,
                        "scale_x": kf.scale_x,
                        "scale_y": kf.scale_y,
                        "rotation_deg": kf.rotation_deg,
                        "alpha": kf.alpha,
                    }
                    for kf in clip.keyframes
                ],
            }
            for clip in project.clips
        ],
    }
    return json.dumps(data, indent=2).encode("utf-8")


def save_project_file(project: Project, path: Path) -> None:
    payload = project_to_payload(project, path)
    block = struct.pack(BLOCK_HEADER_FMT, b"JSON", BLOCK_HEADER_SIZE + len(payload)) + payload
    blocks_size = len(block)
    blocks_offset = FILE_HEADER_SIZE + blocks_size
    file_size = FILE_HEADER_SIZE + blocks_size
    header = pack_project_header(
        out_width=project.out_width,
        out_height=project.out_height,
        total_frames=project_total_frames(project),
        preview_fps_num=project.preview_fps_num or DEFAULT_FPS_NUM,
        preview_fps_den=project.preview_fps_den or DEFAULT_FPS_DEN,
        blocks_size=blocks_size,
        blocks_offset=blocks_offset,
        file_size=file_size,
        source_count=len(project.sources),
        clip_count=len(project.clips),
    )
    path.write_bytes(header + block)


def load_project_file(path: Path) -> Project:
    data = path.read_bytes()
    r = Reader(data)
    header = parse_project_header(r)

    blocks_end = header.header_size + header.blocks_size
    if blocks_end > header.file_size:
        raise ValueError("Project blocks out of bounds")

    json_payload: Optional[bytes] = None
    r.seek(header.header_size)
    while r.tell() < blocks_end:
        block_start = r.tell()
        magic, block_size = r.unpack(BLOCK_HEADER_FMT)
        if block_size < BLOCK_HEADER_SIZE:
            raise ValueError("Invalid project block_size")
        if block_start + block_size > blocks_end:
            raise ValueError("Project block out of bounds")
        payload = r.read(block_size - BLOCK_HEADER_SIZE)
        if magic == b"JSON":
            json_payload = payload
        r.seek(block_start + block_size)

    if json_payload is None:
        raise ValueError("Project JSON block is missing")

    payload = json.loads(json_payload.decode("utf-8"))

    project = Project(
        out_width=int(payload.get("out_width", header.out_width)),
        out_height=int(payload.get("out_height", header.out_height)),
        loop=bool(payload.get("loop", False)),
        preview_fps_num=int(payload.get("preview_fps_num", header.preview_fps_num)),
        preview_fps_den=int(payload.get("preview_fps_den", header.preview_fps_den)),
    )

    available_source_ids = set()
    source_paths: dict[int, Path] = {}
    source_errors: dict[int, str] = {}
    source_items = payload.get("sources", [])
    for source_item in source_items:
        source_path = resolve_project_path(path, str(source_item["path"]))
        source_id = int(source_item["source_id"])
        source_paths[source_id] = source_path
        try:
            project.sources.append(load_source_asset(source_path, source_id))
            available_source_ids.add(source_id)
        except Exception as exc:
            source_errors[source_id] = str(exc)

    clip_items = payload.get("clips", [])
    sequential_start = 0
    for clip_item in clip_items:
        source_id = int(clip_item["source_id"])
        clip_name = str(clip_item.get("name", ""))
        if source_id not in available_source_ids:
            expected_path = source_paths.get(source_id)
            project.load_warnings.append(
                f"Skipped clip: {clip_name or '<unnamed>'}\nExpected source path: {expected_path if expected_path is not None else '<unknown>'}\nReason: {source_errors.get(source_id, f'source_id={source_id} could not be loaded')}"
            )
            continue
        keyframes = [
            Keyframe(
                frame_index=int(kf["frame_index"]),
                x=float(kf["x"]),
                y=float(kf["y"]),
                scale_x=float(kf["scale_x"]),
                scale_y=float(kf["scale_y"]),
                rotation_deg=float(kf["rotation_deg"]),
                alpha=int(kf["alpha"]),
            )
            for kf in clip_item.get("keyframes", [])
        ]
        clip = Clip(
            clip_id=int(clip_item["clip_id"]),
            source_id=source_id,
            timeline_start_frame=int(clip_item.get("timeline_start_frame", sequential_start)),
            source_start_frame=int(clip_item.get("source_start_frame", 0)),
            frame_count=int(clip_item.get("frame_count", 0)),
            name=clip_name,
            keyframes=keyframes or [Keyframe(frame_index=0)],
        )
        try:
            clip_length = clip_frame_count(project, clip)
        except Exception as exc:
            expected_path = source_paths.get(source_id)
            project.load_warnings.append(
                f"Skipped clip: {clip.name or clip.clip_id}\nExpected source path: {expected_path if expected_path is not None else '<unknown>'}\nReason: {exc}"
            )
            continue
        project.clips.append(clip)
        sequential_start += clip_length

    if not project.sources or not project.clips:
        details = ""
        if project.load_warnings:
            details = " " + " | ".join(project.load_warnings[:3])
        raise ValueError(f"Project does not contain any loadable sources or clips.{details}")

    return project


# =========================
# Interpolation / editor model
# =========================

def keyframe_for_frame(clip: Clip, index: int) -> Keyframe:
    if not clip.keyframes:
        return Keyframe(frame_index=index)

    if index <= clip.keyframes[0].frame_index:
        base = clip.keyframes[0]
        return Keyframe(
            frame_index=index,
            x=base.x,
            y=base.y,
            scale_x=base.scale_x,
            scale_y=base.scale_y,
            rotation_deg=base.rotation_deg,
            alpha=base.alpha,
        )

    if index >= clip.keyframes[-1].frame_index:
        base = clip.keyframes[-1]
        return Keyframe(
            frame_index=index,
            x=base.x,
            y=base.y,
            scale_x=base.scale_x,
            scale_y=base.scale_y,
            rotation_deg=base.rotation_deg,
            alpha=base.alpha,
        )

    for left, right in zip(clip.keyframes, clip.keyframes[1:]):
        if left.frame_index <= index <= right.frame_index:
            span = right.frame_index - left.frame_index
            t = 0.0 if span == 0 else (index - left.frame_index) / span
            return Keyframe(
                frame_index=index,
                x=left.x + (right.x - left.x) * t,
                y=left.y + (right.y - left.y) * t,
                scale_x=left.scale_x + (right.scale_x - left.scale_x) * t,
                scale_y=left.scale_y + (right.scale_y - left.scale_y) * t,
                rotation_deg=left.rotation_deg + (right.rotation_deg - left.rotation_deg) * t,
                alpha=int(round(left.alpha + (right.alpha - left.alpha) * t)),
            )

    return Keyframe(frame_index=index)


def upsert_keyframe(clip: Clip, new_kf: Keyframe) -> None:
    for i, kf in enumerate(clip.keyframes):
        if kf.frame_index == new_kf.frame_index:
            clip.keyframes[i] = new_kf
            break
    else:
        clip.keyframes.append(new_kf)

    clip.keyframes.sort(key=lambda k: k.frame_index)


def delete_keyframe(clip: Clip, frame_index: int) -> None:
    clip.keyframes = [kf for kf in clip.keyframes if kf.frame_index != frame_index]


def render_clip_onto_canvas(
    canvas: Image.Image,
    clip: Clip,
    source_image: Image.Image,
    local_frame_index: int,
) -> None:
    kf = keyframe_for_frame(clip, local_frame_index)

    scaled_w = max(1, int(round(source_image.width * max(0.001, kf.scale_x))))
    scaled_h = max(1, int(round(source_image.height * max(0.001, kf.scale_y))))
    img = source_image.resize((scaled_w, scaled_h), Image.Resampling.BICUBIC)

    if kf.alpha != 255:
        alpha = img.getchannel("A")
        alpha = alpha.point(lambda p: (p * kf.alpha) // 255)
        img.putalpha(alpha)

    if abs(kf.rotation_deg) > 1e-6:
        img = img.rotate(
            -kf.rotation_deg,
            resample=Image.Resampling.BICUBIC,
            expand=True,
        )

    canvas.alpha_composite(img, (int(round(kf.x)), int(round(kf.y))))


def render_project_frame(project: Project, frame_index: int) -> Image.Image:
    canvas = Image.new("RGBA", (project.out_width, project.out_height), (0, 0, 0, 0))
    for context in active_timeline_contexts(project, frame_index):
        source_image = context.source.frames[context.source_frame_index]
        render_clip_onto_canvas(canvas, context.clip, source_image, context.local_frame_index)
    return canvas


def render_preview_frame(project: Project, frame_index: int) -> Image.Image:
    frame = render_project_frame(project, frame_index)
    preview = Image.new("RGBA", frame.size, PREVIEW_CANVAS_BG)
    preview.alpha_composite(frame)
    return preview


# =========================
# GUI
# =========================

class MigifEditorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MIGIF Editor")

        self.project: Optional[Project] = None
        self.project_path: Optional[Path] = None

        self.current_frame = 0
        self.active_clip_index = 0
        self.playing = False
        self.play_job: Optional[str] = None
        self.preview_photo: Optional[ImageTk.PhotoImage] = None
        self.exporting = False
        self.syncing_clip_list = False
        self.clip_name_var = tk.StringVar(value="")
        self.clip_start_var = tk.IntVar(value=0)
        self.clip_source_start_var = tk.IntVar(value=0)
        self.clip_length_var = tk.IntVar(value=0)

        self._apply_theme()
        self._build_ui()
        self.root.bind_all("<Control-s>", self._on_ctrl_s)
        self.root.bind_all("<Control-e>", self._on_ctrl_e)

    def _apply_theme(self) -> None:
        bg = "#0f1117"
        panel = "#171a22"
        panel_alt = "#1f2430"
        fg = "#e6e9ef"
        muted = "#9aa4b2"
        accent = "#4cc2ff"
        border = "#2c3340"

        self.theme_colors = {
            "bg": bg,
            "panel": panel,
            "panel_alt": panel_alt,
            "fg": fg,
            "muted": muted,
            "accent": accent,
            "border": border,
        }

        self.root.configure(bg=bg)
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(".", background=bg, foreground=fg, fieldbackground=panel_alt, bordercolor=border)
        style.configure("TFrame", background=bg)
        style.configure("Panel.TFrame", background=panel)
        style.configure("Sidebar.TFrame", background=panel)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("Muted.TLabel", background=bg, foreground=muted)
        style.configure("Panel.TLabel", background=panel, foreground=fg)
        style.configure("TButton", background=panel_alt, foreground=fg, borderwidth=1, focusthickness=0, focuscolor=accent, padding=6)
        style.map("TButton", background=[("active", "#273043")])
        style.configure("Accent.TButton", background=accent, foreground="#091018")
        style.map("Accent.TButton", background=[("active", "#6bd0ff")])
        style.configure("TEntry", fieldbackground=panel_alt, foreground=fg, insertcolor=fg, bordercolor=border)
        style.configure("TCheckbutton", background=panel, foreground=fg)
        style.configure("TScale", background=bg, troughcolor=panel_alt)
        style.configure("TSeparator", background=border)
        style.configure("Horizontal.TProgressbar", background=accent, troughcolor=panel_alt, bordercolor=border)
        style.configure("Panel.TLabelframe", background=panel, bordercolor=border, relief="solid")
        style.configure("Panel.TLabelframe.Label", background=panel, foreground=fg)
        style.configure("PanelHeader.TLabel", background=panel, foreground=fg, font=("Segoe UI Semibold", 10))
        style.configure("MutedPanel.TLabel", background=panel, foreground=muted)

    def _build_ui(self) -> None:
        self.root.geometry("1540x920")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=0)
        self.root.columnconfigure(1, weight=1)
        self.root.columnconfigure(2, weight=0)

        left = ttk.Frame(self.root, padding=10, style="Panel.TFrame")
        left.grid(row=0, column=0, sticky="nsw", padx=(10, 6), pady=10)
        left.columnconfigure(0, weight=1)

        center = ttk.Frame(self.root, padding=10)
        center.grid(row=0, column=1, sticky="nsew", pady=10)
        center.rowconfigure(1, weight=1)
        center.columnconfigure(0, weight=1)

        right = ttk.Frame(self.root, padding=10, style="Panel.TFrame")
        right.grid(row=0, column=2, sticky="nse", padx=(6, 10), pady=10)
        right.columnconfigure(0, weight=1)

        project_box = ttk.LabelFrame(left, text="Project", padding=12, style="Panel.TLabelframe")
        project_box.grid(row=0, column=0, sticky="ew")
        project_box.columnconfigure(0, weight=1)

        ttk.Button(project_box, text="Load .proj", command=self.load_project_dialog).grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(project_box, text="Add .migif", command=self.add_source_file).grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(project_box, text="Export As...", command=self.save_file, style="Accent.TButton").grid(row=2, column=0, sticky="ew")

        self.info_var = tk.StringVar(value="No file loaded")
        ttk.Label(project_box, textvariable=self.info_var, justify="left", style="MutedPanel.TLabel").grid(row=3, column=0, sticky="ew", pady=(10, 0))

        output_box = ttk.LabelFrame(left, text="Output", padding=12, style="Panel.TLabelframe")
        output_box.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        output_box.columnconfigure(1, weight=1)

        self.out_w_var = tk.IntVar(value=800)
        self.out_h_var = tk.IntVar(value=600)
        self.loop_var = tk.BooleanVar(value=False)

        ttk.Label(output_box, text="Canvas width", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        out_w_entry = ttk.Entry(output_box, textvariable=self.out_w_var, width=12)
        out_w_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 6))
        ttk.Label(output_box, text="Canvas height", style="Panel.TLabel").grid(row=1, column=0, sticky="w")
        out_h_entry = ttk.Entry(output_box, textvariable=self.out_h_var, width=12)
        out_h_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(0, 6))
        ttk.Checkbutton(output_box, text="Loop export", variable=self.loop_var, command=self.apply_output_settings).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        keyframe_box = ttk.LabelFrame(left, text="Selected Clip Keyframes", padding=12, style="Panel.TLabelframe")
        keyframe_box.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        keyframe_box.columnconfigure(1, weight=1)
        keyframe_box.rowconfigure(9, weight=1)
        left.rowconfigure(2, weight=1)

        self.frame_var = tk.IntVar(value=0)
        self.x_var = tk.DoubleVar(value=0.0)
        self.y_var = tk.DoubleVar(value=0.0)
        self.scale_x_var = tk.DoubleVar(value=1.0)
        self.scale_y_var = tk.DoubleVar(value=1.0)
        self.rot_var = tk.DoubleVar(value=0.0)
        self.alpha_var = tk.IntVar(value=255)

        for row, (label, var) in enumerate(
            [
                ("Local frame", self.frame_var),
                ("X", self.x_var),
                ("Y", self.y_var),
                ("Scale X", self.scale_x_var),
                ("Scale Y", self.scale_y_var),
                ("Rotation", self.rot_var),
                ("Alpha", self.alpha_var),
            ]
        ):
            ttk.Label(keyframe_box, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w")
            entry = ttk.Entry(keyframe_box, textvariable=var, width=12)
            entry.grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=(0, 6))
            self._bind_commit(entry, self.apply_current_keyframe)

        ttk.Button(keyframe_box, text="Delete keyframe", command=self.delete_current_keyframe).grid(row=7, column=0, columnspan=2, sticky="ew", pady=(4, 8))

        ttk.Label(keyframe_box, text="Keyframes", style="PanelHeader.TLabel").grid(row=8, column=0, columnspan=2, sticky="w")
        self.kf_list = tk.Listbox(keyframe_box, width=32, height=12)
        self.kf_list.grid(row=9, column=0, columnspan=2, sticky="nsew", pady=(6, 0))
        self.kf_list.bind("<<ListboxSelect>>", self.on_keyframe_select)

        clips_box = ttk.LabelFrame(right, text="Clip Stack", padding=12, style="Panel.TLabelframe")
        clips_box.grid(row=0, column=0, sticky="nsew")
        clips_box.columnconfigure(0, weight=1)
        clips_box.rowconfigure(1, weight=1)
        right.rowconfigure(0, weight=1)

        ttk.Label(clips_box, text="Select which layered clip you are editing. Later clips render on top.", style="MutedPanel.TLabel").grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.clip_list = tk.Listbox(clips_box, width=34, height=14)
        self.clip_list.grid(row=1, column=0, sticky="nsew")
        self.clip_list.bind("<<ListboxSelect>>", self.on_clip_select)

        clip_box = ttk.LabelFrame(right, text="Selected Clip", padding=12, style="Panel.TLabelframe")
        clip_box.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        clip_box.columnconfigure(1, weight=1)

        ttk.Label(clip_box, text="Name", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        clip_name_entry = ttk.Entry(clip_box, textvariable=self.clip_name_var)
        clip_name_entry.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=(0, 6))
        ttk.Label(clip_box, text="Start frame", style="Panel.TLabel").grid(row=1, column=0, sticky="w")
        clip_start_entry = ttk.Entry(clip_box, textvariable=self.clip_start_var, width=12)
        clip_start_entry.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(0, 6))
        ttk.Label(clip_box, text="Source start", style="Panel.TLabel").grid(row=2, column=0, sticky="w")
        clip_source_start_entry = ttk.Entry(clip_box, textvariable=self.clip_source_start_var, width=12)
        clip_source_start_entry.grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=(0, 6))
        ttk.Label(clip_box, text="Frame count", style="Panel.TLabel").grid(row=3, column=0, sticky="w")
        clip_length_entry = ttk.Entry(clip_box, textvariable=self.clip_length_var, width=12)
        clip_length_entry.grid(row=3, column=1, sticky="ew", padx=(10, 0), pady=(0, 6))

        for entry in [clip_name_entry, clip_start_entry, clip_source_start_entry, clip_length_entry]:
            self._bind_commit(entry, self.apply_selected_clip_settings)
        for entry in [out_w_entry, out_h_entry]:
            self._bind_commit(entry, self.apply_output_settings)

        quick_box = ttk.LabelFrame(right, text="Quick Actions", padding=12, style="Panel.TLabelframe")
        quick_box.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        quick_box.columnconfigure(0, weight=1)
        quick_box.columnconfigure(1, weight=1)
        ttk.Button(quick_box, text="Save", command=self.save_project_shortcut).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(quick_box, text="Export", command=self.export_without_prompt, style="Accent.TButton").grid(row=0, column=1, sticky="ew", padx=(4, 0))

        topbar = ttk.Frame(center)
        topbar.grid(row=0, column=0, sticky="ew")
        topbar.columnconfigure(5, weight=1)

        ttk.Button(topbar, text="Start", command=self.jump_to_selected_clip_start).grid(row=0, column=0, padx=(0, 4))
        ttk.Button(topbar, text="<<", command=self.prev_frame).grid(row=0, column=1, padx=(0, 4))
        ttk.Button(topbar, text="Play / Pause", command=self.toggle_play).grid(row=0, column=2, padx=(0, 4))
        ttk.Button(topbar, text=">>", command=self.next_frame).grid(row=0, column=3, padx=(0, 4))
        ttk.Button(topbar, text="End", command=self.jump_to_selected_clip_end).grid(row=0, column=4, padx=(0, 8))

        self.frame_scale = ttk.Scale(topbar, from_=0, to=0, orient="horizontal", command=self.on_timeline_change)
        self.frame_scale.grid(row=0, column=5, sticky="ew", padx=8)

        self.frame_label_var = tk.StringVar(value="Frame 0 / 0")
        ttk.Label(topbar, textvariable=self.frame_label_var).grid(row=0, column=6, padx=(8, 0))

        preview_shell = ttk.Frame(center, padding=12, style="Panel.TFrame")
        preview_shell.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        preview_shell.rowconfigure(0, weight=1)
        preview_shell.columnconfigure(0, weight=1)

        self.preview_label = ttk.Label(preview_shell, anchor="center")
        self.preview_label.grid(row=0, column=0, sticky="nsew")
        self.preview_label.bind("<Configure>", lambda _e: self.refresh_preview())

        self.export_status_var = tk.StringVar(
            value="Layer clips, edit the selected clip, then export .migif and matching .proj."
        )
        status = ttk.Label(center, textvariable=self.export_status_var)
        status.grid(row=2, column=0, sticky="ew", pady=(8, 4))

        self.export_progress = ttk.Progressbar(center, mode="determinate", maximum=100)
        self.export_progress.grid(row=3, column=0, sticky="ew")

        self._style_listbox(self.kf_list)
        self._style_listbox(self.clip_list)

    def _format_eta(self, eta_seconds: float) -> str:
        eta_total = max(0, int(round(eta_seconds)))
        minutes, seconds = divmod(eta_total, 60)
        if minutes > 0:
            return f"{minutes}m {seconds:02d}s"
        return f"{seconds}s"

    def _show_load_warnings(self, warnings: List[str]) -> None:
        if not warnings:
            return
        unique_warnings = list(dict.fromkeys(warnings))
        details = "\n\n".join(unique_warnings)
        messagebox.showwarning(
            "Project loaded with missing clips",
            f"Some clips could not be loaded.\n\n{details}",
        )

    def _on_ctrl_s(self, _event=None):
        self.save_project_shortcut()
        return "break"

    def _on_ctrl_e(self, _event=None):
        self.export_without_prompt()
        return "break"

    def _set_export_progress(self, done: int, total: int, eta_seconds: float) -> None:
        self.export_progress.configure(mode="determinate")
        total = max(1, total)
        self.export_progress.configure(maximum=total, value=done)
        self.export_status_var.set(
            f"Saving... {done}/{total} frames | ETA {self._format_eta(eta_seconds)}"
        )
        self.root.update_idletasks()

    def start_busy_indicator(self, message: str) -> None:
        self.export_status_var.set(message)
        self.export_progress.configure(mode="indeterminate")
        self.export_progress.start(12)
        self.root.update_idletasks()

    def stop_busy_indicator(self, message: str) -> None:
        self.export_progress.stop()
        self.export_progress.configure(mode="determinate", maximum=100, value=0)
        self.export_status_var.set(message)
        self.root.update_idletasks()

    def _style_listbox(self, widget: tk.Listbox) -> None:
        colors = self.theme_colors
        widget.configure(
            bg=colors["panel_alt"],
            fg=colors["fg"],
            selectbackground=colors["accent"],
            selectforeground="#091018",
            highlightthickness=1,
            highlightbackground=colors["border"],
            highlightcolor=colors["accent"],
            relief="flat",
            bd=0,
            activestyle="none",
        )

    def _bind_commit(self, widget: tk.Widget, callback: Callable[[], None]) -> None:
        widget.bind("<Return>", lambda _event: callback())
        widget.bind("<FocusOut>", lambda _event: callback())

    def selected_clip(self) -> Optional[Clip]:
        if self.project is None or not self.project.clips:
            return None
        self.active_clip_index = max(0, min(self.active_clip_index, len(self.project.clips) - 1))
        return self.project.clips[self.active_clip_index]

    def selected_clip_length(self) -> int:
        clip = self.selected_clip()
        if self.project is None or clip is None:
            return 0
        return clip_frame_count(self.project, clip)

    def selected_local_frame_index(self) -> int:
        clip = self.selected_clip()
        if self.project is None or clip is None:
            return 0
        clip_length = clip_frame_count(self.project, clip)
        if clip_length <= 0:
            return 0
        local_frame = self.current_frame - clip.timeline_start_frame
        return max(0, min(clip_length - 1, local_frame))

    def sync_selected_clip_controls(self) -> None:
        clip = self.selected_clip()
        if self.project is None or clip is None:
            self.clip_name_var.set("")
            self.clip_start_var.set(0)
            self.clip_source_start_var.set(0)
            self.clip_length_var.set(0)
            return

        self.clip_name_var.set(clip.name or clip_label(self.project, clip))
        self.clip_start_var.set(clip.timeline_start_frame)
        self.clip_source_start_var.set(clip.source_start_frame)
        self.clip_length_var.set(clip.frame_count)

    def apply_selected_clip_settings(self) -> None:
        clip = self.selected_clip()
        if self.project is None or clip is None:
            return

        try:
            source = source_by_id(self.project, clip.source_id)
            source_start = int(self.clip_source_start_var.get())
            if source_start < 0 or source_start >= len(source.frames):
                raise ValueError("Source start frame is out of range")

            frame_count = int(self.clip_length_var.get())
            available = len(source.frames) - source_start
            if frame_count < 0:
                raise ValueError("Frame count must be 0 or greater")
            if frame_count > available:
                raise ValueError(f"Frame count exceeds available source frames ({available})")

            clip.name = self.clip_name_var.get().strip() or source.path.stem
            clip.timeline_start_frame = max(0, int(self.clip_start_var.get()))
            clip.source_start_frame = source_start
            clip.frame_count = frame_count

            self.refresh_timeline_range()
            self.refresh_clip_list()
            self.sync_selected_clip_controls()
            self.sync_controls_from_interpolated()
            self.refresh_keyframe_list()
            self.update_project_info()
            self.refresh_preview()
            self.export_status_var.set("Clip settings updated.")
        except Exception as exc:
            messagebox.showerror("Clip settings error", str(exc))

    def apply_output_settings(self) -> None:
        if self.project is None:
            return

        try:
            out_width = int(self.out_w_var.get())
            out_height = int(self.out_h_var.get())
            if out_width <= 0 or out_height <= 0:
                raise ValueError("Output canvas must be positive")

            self.project.out_width = out_width
            self.project.out_height = out_height
            self.project.loop = bool(self.loop_var.get())
            self.update_project_info()
            self.refresh_preview()
        except Exception as exc:
            messagebox.showerror("Output settings error", str(exc))

    def refresh_clip_list(self) -> None:
        self.syncing_clip_list = True
        self.clip_list.delete(0, tk.END)
        if self.project is None:
            self.syncing_clip_list = False
            return
        for index, clip in enumerate(self.project.clips):
            length = clip_frame_count(self.project, clip)
            start = clip_start_frame(self.project, index)
            self.clip_list.insert(
                tk.END,
                f"{index + 1}. {clip_label(self.project, clip)} "
                f"| start {start} | len {length}"
            )
        if self.project.clips:
            selected = max(0, min(self.active_clip_index, len(self.project.clips) - 1))
            self.clip_list.selection_clear(0, tk.END)
            self.clip_list.selection_set(selected)
            self.clip_list.activate(selected)
        self.syncing_clip_list = False

    def timeline_frame_count(self) -> int:
        if self.project is None:
            return 0
        return project_total_frames(self.project)

    def update_frame_label(self) -> None:
        total_frames = self.timeline_frame_count()
        if self.project is None or total_frames <= 0:
            self.frame_label_var.set("Frame 0 / 0")
            return

        active = active_timeline_contexts(self.project, self.current_frame)
        if not active:
            self.frame_label_var.set(f"Frame {self.current_frame} / {total_frames - 1} | no active clips")
            return

        active_text = ", ".join(
            f"{clip_label(self.project, context.clip)}:{context.local_frame_index}"
            for context in active[:3]
        )
        if len(active) > 3:
            active_text += f" +{len(active) - 3} more"
        self.frame_label_var.set(f"Frame {self.current_frame} / {total_frames - 1} | active: {active_text}")

    def update_project_info(self) -> None:
        if self.project is None:
            self.info_var.set("No file loaded")
            return

        total_frames = self.timeline_frame_count()
        header = [
            f"Project: {self.project_path.name}" if self.project_path is not None else "Project: unsaved",
            f"Sources: {len(self.project.sources)} | Clips: {len(self.project.clips)} | Frames: {total_frames}",
            f"Output: {self.project.out_width}x{self.project.out_height}",
            f"Preview FPS: {self.project.preview_fps_num}/{self.project.preview_fps_den}",
        ]

        clip = self.selected_clip()
        if clip is not None:
            clip_length = clip_frame_count(self.project, clip)
            local_frame = self.selected_local_frame_index()
            header.append(
                f"Selected clip: {clip_label(self.project, clip)} "
                f"| start {clip.timeline_start_frame} "
                f"| local frame {local_frame}/{max(0, clip_length - 1)}"
            )
        active = active_timeline_contexts(self.project, self.current_frame)
        header.append(f"Active layers on frame {self.current_frame}: {len(active)}")

        self.info_var.set("\n".join(header))

    def refresh_timeline_range(self) -> None:
        total_frames = self.timeline_frame_count()
        max_frame = max(0, total_frames - 1)
        self.current_frame = max(0, min(self.current_frame, max_frame))
        self.frame_scale.configure(to=max_frame)
        self.frame_scale.set(self.current_frame)

    def apply_project(self, project: Project, project_path: Optional[Path], reset_frame: bool = True) -> None:
        self.project = project
        self.project_path = project_path
        self.active_clip_index = 0
        self.out_w_var.set(project.out_width)
        self.out_h_var.set(project.out_height)
        self.loop_var.set(project.loop)
        if reset_frame:
            self.current_frame = 0
        self.refresh_timeline_range()
        self.refresh_clip_list()
        self.sync_selected_clip_controls()
        self.update_project_info()
        self.sync_controls_from_interpolated()
        self.refresh_keyframe_list()
        self.refresh_preview()

    def load_project_dialog(self) -> None:
        path_str = filedialog.askopenfilename(filetypes=[("Project files", "*.proj")])
        if not path_str:
            return
        try:
            path = Path(path_str)
            self.start_busy_indicator("Loading project...")
            project = load_project_file(path)
            self.apply_project(project, project_path=path, reset_frame=True)
            if project.load_warnings:
                self.stop_busy_indicator(
                    f"Project loaded with {len(project.load_warnings)} skipped clips."
                )
                self._show_load_warnings(project.load_warnings)
            else:
                self.stop_busy_indicator("Project loaded.")
        except Exception as exc:
            self.stop_busy_indicator("Project load failed.")
            messagebox.showerror("Project load error", str(exc))

    def add_source_file(self) -> None:
        path_str = filedialog.askopenfilename(filetypes=[("MIGIF files", "*.migif")])
        if not path_str:
            return

        try:
            path = Path(path_str)
            self.start_busy_indicator("Loading source...")
            if self.project is None:
                self.apply_project(create_project_from_source(path), project_path=None, reset_frame=True)
            else:
                append_source_to_project(self.project, path, timeline_start_frame=self.current_frame)
                self.active_clip_index = len(self.project.clips) - 1
                self.refresh_timeline_range()
                self.refresh_clip_list()
                self.sync_selected_clip_controls()
                self.update_project_info()
                self.sync_controls_from_interpolated()
                self.refresh_keyframe_list()
                self.refresh_preview()
            self.stop_busy_indicator("Source added to project.")
        except Exception as exc:
            self.stop_busy_indicator("Source load failed.")
            messagebox.showerror("Add source error", str(exc))

    def _ensure_project_path(self) -> Optional[Path]:
        if self.project is None:
            return None
        if self.project_path is not None:
            return self.project_path

        out_path = filedialog.asksaveasfilename(
            defaultextension=".proj",
            filetypes=[("Project files", "*.proj")],
        )
        if not out_path:
            return None
        self.project_path = Path(out_path)
        return self.project_path

    def _apply_export_settings(self) -> None:
        if self.project is None:
            return
        self.project.out_width = int(self.out_w_var.get())
        self.project.out_height = int(self.out_h_var.get())
        self.project.loop = bool(self.loop_var.get())
        if self.project.out_width <= 0 or self.project.out_height <= 0:
            raise ValueError("Output canvas must be positive")

    def save_project_shortcut(self) -> None:
        if self.project is None:
            return

        try:
            self._apply_export_settings()
            project_path = self._ensure_project_path()
            if project_path is None:
                return
            save_project_file(self.project, project_path)
            self.project_path = project_path
            self.update_project_info()
            self.export_status_var.set(f"Project saved to {project_path.name}.")
        except Exception as exc:
            self.export_status_var.set("Project save failed.")
            messagebox.showerror("Project save error", str(exc))

    def _export_to_path(self, out_path: Path, show_confirmation: bool) -> None:
        if self.project is None or self.exporting:
            return

        try:
            self._apply_export_settings()

            self.exporting = True
            self.export_progress.configure(value=0)
            self.export_status_var.set("Saving... preparing export")
            self.root.update_idletasks()

            export_project(
                self.project,
                out_path,
                progress_callback=self._set_export_progress,
            )
            project_out_path = out_path.with_suffix(".proj")
            save_project_file(self.project, project_out_path)
            self.project_path = project_out_path
            self.export_progress.configure(value=self.export_progress.cget("maximum"))
            self.update_project_info()
            self.export_status_var.set(f"Export complete: {out_path.name}")
            if show_confirmation:
                messagebox.showinfo(
                    "Saved",
                    f"Saved to {out_path}\nProject saved to {project_out_path}",
                )

        except Exception as exc:
            self.export_status_var.set("Save failed.")
            messagebox.showerror("Save error", str(exc))
        finally:
            self.exporting = False

    def export_without_prompt(self) -> None:
        if self.project is None:
            return
        project_path = self._ensure_project_path()
        if project_path is None:
            return
        self._export_to_path(project_path.with_suffix(".migif"), show_confirmation=False)

    def save_file(self) -> None:
        if self.project is None or self.exporting:
            return

        out_path = filedialog.asksaveasfilename(
            defaultextension=".migif",
            filetypes=[("MIGIF files", "*.migif")],
        )
        if not out_path:
            return
        self._export_to_path(Path(out_path), show_confirmation=True)

    def refresh_keyframe_list(self) -> None:
        self.kf_list.delete(0, tk.END)
        clip = self.selected_clip()
        if self.project is None or clip is None:
            return

        for kf in clip.keyframes:
            self.kf_list.insert(
                tk.END,
                f"F{kf.frame_index}: x={kf.x:.1f} y={kf.y:.1f} "
                f"s=({kf.scale_x:.2f},{kf.scale_y:.2f}) "
                f"r={kf.rotation_deg:.1f} a={kf.alpha}"
            )

    def on_keyframe_select(self, _event=None) -> None:
        clip = self.selected_clip()
        if self.project is None or clip is None:
            return

        sel = self.kf_list.curselection()
        if not sel:
            return

        kf = clip.keyframes[sel[0]]
        self.frame_var.set(kf.frame_index)
        self.x_var.set(kf.x)
        self.y_var.set(kf.y)
        self.scale_x_var.set(kf.scale_x)
        self.scale_y_var.set(kf.scale_y)
        self.rot_var.set(kf.rotation_deg)
        self.alpha_var.set(kf.alpha)

    def on_clip_select(self, _event=None) -> None:
        if self.project is None or self.syncing_clip_list:
            return

        sel = self.clip_list.curselection()
        if not sel:
            return

        clip_index = sel[0]
        if clip_index >= len(self.project.clips):
            return

        self.active_clip_index = clip_index
        self.current_frame = clip_start_frame(self.project, clip_index)
        self.frame_scale.set(self.current_frame)
        self.sync_selected_clip_controls()
        self.sync_controls_from_interpolated()
        self.refresh_keyframe_list()
        self.update_project_info()
        self.refresh_preview()

    def apply_current_keyframe(self) -> None:
        clip = self.selected_clip()
        if self.project is None or clip is None:
            return

        try:
            frame_index = int(self.frame_var.get())
            clip_length = clip_frame_count(self.project, clip)
            if frame_index < 0 or frame_index >= clip_length:
                raise ValueError("Frame index out of range")

            kf = Keyframe(
                frame_index=frame_index,
                x=float(self.x_var.get()),
                y=float(self.y_var.get()),
                scale_x=float(self.scale_x_var.get()),
                scale_y=float(self.scale_y_var.get()),
                rotation_deg=float(self.rot_var.get()),
                alpha=max(0, min(255, int(self.alpha_var.get()))),
            )

            upsert_keyframe(clip, kf)
            self.refresh_keyframe_list()
            self.update_project_info()
            self.refresh_preview()
            self.export_status_var.set("Keyframe updated.")

        except Exception as exc:
            messagebox.showerror("Keyframe error", str(exc))

    def delete_current_keyframe(self) -> None:
        clip = self.selected_clip()
        if self.project is None or clip is None:
            return

        delete_keyframe(clip, int(self.frame_var.get()))
        self.refresh_keyframe_list()
        self.sync_controls_from_interpolated()
        self.update_project_info()
        self.refresh_preview()

    def interpolated_keyframe(self) -> Keyframe:
        clip = self.selected_clip()
        if self.project is None or clip is None:
            return Keyframe(frame_index=0)
        return keyframe_for_frame(clip, self.selected_local_frame_index())

    def sync_controls_from_interpolated(self) -> None:
        clip = self.selected_clip()
        if self.project is None or clip is None:
            self.frame_var.set(0)
            self.x_var.set(0.0)
            self.y_var.set(0.0)
            self.scale_x_var.set(1.0)
            self.scale_y_var.set(1.0)
            self.rot_var.set(0.0)
            self.alpha_var.set(255)
            return

        kf = self.interpolated_keyframe()
        self.frame_var.set(self.selected_local_frame_index())
        self.x_var.set(kf.x)
        self.y_var.set(kf.y)
        self.scale_x_var.set(kf.scale_x)
        self.scale_y_var.set(kf.scale_y)
        self.rot_var.set(kf.rotation_deg)
        self.alpha_var.set(kf.alpha)

    def on_timeline_change(self, value: str) -> None:
        total_frames = self.timeline_frame_count()
        if total_frames <= 0:
            return

        self.current_frame = max(0, min(total_frames - 1, int(round(float(value)))))
        if self.project is not None:
            self.update_frame_label()
        self.sync_controls_from_interpolated()
        self.update_project_info()
        self.refresh_preview()

    def prev_frame(self) -> None:
        if self.timeline_frame_count() <= 0:
            return

        self.current_frame = max(0, self.current_frame - 1)
        self.frame_scale.set(self.current_frame)
        self.on_timeline_change(str(self.current_frame))

    def jump_to_selected_clip_start(self) -> None:
        clip = self.selected_clip()
        if self.project is None or clip is None:
            return

        self.current_frame = clip.timeline_start_frame
        self.frame_scale.set(self.current_frame)
        self.on_timeline_change(str(self.current_frame))

    def next_frame(self) -> None:
        total_frames = self.timeline_frame_count()
        if total_frames <= 0:
            return

        self.current_frame = min(total_frames - 1, self.current_frame + 1)
        self.frame_scale.set(self.current_frame)
        self.on_timeline_change(str(self.current_frame))

    def jump_to_selected_clip_end(self) -> None:
        clip = self.selected_clip()
        if self.project is None or clip is None:
            return

        clip_length = clip_frame_count(self.project, clip)
        if clip_length <= 0:
            return

        self.current_frame = clip.timeline_start_frame + clip_length - 1
        self.frame_scale.set(self.current_frame)
        self.on_timeline_change(str(self.current_frame))

    def toggle_play(self) -> None:
        self.playing = not self.playing

        if self.playing:
            self._play_step()
        elif self.play_job is not None:
            self.root.after_cancel(self.play_job)
            self.play_job = None

    def _play_step(self) -> None:
        total_frames = self.timeline_frame_count()
        if not self.playing or self.project is None or total_frames <= 0:
            return

        if self.current_frame >= total_frames - 1:
            self.current_frame = 0
        else:
            self.current_frame += 1

        self.frame_scale.set(self.current_frame)
        self.on_timeline_change(str(self.current_frame))

        fps = (
            self.project.preview_fps_num / self.project.preview_fps_den
            if self.project.preview_fps_den
            else 30.0
        )
        delay_ms = max(1, int(round(1000.0 / max(1.0, fps))))

        self.play_job = self.root.after(delay_ms, self._play_step)

    def render_current_frame_image(self) -> Optional[Image.Image]:
        if self.project is None or self.timeline_frame_count() <= 0:
            return None

        self.project.out_width = int(self.out_w_var.get())
        self.project.out_height = int(self.out_h_var.get())

        return render_preview_frame(self.project, self.current_frame)

    def refresh_preview(self) -> None:
        img = self.render_current_frame_image()
        if img is None:
            self.preview_label.configure(image="")
            return

        label_w = max(200, self.preview_label.winfo_width())
        label_h = max(200, self.preview_label.winfo_height())

        scale = min(label_w / img.width, label_h / img.height)
        scale = max(0.05, scale)

        preview_w = max(1, int(round(img.width * scale)))
        preview_h = max(1, int(round(img.height * scale)))

        preview = img.resize((preview_w, preview_h), Image.Resampling.NEAREST)
        self.preview_photo = ImageTk.PhotoImage(preview)
        self.preview_label.configure(image=self.preview_photo)
        self.update_frame_label()


def main() -> None:
    parser = argparse.ArgumentParser(description="MIGIF editor")
    parser.add_argument("input", nargs="?", help="Optional .migif or .proj to open")
    args = parser.parse_args()

    input_path = Path(args.input) if args.input else None
    app = launch_editor_window(input_path=input_path)
    app.root.mainloop()


def _open_input_path(app: MigifEditorApp, input_path: Path) -> None:
    if input_path.suffix.lower() == ".proj":
        project = load_project_file(input_path)
        app.apply_project(project, project_path=input_path, reset_frame=True)
        if project.load_warnings:
            app.export_status_var.set(
                f"Project loaded with {len(project.load_warnings)} skipped clips."
            )
            app._show_load_warnings(project.load_warnings)
        else:
            app.export_status_var.set("Project loaded.")
        return

    app.apply_project(create_project_from_source(input_path), project_path=None, reset_frame=True)
    app.export_status_var.set("Source loaded as new project.")


def launch_editor_window(
    parent: Optional[tk.Misc] = None,
    input_path: Optional[Path] = None,
) -> MigifEditorApp:
    root = tk.Tk() if parent is None else tk.Toplevel(parent)
    app = MigifEditorApp(root)

    if input_path is not None:
        try:
            _open_input_path(app, input_path)
        except Exception as exc:
            messagebox.showerror("Load error", str(exc), parent=root)

    return app


if __name__ == "__main__":
    main()
