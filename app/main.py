#!/usr/bin/env python3
from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk


APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from core import migif_decoder, migif_editor, migif_encoder, miimg_decoder, miimg_encoder


@dataclass(frozen=True)
class AppPaths:
    root: Path
    files: Path
    miimg_input: Path
    miimg_output: Path
    miimg_samples: Path
    migif_input: Path
    migif_output: Path
    migif_projects: Path
    migif_samples: Path


def build_paths() -> AppPaths:
    root = APP_DIR.parent
    files = root / "files"
    return AppPaths(
        root=root,
        files=files,
        miimg_input=files / "miimg" / "input",
        miimg_output=files / "miimg" / "output",
        miimg_samples=files / "miimg" / "samples",
        migif_input=files / "migif" / "input",
        migif_output=files / "migif" / "output",
        migif_projects=files / "migif" / "projects",
        migif_samples=files / "migif" / "samples",
    )


PATHS = build_paths()


def ensure_directories() -> None:
    for path in (
        PATHS.miimg_input,
        PATHS.miimg_output,
        PATHS.miimg_samples,
        PATHS.migif_input,
        PATHS.migif_output,
        PATHS.migif_projects,
        PATHS.migif_samples,
    ):
        path.mkdir(parents=True, exist_ok=True)


def fit_preview(image: Image.Image, max_width: int, max_height: int) -> Image.Image:
    return ImageOps.contain(image, (max(1, max_width), max(1, max_height)), Image.Resampling.LANCZOS)


def render_checkerboard(width: int, height: int, cell: int = 12) -> Image.Image:
    img = Image.new("RGBA", (width, height), (239, 233, 222, 255))
    pixels = img.load()
    for y in range(height):
        for x in range(width):
            if ((x // cell) + (y // cell)) % 2:
                pixels[x, y] = (220, 213, 199, 255)
    return img


def composite_for_preview(image: Image.Image) -> Image.Image:
    base = render_checkerboard(image.width, image.height)
    base.alpha_composite(image.convert("RGBA"))
    return base


def run_action(root: tk.Misc, action: Callable[[], None]) -> None:
    try:
        action()
    except Exception as exc:
        traceback.print_exc()
        messagebox.showerror("Operation failed", str(exc), parent=root)


def parse_int(value: str, field_name: str, minimum: int = 1) -> int:
    parsed = int(value)
    if parsed < minimum:
        raise ValueError(f"{field_name} mora biti >= {minimum}.")
    return parsed


class BasePanel(ttk.Frame):
    def __init__(self, master: tk.Misc, title: str, description: str) -> None:
        super().__init__(master, padding=20, style="Content.TFrame")
        self.columnconfigure(0, weight=1)
        ttk.Label(self, text=title, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(self, text=description, style="Body.TLabel", wraplength=820).grid(
            row=1, column=0, sticky="w", pady=(6, 16)
        )
        self.body = ttk.Frame(self, style="Content.TFrame")
        self.body.grid(row=2, column=0, sticky="nsew")
        self.body.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status_var, style="Status.TLabel").grid(
            row=3, column=0, sticky="ew", pady=(16, 0)
        )

    def set_status(self, message: str) -> None:
        self.status_var.set(message)


class HomePanel(BasePanel):
    def __init__(self, master: tk.Misc, app: "CreatorApp") -> None:
        super().__init__(
            master,
            "MIOS Creator App",
            "Centralna aplikacija za MIOS alate. Trenutni modul pokriva MIIMG encode/decode, MIGIF encode/play i MIGIF editor.",
        )
        card = ttk.Frame(self.body, style="Card.TFrame", padding=18)
        card.grid(row=0, column=0, sticky="nw")
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text="Workspace", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(card, text=f"App: {PATHS.root}", style="Body.TLabel", wraplength=780).grid(
            row=1, column=0, sticky="w", pady=(8, 4)
        )
        ttk.Label(card, text=f"Files: {PATHS.files}", style="Body.TLabel", wraplength=780).grid(
            row=2, column=0, sticky="w", pady=(0, 12)
        )
        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=3, column=0, sticky="w")
        ttk.Button(actions, text="MIIMG Encode", command=lambda: app.show_panel("miimg_encode")).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="MIIMG Decode", command=lambda: app.show_panel("miimg_decode")).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(actions, text="MIGIF Encode", command=lambda: app.show_panel("migif_encode")).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(actions, text="MIGIF Player", command=lambda: app.show_panel("migif_player")).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(actions, text="Open Editor", command=lambda: app.show_panel("migif_editor")).grid(row=0, column=4)

        samples = ttk.Frame(self.body, style="Card.TFrame", padding=18)
        samples.grid(row=1, column=0, sticky="nw", pady=(18, 0))
        ttk.Label(samples, text="Default file buckets", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        sample_lines = [
            f"MIIMG input:  {PATHS.miimg_input}",
            f"MIIMG output: {PATHS.miimg_output}",
            f"MIGIF input:  {PATHS.migif_input}",
            f"MIGIF output: {PATHS.migif_output}",
            f"Projects:     {PATHS.migif_projects}",
        ]
        ttk.Label(samples, text="\n".join(sample_lines), style="Mono.TLabel", justify="left").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        self.set_status("Odaberi alat s lijeve strane.")


class PreviewBox(ttk.Frame):
    def __init__(self, master: tk.Misc, title: str) -> None:
        super().__init__(master, style="Card.TFrame", padding=12)
        self.columnconfigure(0, weight=1)
        ttk.Label(self, text=title, style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.label = ttk.Label(self, anchor="center")
        self.label.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.rowconfigure(1, weight=1)
        self.current_image: Optional[ImageTk.PhotoImage] = None

    def set_image(self, image: Image.Image, max_width: int = 420, max_height: int = 320) -> None:
        preview = fit_preview(image, max_width, max_height)
        self.current_image = ImageTk.PhotoImage(preview)
        self.label.configure(image=self.current_image)

    def clear(self) -> None:
        self.current_image = None
        self.label.configure(image="")


class MiimgEncodePanel(BasePanel):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(
            master,
            "MIIMG Encoder",
            "Odaberi običnu sliku, po želji resizeaj i spremi u .miimg bez terminala.",
        )
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.width_var = tk.StringVar()
        self.height_var = tk.StringVar()
        self.force_alpha_var = tk.BooleanVar(value=False)

        form = ttk.Frame(self.body, style="Content.TFrame")
        form.grid(row=0, column=0, sticky="nw")
        form.columnconfigure(1, weight=1)
        self._file_row(form, 0, "Input image", self.input_var, self.pick_input)
        self._file_row(form, 1, "Output .miimg", self.output_var, self.pick_output)
        ttk.Label(form, text="Resize width", style="Field.TLabel").grid(row=2, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(form, textvariable=self.width_var, width=12).grid(row=2, column=1, sticky="w", pady=(12, 0))
        ttk.Label(form, text="Resize height", style="Field.TLabel").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=self.height_var, width=12).grid(row=3, column=1, sticky="w", pady=(8, 0))
        ttk.Checkbutton(form, text="Force opaque alpha", variable=self.force_alpha_var).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(12, 0)
        )
        actions = ttk.Frame(form, style="Content.TFrame")
        actions.grid(row=5, column=0, columnspan=3, sticky="w", pady=(16, 0))
        ttk.Button(actions, text="Load Sample", command=self.load_sample).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="Refresh Preview", command=lambda: run_action(self, self.refresh_preview)).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(actions, text="Encode", style="Accent.TButton", command=lambda: run_action(self, self.encode)).grid(row=0, column=2)
        self.preview = PreviewBox(self.body, "Input preview")
        self.preview.grid(row=1, column=0, sticky="nsew", pady=(18, 0))

    def _file_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, browse_command: Callable[[], None]) -> None:
        ttk.Label(parent, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=variable, width=78).grid(row=row, column=1, sticky="ew", padx=(12, 8))
        ttk.Button(parent, text="Browse", command=browse_command).grid(row=row, column=2, sticky="e")

    def load_sample(self) -> None:
        sample = PATHS.miimg_samples / "maca.png"
        if not sample.exists():
            raise FileNotFoundError(sample)
        self.input_var.set(str(sample))
        self.output_var.set(str(PATHS.miimg_output / "maca.miimg"))
        self.refresh_preview()

    def pick_input(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Open input image", initialdir=PATHS.miimg_input, filetypes=[("Images", "*.png *.bmp *.jpg *.jpeg *.webp"), ("All files", "*.*")])
        if not path:
            return
        path_obj = Path(path)
        self.input_var.set(path)
        self.output_var.set(str(PATHS.miimg_output / f"{path_obj.stem}.miimg"))
        run_action(self, self.refresh_preview)

    def pick_output(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, title="Save MIIMG as", initialdir=PATHS.miimg_output, defaultextension=".miimg", filetypes=[("MIIMG", "*.miimg")])
        if path:
            self.output_var.set(path)

    def refresh_preview(self) -> None:
        if not self.input_var.get():
            return
        img = Image.open(self.input_var.get()).convert("RGBA")
        self.preview.set_image(composite_for_preview(img))
        self.set_status(f"Preview loaded: {img.width}x{img.height}")

    def encode(self) -> None:
        input_path = Path(self.input_var.get())
        output_path = Path(self.output_var.get())
        if not self.input_var.get() or not input_path.exists():
            raise ValueError("Odaberi ulaznu sliku.")
        if not self.output_var.get():
            raise ValueError("Odaberi izlaznu .miimg datoteku.")
        width = self.width_var.get().strip()
        height = self.height_var.get().strip()
        resize_width = parse_int(width, "Width") if width else None
        resize_height = parse_int(height, "Height") if height else None
        if (resize_width is None) != (resize_height is None):
            raise ValueError("Width i height moraju biti zadani zajedno.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        miimg_encoder.write_miimg(
            input_path=input_path,
            output_path=output_path,
            resize_width=resize_width,
            resize_height=resize_height,
            force_opaque_alpha=self.force_alpha_var.get(),
        )
        self.set_status(f"Saved MIIMG: {output_path.name}")


class MiimgDecodePanel(BasePanel):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(
            master,
            "MIIMG Decoder",
            "Otvori .miimg, pregledaj rezultat i spremi ga kao PNG iz istog GUI-ja.",
        )
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.meta_var = tk.StringVar(value="No file loaded.")
        form = ttk.Frame(self.body, style="Content.TFrame")
        form.grid(row=0, column=0, sticky="nw")
        form.columnconfigure(1, weight=1)
        self._file_row(form, 0, "Input .miimg", self.input_var, self.pick_input)
        self._file_row(form, 1, "Output .png", self.output_var, self.pick_output)
        ttk.Label(form, textvariable=self.meta_var, style="Mono.TLabel").grid(row=2, column=0, columnspan=3, sticky="w", pady=(12, 0))
        actions = ttk.Frame(form, style="Content.TFrame")
        actions.grid(row=3, column=0, columnspan=3, sticky="w", pady=(16, 0))
        ttk.Button(actions, text="Load Sample", command=self.load_sample).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="Decode Preview", command=lambda: run_action(self, self.refresh_preview)).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(actions, text="Save PNG", style="Accent.TButton", command=lambda: run_action(self, self.decode)).grid(row=0, column=2)
        self.preview = PreviewBox(self.body, "Decoded preview")
        self.preview.grid(row=1, column=0, sticky="nsew", pady=(18, 0))

    def _file_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, browse_command: Callable[[], None]) -> None:
        ttk.Label(parent, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=variable, width=78).grid(row=row, column=1, sticky="ew", padx=(12, 8))
        ttk.Button(parent, text="Browse", command=browse_command).grid(row=row, column=2, sticky="e")

    def load_sample(self) -> None:
        sample = PATHS.miimg_samples / "maca.miimg"
        if not sample.exists():
            raise FileNotFoundError(sample)
        self.input_var.set(str(sample))
        self.output_var.set(str(PATHS.miimg_output / "maca_decoded.png"))
        self.refresh_preview()

    def pick_input(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Open MIIMG", initialdir=PATHS.miimg_input, filetypes=[("MIIMG", "*.miimg"), ("All files", "*.*")])
        if not path:
            return
        path_obj = Path(path)
        self.input_var.set(path)
        self.output_var.set(str(PATHS.miimg_output / f"{path_obj.stem}.png"))
        run_action(self, self.refresh_preview)

    def pick_output(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, title="Save PNG as", initialdir=PATHS.miimg_output, defaultextension=".png", filetypes=[("PNG", "*.png")])
        if path:
            self.output_var.set(path)

    def refresh_preview(self) -> None:
        if not self.input_var.get():
            return
        img, width, height = miimg_decoder.decode_miimg(self.input_var.get())
        self.preview.set_image(composite_for_preview(img))
        self.meta_var.set(f"Size: {width}x{height}")
        self.set_status(f"Decoded preview ready for {Path(self.input_var.get()).name}")

    def decode(self) -> None:
        input_path = Path(self.input_var.get())
        output_path = Path(self.output_var.get())
        if not self.input_var.get() or not input_path.exists():
            raise ValueError("Odaberi ulaznu .miimg datoteku.")
        if not self.output_var.get():
            raise ValueError("Odaberi izlazni PNG.")
        img, width, height = miimg_decoder.decode_miimg(input_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path)
        self.preview.set_image(composite_for_preview(img))
        self.meta_var.set(f"Size: {width}x{height}")
        self.set_status(f"Saved PNG: {output_path.name}")


class MigifEncodePanel(BasePanel):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(
            master,
            "MIGIF Encoder",
            "Pretvori GIF u MIGIF, uz opcionalni FPS override i kontrolu palete, sve kroz formu.",
        )
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.force_rgba_var = tk.BooleanVar(value=False)
        self.force_full_var = tk.StringVar(value="0")
        self.fps_var = tk.StringVar()
        form = ttk.Frame(self.body, style="Content.TFrame")
        form.grid(row=0, column=0, sticky="nw")
        form.columnconfigure(1, weight=1)
        self._file_row(form, 0, "Input GIF", self.input_var, self.pick_input)
        self._file_row(form, 1, "Output .migif", self.output_var, self.pick_output)
        ttk.Checkbutton(form, text="Force RGBA (no global palette)", variable=self.force_rgba_var).grid(row=2, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Label(form, text="Force full every N", style="Field.TLabel").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(form, textvariable=self.force_full_var, width=12).grid(row=3, column=1, sticky="w", pady=(10, 0))
        ttk.Label(form, text="FPS override", style="Field.TLabel").grid(row=4, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(form, textvariable=self.fps_var, width=16).grid(row=4, column=1, sticky="w", pady=(10, 0))
        ttk.Label(form, text="Primjer: 30 ili 30000/1001", style="Hint.TLabel").grid(row=4, column=2, sticky="w", pady=(10, 0))
        actions = ttk.Frame(form, style="Content.TFrame")
        actions.grid(row=5, column=0, columnspan=3, sticky="w", pady=(16, 0))
        ttk.Button(actions, text="Load Sample", command=self.load_sample).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="Preview GIF", command=lambda: run_action(self, self.refresh_preview)).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(actions, text="Encode", style="Accent.TButton", command=lambda: run_action(self, self.encode)).grid(row=0, column=2)
        self.preview = PreviewBox(self.body, "GIF first frame")
        self.preview.grid(row=1, column=0, sticky="nsew", pady=(18, 0))

    def _file_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, browse_command: Callable[[], None]) -> None:
        ttk.Label(parent, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=variable, width=78).grid(row=row, column=1, sticky="ew", padx=(12, 8))
        ttk.Button(parent, text="Browse", command=browse_command).grid(row=row, column=2, sticky="e")

    def load_sample(self) -> None:
        sample = PATHS.migif_samples / "test.gif"
        if not sample.exists():
            raise FileNotFoundError(sample)
        self.input_var.set(str(sample))
        self.output_var.set(str(PATHS.migif_output / "test.migif"))
        self.refresh_preview()

    def pick_input(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Open GIF", initialdir=PATHS.migif_input, filetypes=[("GIF", "*.gif"), ("All files", "*.*")])
        if not path:
            return
        path_obj = Path(path)
        self.input_var.set(path)
        self.output_var.set(str(PATHS.migif_output / f"{path_obj.stem}.migif"))
        run_action(self, self.refresh_preview)

    def pick_output(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, title="Save MIGIF as", initialdir=PATHS.migif_output, defaultextension=".migif", filetypes=[("MIGIF", "*.migif")])
        if path:
            self.output_var.set(path)

    def refresh_preview(self) -> None:
        if not self.input_var.get():
            return
        img = Image.open(self.input_var.get())
        frame = ImageOps.exif_transpose(img.copy()).convert("RGBA")
        self.preview.set_image(composite_for_preview(frame))
        self.set_status(f"GIF preview loaded: {frame.width}x{frame.height}")

    def encode(self) -> None:
        input_path = Path(self.input_var.get())
        output_path = Path(self.output_var.get())
        if not self.input_var.get() or not input_path.exists():
            raise ValueError("Odaberi ulazni GIF.")
        if not self.output_var.get():
            raise ValueError("Odaberi izlazni .migif.")
        force_full_every = parse_int(self.force_full_var.get().strip() or "0", "Force full every", minimum=0)
        fps_override = migif_encoder.parse_fps(self.fps_var.get().strip()) if self.fps_var.get().strip() else None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        migif_encoder.convert_gif_to_migif(
            input_path=input_path,
            output_path=output_path,
            force_rgba=self.force_rgba_var.get(),
            force_full_every=force_full_every,
            fps_override=fps_override,
        )
        self.set_status(f"Saved MIGIF: {output_path.name}")


class MigifPlayerPanel(BasePanel):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(
            master,
            "MIGIF Player",
            "Učitaj MIGIF, pregledaj osnovne metapodatke i pokreni player iz istog launchera.",
        )
        self.input_var = tk.StringVar()
        self.scale_var = tk.StringVar(value="1")
        self.bg_var = tk.StringVar(value="0,0,0")
        self.meta_var = tk.StringVar(value="No MIGIF loaded.")
        self.loaded_migif: Optional[migif_decoder.MigifFile] = None
        form = ttk.Frame(self.body, style="Content.TFrame")
        form.grid(row=0, column=0, sticky="nw")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Input .migif", style="Field.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.input_var, width=78).grid(row=0, column=1, sticky="ew", padx=(12, 8))
        ttk.Button(form, text="Browse", command=self.pick_input).grid(row=0, column=2, sticky="e")
        ttk.Label(form, text="Window scale", style="Field.TLabel").grid(row=1, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(form, textvariable=self.scale_var, width=12).grid(row=1, column=1, sticky="w", pady=(12, 0))
        ttk.Label(form, text="Background RGB", style="Field.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(form, textvariable=self.bg_var, width=16).grid(row=2, column=1, sticky="w", pady=(10, 0))
        ttk.Label(form, textvariable=self.meta_var, style="Mono.TLabel", justify="left").grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 0))
        actions = ttk.Frame(form, style="Content.TFrame")
        actions.grid(row=4, column=0, columnspan=3, sticky="w", pady=(16, 0))
        ttk.Button(actions, text="Load Sample", command=self.load_sample).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="Inspect", command=lambda: run_action(self, self.inspect)).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(actions, text="Play", style="Accent.TButton", command=lambda: run_action(self, self.play)).grid(row=0, column=2)
        self.preview = PreviewBox(self.body, "First frame preview")
        self.preview.grid(row=1, column=0, sticky="nsew", pady=(18, 0))

    def load_sample(self) -> None:
        sample = PATHS.migif_samples / "test.migif"
        if not sample.exists():
            raise FileNotFoundError(sample)
        self.input_var.set(str(sample))
        self.inspect()

    def pick_input(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Open MIGIF", initialdir=PATHS.migif_input, filetypes=[("MIGIF", "*.migif"), ("All files", "*.*")])
        if path:
            self.input_var.set(path)
            run_action(self, self.inspect)

    def _parse_bg(self) -> tuple[int, int, int]:
        return migif_decoder.parse_bg(self.bg_var.get().strip())

    def _build_preview(self, migif_file: migif_decoder.MigifFile) -> Image.Image:
        width = migif_file.header.canvas_width
        height = migif_file.header.canvas_height
        canvas = bytearray(width * height * 4)
        for frame in migif_file.frames:
            if frame.frame_type == migif_decoder.MIGIF_FRAME_FULL:
                img_w, img_h, pixels = migif_decoder.decode_full_payload_bytes(frame.payload, migif_file.palette)
                migif_decoder.apply_full_to_canvas_bytes(canvas, width, height, img_w, img_h, pixels)
                break
            if frame.frame_type == migif_decoder.MIGIF_FRAME_DELTA:
                migif_decoder.apply_delta_payload_bytes(frame.payload, canvas, width, height, migif_file.palette)
                break
        return composite_for_preview(Image.frombytes("RGBA", (width, height), bytes(canvas)))

    def inspect(self) -> None:
        input_path = Path(self.input_var.get())
        if not self.input_var.get() or not input_path.exists():
            raise ValueError("Odaberi ulazni .migif.")
        self.loaded_migif = migif_decoder.load_migif(input_path)
        header = self.loaded_migif.header
        info_lines = [
            f"Canvas: {header.canvas_width}x{header.canvas_height}",
            f"Frames: {header.frame_count}",
            f"FPS: {header.fps_num}/{header.fps_den}",
            f"Loop: {'yes' if header.flags & migif_decoder.MIGIF_FLAG_LOOP else 'no'}",
            f"Palette: {len(self.loaded_migif.palette) if self.loaded_migif.palette else 0} colors",
        ]
        self.meta_var.set("\n".join(info_lines))
        self.preview.set_image(self._build_preview(self.loaded_migif))
        self.set_status(f"Loaded MIGIF: {input_path.name}")

    def play(self) -> None:
        if self.loaded_migif is None:
            self.inspect()
        scale = parse_int(self.scale_var.get().strip() or "1", "Scale")
        background = self._parse_bg()
        assert self.loaded_migif is not None
        self.set_status("Player running. Zatvori player prozor ili stisni ESC za povratak.")
        migif_decoder.play_migif(self.loaded_migif, scale_window=scale, background=background)
        self.set_status("Player closed.")


class MigifEditorPanel(BasePanel):
    def __init__(self, master: tk.Misc, app: "CreatorApp") -> None:
        super().__init__(
            master,
            "MIGIF Editor",
            "Otvori editor kao dio iste aplikacije. Možeš krenuti od praznog projekta, .migif ili .proj datoteke.",
        )
        self.app = app
        self.input_var = tk.StringVar()
        form = ttk.Frame(self.body, style="Content.TFrame")
        form.grid(row=0, column=0, sticky="nw")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Optional input", style="Field.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.input_var, width=78).grid(row=0, column=1, sticky="ew", padx=(12, 8))
        ttk.Button(form, text="Browse", command=self.pick_input).grid(row=0, column=2, sticky="e")
        actions = ttk.Frame(form, style="Content.TFrame")
        actions.grid(row=1, column=0, columnspan=3, sticky="w", pady=(18, 0))
        ttk.Button(actions, text="Open Empty Editor", command=lambda: run_action(self, self.open_empty)).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="Open With File", style="Accent.TButton", command=lambda: run_action(self, self.open_with_input)).grid(row=0, column=1)
        notes = ttk.Frame(self.body, style="Card.TFrame", padding=18)
        notes.grid(row=1, column=0, sticky="nw", pady=(18, 0))
        ttk.Label(notes, text="Suggested paths", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(notes, text="\n".join([f"Samples:  {PATHS.migif_samples}", f"Projects: {PATHS.migif_projects}", f"Output:   {PATHS.migif_output}"]), style="Mono.TLabel", justify="left").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.set_status("Editor otvara zaseban prozor iz istog launchera.")

    def pick_input(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Open MIGIF or project", initialdir=PATHS.migif_samples, filetypes=[("MIGIF / Project", "*.migif *.proj"), ("All files", "*.*")])
        if path:
            self.input_var.set(path)

    def open_empty(self) -> None:
        migif_editor.launch_editor_window(parent=self.app.root)
        self.set_status("Opened empty editor window.")

    def open_with_input(self) -> None:
        input_text = self.input_var.get().strip()
        if not input_text:
            raise ValueError("Odaberi .migif ili .proj datoteku.")
        migif_editor.launch_editor_window(parent=self.app.root, input_path=Path(input_text))
        self.set_status(f"Opened editor with {Path(input_text).name}")


class CreatorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MIOS Creator App")
        self.root.geometry("1260x860")
        self.root.minsize(1080, 760)
        self._configure_styles()
        self._build_layout()

    def _configure_styles(self) -> None:
        colors = {
            "paper": "#f6f1e7",
            "paper_alt": "#fffaf0",
            "ink": "#1e2a24",
            "muted": "#56645c",
            "accent": "#0e8f61",
            "accent_active": "#14a874",
            "line": "#d8cfbf",
            "card": "#fbf7ef",
            "nav": "#1f2a24",
            "nav_active": "#2e4238",
            "nav_text": "#f8f3e8",
        }
        self.root.configure(bg=colors["paper"])
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TFrame", background=colors["paper"])
        style.configure("Content.TFrame", background=colors["paper"])
        style.configure("Card.TFrame", background=colors["card"], relief="solid", borderwidth=1)
        style.configure("Sidebar.TFrame", background=colors["nav"])
        style.configure("Title.TLabel", background=colors["paper"], foreground=colors["ink"], font=("Segoe UI Semibold", 20))
        style.configure("Section.TLabel", background=colors["card"], foreground=colors["ink"], font=("Segoe UI Semibold", 12))
        style.configure("Body.TLabel", background=colors["paper"], foreground=colors["ink"])
        style.configure("Field.TLabel", background=colors["paper"], foreground=colors["ink"], font=("Segoe UI Semibold", 10))
        style.configure("Hint.TLabel", background=colors["paper"], foreground=colors["muted"])
        style.configure("Status.TLabel", background=colors["paper"], foreground=colors["muted"], font=("Consolas", 9))
        style.configure("Mono.TLabel", background=colors["card"], foreground=colors["ink"], font=("Consolas", 9))
        style.configure("TButton", background=colors["paper_alt"], foreground=colors["ink"], borderwidth=1, padding=(12, 8))
        style.map("TButton", background=[("active", colors["paper"])])
        style.configure("Accent.TButton", background=colors["accent"], foreground="#ffffff", borderwidth=0, padding=(12, 8))
        style.map("Accent.TButton", background=[("active", colors["accent_active"])])
        style.configure("TCheckbutton", background=colors["paper"], foreground=colors["ink"])
        style.configure("TEntry", fieldbackground="#ffffff", foreground=colors["ink"], bordercolor=colors["line"])
        self.nav_button_style = {
            "bg": colors["nav"],
            "fg": colors["nav_text"],
            "activebackground": colors["nav_active"],
            "activeforeground": colors["nav_text"],
            "relief": "flat",
            "bd": 0,
            "padx": 16,
            "pady": 12,
            "anchor": "w",
            "font": ("Segoe UI Semibold", 11),
        }

    def _build_layout(self) -> None:
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(self.root, style="Sidebar.TFrame", width=240)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        tk.Label(sidebar, text="MIOS\nCreator", bg=self.nav_button_style["bg"], fg=self.nav_button_style["fg"], font=("Bahnschrift SemiBold", 22), justify="left", padx=16, pady=18).grid(row=0, column=0, sticky="ew")

        self.panel_container = ttk.Frame(self.root, style="Content.TFrame")
        self.panel_container.grid(row=0, column=1, sticky="nsew")
        self.panel_container.columnconfigure(0, weight=1)
        self.panel_container.rowconfigure(0, weight=1)

        self.panels = {
            "home": HomePanel(self.panel_container, self),
            "miimg_encode": MiimgEncodePanel(self.panel_container),
            "miimg_decode": MiimgDecodePanel(self.panel_container),
            "migif_encode": MigifEncodePanel(self.panel_container),
            "migif_player": MigifPlayerPanel(self.panel_container),
            "migif_editor": MigifEditorPanel(self.panel_container, self),
        }
        for panel in self.panels.values():
            panel.grid(row=0, column=0, sticky="nsew")

        nav_items = [
            ("home", "Overview"),
            ("miimg_encode", "MIIMG Encode"),
            ("miimg_decode", "MIIMG Decode"),
            ("migif_encode", "MIGIF Encode"),
            ("migif_player", "MIGIF Player"),
            ("migif_editor", "MIGIF Editor"),
        ]
        self.nav_buttons: dict[str, tk.Button] = {}
        for index, (panel_key, title) in enumerate(nav_items, start=1):
            button = tk.Button(sidebar, text=title, command=lambda key=panel_key: self.show_panel(key), **self.nav_button_style)
            button.grid(row=index, column=0, sticky="ew")
            self.nav_buttons[panel_key] = button
        self.show_panel("home")

    def show_panel(self, panel_key: str) -> None:
        self.panels[panel_key].tkraise()
        for key, button in self.nav_buttons.items():
            button.configure(bg=self.nav_button_style["activebackground"] if key == panel_key else self.nav_button_style["bg"])


def main() -> None:
    ensure_directories()
    root = tk.Tk()
    CreatorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
