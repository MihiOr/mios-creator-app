#!/usr/bin/env python3
import argparse
from pathlib import Path
from PIL import Image


MIIMG_MAGIC = b"MII0"
MIIMG_FORMAT_BGRA_RLE = 1


def read_u32le(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little")


def rle_unpack_bgra(data: bytes, width: int, height: int) -> bytes:
    pixel_count = width * height
    out = bytearray()
    src = 0
    pixels_written = 0

    while src < len(data) and pixels_written < pixel_count:
        ctrl = data[src]
        src += 1

        if ctrl < 128:
            run_len = ctrl + 1
            byte_len = run_len * 4

            if src + byte_len > len(data):
                raise ValueError("Corrupted literal run")

            out.extend(data[src:src + byte_len])
            src += byte_len
            pixels_written += run_len
        else:
            run_len = (ctrl - 128) + 1

            if src + 4 > len(data):
                raise ValueError("Corrupted repeat run")

            px = data[src:src + 4]
            src += 4

            for _ in range(run_len):
                out.extend(px)

            pixels_written += run_len

    if pixels_written != pixel_count:
        raise ValueError(f"Decoded pixel count mismatch: got {pixels_written}, expected {pixel_count}")

    return bytes(out)


def bgra_to_rgba(bgra: bytes) -> bytes:
    if len(bgra) % 4 != 0:
        raise ValueError("BGRA buffer size must be divisible by 4")

    out = bytearray(len(bgra))

    for i in range(0, len(bgra), 4):
        b = bgra[i + 0]
        g = bgra[i + 1]
        r = bgra[i + 2]
        a = bgra[i + 3]

        out[i + 0] = r
        out[i + 1] = g
        out[i + 2] = b
        out[i + 3] = a

    return bytes(out)


def decode_miimg(path: str | Path):
    with open(path, "rb") as f:
        data = f.read()

    if len(data) < 20:
        raise ValueError("File too small to be a valid .miimg")

    magic = data[0:4]
    if magic != MIIMG_MAGIC:
        raise ValueError(f"Invalid magic: {magic!r}")

    width = read_u32le(data, 4)
    height = read_u32le(data, 8)
    fmt = read_u32le(data, 12)
    packed_size = read_u32le(data, 16)

    if width <= 0 or height <= 0:
        raise ValueError("Invalid image size")

    if fmt != MIIMG_FORMAT_BGRA_RLE:
        raise ValueError(f"Unsupported format: {fmt}")

    if 20 + packed_size > len(data):
        raise ValueError("Packed size exceeds file size")

    packed = data[20:20 + packed_size]
    bgra = rle_unpack_bgra(packed, width, height)
    rgba = bgra_to_rgba(bgra)

    img = Image.frombytes("RGBA", (width, height), rgba)
    return img, width, height


def main() -> None:
    parser = argparse.ArgumentParser(description="Dekodira .miimg datoteku u sliku.")
    parser.add_argument("input", type=Path, help="Ulazni .miimg file")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        help="Opcionalni izlazni PNG file",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="Nemoj otvarati preview prozor nakon dekodiranja.",
    )
    args = parser.parse_args()

    img, width, height = decode_miimg(args.input)

    print(f"Decoded: {args.input}")
    print(f"Size: {width}x{height}")

    if args.output is not None:
        img.save(args.output)
        print(f"Saved PNG: {args.output}")

    if not args.no_show:
        img.show()


if __name__ == "__main__":
    main()
