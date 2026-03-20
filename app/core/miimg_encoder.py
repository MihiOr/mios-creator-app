#!/usr/bin/env python3
import argparse
from pathlib import Path
from PIL import Image


def image_to_bgra(img: Image.Image) -> bytes:
    rgba = img.convert("RGBA")
    src = rgba.tobytes()
    out = bytearray(len(src))

    for i in range(0, len(src), 4):
        r = src[i + 0]
        g = src[i + 1]
        b = src[i + 2]
        a = src[i + 3]

        out[i + 0] = b
        out[i + 1] = g
        out[i + 2] = r
        out[i + 3] = a

    return bytes(out)


def rle_pack_bgra(data: bytes) -> bytes:
    if len(data) % 4 != 0:
        raise ValueError("BGRA data length must be multiple of 4")

    pixels = [data[i:i + 4] for i in range(0, len(data), 4)]
    out = bytearray()
    n = len(pixels)
    i = 0

    while i < n:
        run_len = 1
        while i + run_len < n and pixels[i + run_len] == pixels[i] and run_len < 128:
            run_len += 1

        if run_len >= 2:
            out.append(128 + (run_len - 1))
            out.extend(pixels[i])
            i += run_len
            continue

        start = i
        i += 1

        while i < n and (i - start) < 128:
            lookahead = 1
            while i + lookahead < n and pixels[i + lookahead] == pixels[i] and lookahead < 128:
                lookahead += 1

            if lookahead >= 2:
                break

            i += 1

        lit_len = i - start
        out.append(lit_len - 1)
        for p in pixels[start:i]:
            out.extend(p)

    return bytes(out)


def write_miimg(
    input_path: str | Path,
    output_path: str | Path,
    resize_width: int = None,
    resize_height: int = None,
    force_opaque_alpha: bool = False
):
    img = Image.open(input_path)

    if resize_width is not None and resize_height is not None:
        img = img.resize((resize_width, resize_height), Image.Resampling.LANCZOS)

    bgra = bytearray(image_to_bgra(img))

    if force_opaque_alpha:
        for i in range(3, len(bgra), 4):
            bgra[i] = 255

    packed = rle_pack_bgra(bytes(bgra))

    with open(output_path, "wb") as f:
        f.write(b"MII0")
        f.write(img.width.to_bytes(4, "little"))
        f.write(img.height.to_bytes(4, "little"))
        f.write((1).to_bytes(4, "little"))  # format = BGRA_RLE
        f.write(len(packed).to_bytes(4, "little"))
        f.write(packed)

    print(f"Wrote {output_path}")
    print(f"Size: {img.width}x{img.height}")
    print(f"Raw BGRA bytes: {len(bgra)}")
    print(f"Packed bytes: {len(packed)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pretvara sliku u .miimg format.")
    parser.add_argument("input", type=Path, help="Ulazni image file")
    parser.add_argument("output", type=Path, nargs="?", help="Izlazni .miimg file")
    parser.add_argument("--width", type=int, default=None, help="Resize širina")
    parser.add_argument("--height", type=int, default=None, help="Resize visina")
    parser.add_argument(
        "--force-opaque-alpha",
        action="store_true",
        help="Postavi alpha kanal svih piksela na 255.",
    )
    args = parser.parse_args()

    if (args.width is None) != (args.height is None):
        parser.error("--width i --height moraju biti zadani zajedno")

    if args.width is not None and args.width <= 0:
        parser.error("--width mora biti > 0")

    if args.height is not None and args.height <= 0:
        parser.error("--height mora biti > 0")

    output_path: Path = args.output or args.input.with_suffix(".miimg")

    write_miimg(
        input_path=args.input,
        output_path=output_path,
        resize_width=args.width,
        resize_height=args.height,
        force_opaque_alpha=args.force_opaque_alpha,
    )


if __name__ == "__main__":
    main()
