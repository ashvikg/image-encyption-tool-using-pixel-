#!/usr/bin/env python3
"""
image_encrypt_gui.py

Simple Image Encryption/Decryption via pixel manipulation with a Tkinter GUI.

Dependencies:
    pip install pillow numpy

Usage (GUI):
    python image_encrypt_gui.py

Usage (CLI example inside code if needed):
    # The GUI is the default; this script focuses on GUI usage.
"""
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk
import numpy as np
import os

# --- Core image ops (pure functions) ---


def ensure_rgba(arr: np.ndarray) -> np.ndarray:
    """Ensure shape (H, W, 4) uint8."""
    if arr.ndim != 3 or arr.shape[2] not in (3, 4):
        raise ValueError("Input array must be HxWx3 or HxWx4")
    if arr.shape[2] == 3:
        # add alpha = 255
        alpha = np.full((arr.shape[0], arr.shape[1], 1), 255, dtype=np.uint8)
        arr = np.concatenate([arr, alpha], axis=2)
    return arr.astype(np.uint8)


def encrypt_xor(arr: np.ndarray, key: int) -> np.ndarray:
    """XOR every channel by key (0..255)."""
    if not (0 <= key <= 255):
        raise ValueError("Key must be 0..255")
    return (arr ^ key).astype(np.uint8)


def encrypt_add(arr: np.ndarray, key: int, inverse: bool = False) -> np.ndarray:
    """Add key mod 256. If inverse=True, subtract key."""
    if not (0 <= key <= 255):
        raise ValueError("Key must be 0..255")
    if inverse:
        out = (arr.astype(np.int16) - key) % 256
    else:
        out = (arr.astype(np.int16) + key) % 256
    return out.astype(np.uint8)


def channel_swap(arr: np.ndarray, perm: str, inverse: bool = False) -> np.ndarray:
    """
    perm examples: 'BGR' or 'BGRA' or 'GRB' etc.
    Works on RGBA array; for 3-letter strings alpha remains last.
    """
    mapping = {'R': 0, 'G': 1, 'B': 2, 'A': 3}
    perm = perm.upper()
    if not all(ch in mapping for ch in perm):
        raise ValueError("perm must be letters from RGBA, e.g., BGR or BGRA")
    if len(perm) not in (3, 4):
        raise ValueError("perm length should be 3 or 4")
    # Build 4-length permutation
    if len(perm) == 3:
        perm_idx = [mapping[c] for c in perm] + [3]
    else:
        perm_idx = [mapping[c] for c in perm]
    if inverse:
        inv = [0] * 4
        for i, p in enumerate(perm_idx):
            inv[p] = i
        perm_idx = inv
    return arr[..., perm_idx]


def permute_pixels(arr: np.ndarray, seed: int, inverse: bool = False) -> np.ndarray:
    """
    Permutes pixel positions. Deterministic shuffle from seed.
    inverse=True applies inverse permutation.
    """
    h, w, c = arr.shape
    n = h * w
    rng = np.random.default_rng(int(seed))
    idx = np.arange(n)
    rng.shuffle(idx)
    if inverse:
        # build inverse mapping
        inv = np.empty_like(idx)
        inv[idx] = np.arange(n)
        idx = inv
    flat = arr.reshape((n, c))
    out = flat[idx].reshape((h, w, c))
    return out.astype(np.uint8)


def swap_pairs(arr: np.ndarray, seed: int, inverse: bool = False) -> np.ndarray:
    """
    Swap pixel pairs deterministically. seed controls RNG.
    This operation is its own inverse if the same swaps are applied again,
    but for clarity we compute and apply the exact inverse mapping when inverse=True.
    """
    h, w, c = arr.shape
    n = h * w
    rng = np.random.default_rng(int(seed))
    perm = np.arange(n)
    rng.shuffle(perm)
    # Create swaps: pair i with perm[i]
    # For deterministic reversible swapping, apply mapping: out[i] = in[perm[i]]
    # Inverse mapping is inv[perm[i]] = i
    flat = arr.reshape((n, c))
    if inverse:
        inv = np.empty_like(perm)
        inv[perm] = np.arange(n)
        out = flat[inv]
    else:
        out = flat[perm]
    return out.reshape((h, w, c)).astype(np.uint8)


# --- GUI application ---


class ImageCipherApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Image Encryption (Pixel Manipulation)")
        self.image_path = None
        self.original_arr = None
        self.display_img = None

        # UI Elements
        frm = tk.Frame(root)
        frm.pack(padx=8, pady=8)

        # Buttons: Load / Save / Encrypt / Decrypt
        btn_load = tk.Button(frm, text="Load Image", command=self.load_image)
        btn_load.grid(row=0, column=0, padx=4, pady=4)

        btn_save = tk.Button(frm, text="Save Result", command=self.save_image)
        btn_save.grid(row=0, column=1, padx=4, pady=4)

        btn_encrypt = tk.Button(frm, text="Encrypt →", command=lambda: self.process(mode="encrypt"))
        btn_encrypt.grid(row=0, column=2, padx=4, pady=4)

        btn_decrypt = tk.Button(frm, text="← Decrypt", command=lambda: self.process(mode="decrypt"))
        btn_decrypt.grid(row=0, column=3, padx=4, pady=4)

        # Operation choices
        ops_frame = tk.LabelFrame(frm, text="Operations (applied in order)")
        ops_frame.grid(row=1, column=0, columnspan=4, sticky="we", pady=6)

        self.ops_var = tk.StringVar(value="xor:add")
        tk.Entry(ops_frame, textvariable=self.ops_var, width=40).grid(row=0, column=0, padx=6, pady=6)
        tk.Label(ops_frame, text="(colon-separated: xor,add,channel_swap,permute,swap_pairs)").grid(row=0, column=1)

        # Key / Channels
        tk.Label(frm, text="Key/Seed (int 0..4294967...):").grid(row=2, column=0, sticky="w")
        self.key_var = tk.StringVar(value="42")
        tk.Entry(frm, textvariable=self.key_var, width=12).grid(row=2, column=1, sticky="w")

        tk.Label(frm, text="Channel perm (for channel_swap, e.g. BGR):").grid(row=2, column=2, sticky="w")
        self.chan_var = tk.StringVar(value="BGR")
        tk.Entry(frm, textvariable=self.chan_var, width=8).grid(row=2, column=3, sticky="w")

        # Canvas to show image
        self.canvas = tk.Canvas(root, width=520, height=360, bg="#ddd")
        self.canvas.pack(padx=8, pady=8)
        tk.Label(root, text="Preview (alpha preserved). Load an image to begin.").pack()

        # status
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(root, textvariable=self.status_var).pack(pady=4)

    def load_image(self):
        path = filedialog.askopenfilename(title="Choose image",
                                          filetypes=[("Image files", "*.png;*.jpg;*.jpeg;*.bmp;*.gif"), ("All files", "*.*")])
        if not path:
            return
        try:
            im = Image.open(path).convert("RGBA")
            arr = np.array(im, dtype=np.uint8)
            self.original_arr = ensure_rgba(arr)
            self.image_path = path
            self.show_preview(self.original_arr)
            self.status_var.set(f"Loaded: {os.path.basename(path)} ({self.original_arr.shape[1]}x{self.original_arr.shape[0]})")
        except Exception as e:
            messagebox.showerror("Load error", f"Failed to load image: {e}")

    def save_image(self):
        if self.display_img is None:
            messagebox.showinfo("No image", "No result to save. Please encrypt/decrypt first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG", "*.png")])
        if not path:
            return
        try:
            # display_img is PIL Image
            self.display_img.save(path)
            messagebox.showinfo("Saved", f"Saved to {path}")
        except Exception as e:
            messagebox.showerror("Save error", f"Failed to save image: {e}")

    def show_preview(self, arr: np.ndarray):
        im = Image.fromarray(arr, mode="RGBA")
        # Resize for preview while keeping aspect ratio
        w, h = im.size
        maxw, maxh = 512, 320
        scale = min(maxw / w, maxh / h, 1.0)
        if scale < 1.0:
            im_thumb = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        else:
            im_thumb = im
        self.display_img = im  # full-size PIL image for saving
        im_tk = ImageTk.PhotoImage(im_thumb)
        self.canvas.delete("all")
        self.canvas.image = im_tk  # keep reference
        self.canvas.create_image( (maxw//2, maxh//2), image=im_tk, anchor="center")

    def process(self, mode="encrypt"):
        if self.original_arr is None:
            messagebox.showinfo("No image", "Please load an image first.")
            return
        ops_text = self.ops_var.get().strip()
        if not ops_text:
            messagebox.showinfo("Specify ops", "Please enter operations (colon-separated).")
            return
        ops = [s.strip().lower() for s in ops_text.split(":") if s.strip()]
        try:
            key = int(self.key_var.get())
        except Exception:
            messagebox.showerror("Key error", "Key/seed must be an integer.")
            return
        channels = self.chan_var.get().strip().upper() or "BGR"

        # prepare array copy
        arr = self.original_arr.copy()
        inverse = (mode == "decrypt")
        # decryption: inverse operations in reverse order
        if inverse:
            ops_to_apply = ops[::-1]
        else:
            ops_to_apply = ops

        try:
            for op in ops_to_apply:
                if op == "xor":
                    # XOR is self-inverse, same op for encrypt/decrypt
                    arr = encrypt_xor(arr, key)
                elif op == "add":
                    arr = encrypt_add(arr, key, inverse=inverse)
                elif op == "channel_swap":
                    arr = channel_swap(arr, channels, inverse=inverse)
                elif op == "permute":
                    arr = permute_pixels(arr, key, inverse=inverse)
                elif op == "swap_pairs":
                    arr = swap_pairs(arr, key, inverse=inverse)
                elif op in ("none", "noop", "skip"):
                    continue
                else:
                    raise ValueError(f"Unsupported op: {op}")
        except Exception as e:
            messagebox.showerror("Processing error", f"Error applying operations: {e}")
            return

        self.show_preview(arr)
        self.status_var.set(f"{mode.title()}ed image (ops: {', '.join(ops)}). Use 'Save Result' to store.")

# --- main ---
def main():
    root = tk.Tk()
    app = ImageCipherApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
