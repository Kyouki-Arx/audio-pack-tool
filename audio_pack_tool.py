"""TClient / DDNet audio pack editor.

Per-sample editor for the engine's audio asset packs: every sound set and every
variant inside it can be replaced individually, taken from another pack, or
restored to the built-in default.
"""

from __future__ import annotations

import os
import queue
import struct
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import packstore
import soundcatalog
from wavpack_codec import WavpackCodec, WavpackError

APP_NAME = "TClient Audio Pack Editor"
STATE_PACK = "custom"
STATE_DEFAULT = "default"
STATE_MISSING = "missing"


def human_time(seconds: float) -> str:
    if not seconds:
        return "-"
    if seconds < 1:
        return "%.0f ms" % (seconds * 1000)
    return "%.2f s" % seconds


def human_size(n: int) -> str:
    if not n:
        return "-"
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.0f KB" % (n / 1024.0)
    return "%.1f MB" % (n / (1024.0 * 1024.0))


_PREVIEW_FILES = []


def play_pcm(pcm: bytes, rate: int, channels: int, loop: bool = False) -> str:
    stop_playback()
    fd, path = tempfile.mkstemp(prefix="apt_preview_", suffix=".wav")
    os.close(fd)
    packstore.write_wav(path, pcm, rate, channels)
    _PREVIEW_FILES.append(path)
    while len(_PREVIEW_FILES) > 6:
        old = _PREVIEW_FILES.pop(0)
        try:
            os.remove(old)
        except OSError:
            pass
    try:
        import winsound
    except ImportError:
        os.startfile(path)
        return path
    flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
    if loop:
        flags |= winsound.SND_LOOP
    winsound.PlaySound(path, flags)
    return path


def stop_playback() -> None:
    try:
        import winsound
        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


def cleanup_preview_files() -> None:
    tmp = tempfile.gettempdir()
    try:
        names = os.listdir(tmp)
    except OSError:
        return
    for name in names:
        if name.startswith("apt_preview_") and name.endswith(".wav"):
            try:
                os.remove(os.path.join(tmp, name))
            except OSError:
                pass


def build_envelope(dec, buckets: int = 1200):
    frames = dec["samples"]
    ch = dec["channels"]
    if frames <= 0:
        return []
    vals = struct.unpack("<%dh" % (frames * ch), dec["pcm"][: frames * ch * 2])
    step = max(1, frames // buckets)
    env = []
    for start in range(0, frames, step):
        end = min(frames, start + step)
        peak = 0
        for i in range(start, end):
            v = vals[i * ch]
            if v < 0:
                v = -v
            if v > peak:
                peak = v
        env.append(peak / 32768.0)
    return env


class AudioPackApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1180x760")
        self.minsize(980, 620)
        try:
            self.tk.call("tk", "scaling", 1.25)
        except tk.TclError:
            pass

        self.settings = packstore.load_settings()
        self.codec = None
        self.codec_error = ""
        self._load_codec()

        self.store = packstore.PackStore(self.codec, packstore.guess_pack_root())
        self.convert_options = self._options_from_settings()

        self.current_pack = ""
        self.current_set = ""
        self.current_pack_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.root_var = tk.StringVar(value=self.store.root)
        self.info_var = tk.StringVar(value="")
        self.summary_var = tk.StringVar(value="")
        self.loop_var = tk.BooleanVar(value=False)
        self._play_path = None
        self._waveform = None
        self._envelope = None
        self._wave_token = 0
        self._bg_results = queue.Queue()

        self._build_ui()
        self._refresh_codec_state()
        self.after(60, self._poll_bg_results)
        self.refresh_packs(select=self.settings.get("last_pack") or None)

    # ---------------- setup ----------------

    def _load_codec(self):
        try:
            self.codec = WavpackCodec(self.settings.get("wavpack_dll"))
            self.codec_error = ""
        except WavpackError as e:
            self.codec = None
            self.codec_error = str(e)

    def _options_from_settings(self):
        s = self.settings
        return packstore.ConvertOptions(
            target_rate=int(s.get("target_rate", 0) or 0),
            normalize=bool(s.get("normalize", False)),
            gain_db=float(s.get("gain_db", 0) or 0),
            trim_silence=bool(s.get("trim_silence", False)),
            fade_ms=float(s.get("fade_ms", 0) or 0),
            force_mono=bool(s.get("force_mono", False)),
            fast=bool(s.get("fast_encode", True)),
        )

    def _build_ui(self):
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=22)

        self._build_menu()

        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Packs folder:").pack(side="left")
        entry = ttk.Entry(top, textvariable=self.root_var)
        entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
        entry.bind("<Return>", lambda _e: self._apply_root(self.root_var.get()))
        ttk.Button(top, text="Browse...", command=self._browse_root).pack(side="left")
        ttk.Button(top, text="Refresh", command=lambda: self._apply_root(self.root_var.get())).pack(side="left", padx=(6, 0))

        packbar = ttk.Frame(self, padding=(10, 0, 10, 6))
        packbar.pack(fill="x")
        ttk.Label(packbar, text="Pack:").pack(side="left")
        self.pack_combo = ttk.Combobox(packbar, textvariable=self.current_pack_var, state="readonly", width=28)
        self.pack_combo.pack(side="left", padx=(6, 8))
        self.pack_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_pack_selected())
        ttk.Button(packbar, text="New pack", command=self._new_pack).pack(side="left")
        ttk.Button(packbar, text="Clone built-in", command=self._clone_default).pack(side="left", padx=(4, 0))
        ttk.Button(packbar, text="Duplicate", command=self._duplicate_pack).pack(side="left", padx=(4, 0))
        ttk.Button(packbar, text="Rename", command=self._rename_pack).pack(side="left", padx=(4, 0))
        ttk.Button(packbar, text="Delete", command=self._delete_pack).pack(side="left", padx=(4, 0))
        ttk.Button(packbar, text="Open folder", command=self._open_pack_folder).pack(side="left", padx=(4, 0))
        ttk.Label(packbar, textvariable=self.summary_var, foreground="#555").pack(side="right")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 4))

        left = ttk.Frame(body)
        ttk.Label(left, text="Sound sets").pack(anchor="w")
        self.sets_tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.sets_tree.pack(fill="both", expand=True)
        self.sets_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_set_selected())
        body.add(left, weight=1)

        right = ttk.Frame(body)
        self.set_header = ttk.Label(right, text="Select a sound set", font=("Segoe UI", 10, "bold"))
        self.set_header.pack(anchor="w")
        self.set_desc = ttk.Label(right, text="", foreground="#555", wraplength=760, justify="left")
        self.set_desc.pack(anchor="w", pady=(0, 4))

        cols = ("idx", "variant", "file", "state", "length", "rate", "ch", "size")
        self.slots = ttk.Treeview(right, columns=cols, show="headings", selectmode="extended")
        for col, text, width, anchor in (
                ("idx", "#", 40, "center"),
                ("variant", "variant", 110, "w"),
                ("file", "file in pack", 240, "w"),
                ("state", "state", 86, "w"),
                ("length", "length", 76, "e"),
                ("rate", "rate", 76, "e"),
                ("ch", "ch", 40, "center"),
                ("size", "size", 76, "e")):
            self.slots.heading(col, text=text)
            self.slots.column(col, width=width, anchor=anchor, stretch=(col == "file"))
        self.slots.pack(fill="both", expand=True)
        self.slots.bind("<<TreeviewSelect>>", lambda _e: self._on_slot_selected())
        self.slots.bind("<Double-1>", lambda _e: self._preview_selected())
        self.slots.tag_configure("custom", background="#e8f5e9")
        self.slots.tag_configure("missing", background="#fdecea")

        btns = ttk.Frame(right)
        btns.pack(fill="x", pady=(6, 4))
        ttk.Button(btns, text="Import file(s)...", command=self._import_files).pack(side="left")
        ttk.Button(btns, text="Take from pack...", command=self._open_copy_dialog).pack(side="left", padx=(4, 0))
        ttk.Button(btns, text="Restore default", command=self._restore_slots).pack(side="left", padx=(4, 0))
        ttk.Button(btns, text="Preview", command=self._preview_selected).pack(side="left", padx=(12, 0))
        ttk.Button(btns, text="Stop", command=self._stop_playback).pack(side="left", padx=(4, 0))
        ttk.Checkbutton(btns, text="loop", variable=self.loop_var).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Show file", command=self._show_slot_file).pack(side="right")

        self.wave = tk.Canvas(right, height=120, background="#ffffff", highlightthickness=1,
                              highlightbackground="#c8c8c8")
        self.wave.pack(fill="x", pady=(4, 2))
        self.wave.bind("<Configure>", lambda _e: self.after_idle(self._redraw_waveform))
        ttk.Label(right, textvariable=self.info_var, foreground="#333").pack(anchor="w")
        body.add(right, weight=3)

        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w",
                  padding=(8, 3)).pack(fill="x", side="bottom")

    def _build_menu(self):
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="New pack...", command=self._new_pack)
        file_menu.add_command(label="Clone built-in pack", command=self._clone_default)
        file_menu.add_separator()
        file_menu.add_command(label="Export current pack to .zip...", command=self._export_zip)
        file_menu.add_command(label="Import .zip into new pack...", command=self._import_zip)
        file_menu.add_separator()
        file_menu.add_command(label="Validate current pack", command=self._validate_pack)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)
        menubar.add_cascade(label="Pack", menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=False)
        settings_menu.add_command(label="Settings...", command=self._open_settings)
        settings_menu.add_command(label="Reload WavPack library", command=self._reload_codec)
        menubar.add_cascade(label="Settings", menu=settings_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="How packs work", command=self._show_help)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menubar)

    # ---------------- helpers ----------------

    def _refresh_codec_state(self):
        if self.codec:
            self.status_var.set("libwavpack %s loaded from %s" % (
                self.codec.lib.WavpackGetLibraryVersionString().decode(), self.codec.dll_path))
        else:
            self.status_var.set("WavPack library missing: %s" % self.codec_error)

    def _reload_codec(self):
        self._load_codec()
        self.store.codec = self.codec
        self._refresh_codec_state()
        self.refresh_slots()

    def _persist(self, **kw):
        self.settings.update(kw)
        packstore.save_settings(self.settings)

    def _require_pack(self):
        if not self.current_pack:
            messagebox.showinfo(APP_NAME, "Select or create a pack first.", parent=self)
            return False
        return True

    def _require_codec(self):
        if not self.codec:
            messagebox.showerror(APP_NAME,
                                 "libwavpack-1.dll was not found.\n\n"
                                 "Point the tool at the DLL that ships next to DDNet.exe / "
                                 "TClient.exe in Settings -> WavPack DLL.\n\n" + self.codec_error,
                                 parent=self)
            return False
        return True

    def run_bg(self, fn, on_done=None, message="Working..."):
        self.status_var.set(message)

        def worker():
            try:
                value = fn()
                self._bg_results.put((on_done, value, None))
            except Exception as e:
                self._bg_results.put((None, None, e))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_bg_results(self):
        try:
            while True:
                callback, value, error = self._bg_results.get_nowait()
                if error is not None:
                    self.status_var.set("Error: %s" % error)
                    messagebox.showerror(APP_NAME, str(error), parent=self)
                elif callback:
                    callback(value)
                else:
                    self.status_var.set("Done")
        except queue.Empty:
            pass
        self.after(60, self._poll_bg_results)

    # ---------------- pack list ----------------

    def _browse_root(self):
        d = filedialog.askdirectory(parent=self, initialdir=self.store.root, title="Select assets/audio folder")
        if d:
            self._apply_root(d)

    def _apply_root(self, path):
        path = os.path.abspath(os.path.expanduser(path.strip()))
        if not os.path.isdir(path):
            try:
                os.makedirs(path, exist_ok=True)
            except OSError as e:
                messagebox.showerror(APP_NAME, "Cannot use that folder:\n%s" % e, parent=self)
                return
        self.store.root = path
        self.root_var.set(path)
        self._persist(pack_root=path)
        self.refresh_packs()

    def refresh_packs(self, select=None):
        packs = self.store.packs()
        self.pack_combo["values"] = packs
        target = select or self.current_pack
        if target in packs:
            self.current_pack = target
        elif packs:
            self.current_pack = packs[0]
        else:
            self.current_pack = ""
        self.current_pack_var.set(self.current_pack)
        self.refresh_sets()
        if not packs:
            self.status_var.set("No audio packs in %s - use 'New pack' or 'Clone built-in'." % self.store.root)

    def _on_pack_selected(self):
        self.current_pack = self.current_pack_var.get()
        self._persist(last_pack=self.current_pack)
        self.refresh_sets()

    def _new_pack(self):
        name = SimplePrompt(self, "New pack", "Pack name (folder under assets/audio):").result
        if not name:
            return
        try:
            self.store.ensure_root()
            self.store.create_pack(name)
        except (ValueError, OSError) as e:
            messagebox.showerror(APP_NAME, str(e), parent=self)
            return
        self.refresh_packs(select=packstore.sanitize_pack_name(name))
        self.status_var.set("Created pack %s" % self.current_pack)

    def _clone_default(self):
        if not self.store.default_pack_dir():
            messagebox.showerror(APP_NAME, "Built-in data/audio folder not found.\n"
                                          "Set it in Settings -> built-in data folder.", parent=self)
            return
        name = SimplePrompt(self, "Clone built-in pack",
                            "New pack name (gets a full copy of the built-in sounds):").result
        if not name:
            return
        try:
            n = self.store.clone_default(name)
        except (ValueError, OSError) as e:
            messagebox.showerror(APP_NAME, str(e), parent=self)
            return
        self.refresh_packs(select=packstore.sanitize_pack_name(name))
        self.status_var.set("Cloned %d files into %s" % (n, self.current_pack))

    def _duplicate_pack(self):
        if not self._require_pack():
            return
        name = SimplePrompt(self, "Duplicate pack", "Name for the copy:", initial=self.current_pack + "_copy").result
        if not name:
            return
        try:
            self.store.duplicate_pack(self.current_pack, name)
        except (ValueError, OSError) as e:
            messagebox.showerror(APP_NAME, str(e), parent=self)
            return
        self.refresh_packs(select=packstore.sanitize_pack_name(name))

    def _rename_pack(self):
        if not self._require_pack():
            return
        name = SimplePrompt(self, "Rename pack", "New name:", initial=self.current_pack).result
        if not name:
            return
        try:
            self.store.rename_pack(self.current_pack, name)
        except (ValueError, OSError) as e:
            messagebox.showerror(APP_NAME, str(e), parent=self)
            return
        self.refresh_packs(select=packstore.sanitize_pack_name(name))

    def _delete_pack(self):
        if not self._require_pack():
            return
        if not messagebox.askyesno(APP_NAME, "Delete pack '%s' and all its files?" % self.current_pack, parent=self):
            return
        self.store.delete_pack(self.current_pack)
        self.current_pack = ""
        self.refresh_packs()

    def _open_pack_folder(self):
        if not self._require_pack():
            return
        path = self.store.pack_dir(self.current_pack)
        os.makedirs(path, exist_ok=True)
        self._reveal(path)

    def _reveal(self, path):
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])

    # ---------------- set / slot views ----------------

    def refresh_sets(self):
        self.sets_tree.delete(*self.sets_tree.get_children())
        if not self.current_pack:
            self.summary_var.set("no pack selected")
            self.set_header.config(text="Select a sound set")
            self.set_desc.config(text="")
            self.slots.delete(*self.slots.get_children())
            return
        counts = {}
        for name in soundcatalog.SET_BY_NAME:
            files = soundcatalog.SET_BY_NAME[name]["files"]
            counts[name] = sum(1 for f in files if self.store.has_slot(self.current_pack, f))
        total = soundcatalog.total_slots()
        overrides = sum(counts.values())
        self.summary_var.set("%d / %d samples overridden" % (overrides, total))

        for category in soundcatalog.CATEGORIES:
            members = [s for s in soundcatalog.SETS if s[1] == category]
            if not members:
                continue
            cat_node = self.sets_tree.insert("", "end", text=category, open=True,
                                             values=("cat:" + category,))
            for name, _cat, label, _desc, files in members:
                text = "%s  (%d/%d)" % (label, counts[name], len(files))
                node = self.sets_tree.insert(cat_node, "end", text=text, values=(name,))
                if counts[name] == len(files):
                    self.sets_tree.item(node, tags=("full",))
        self.sets_tree.tag_configure("full", foreground="#1b5e20")

        extras = self.store.extra_files(self.current_pack)
        if extras:
            node = self.sets_tree.insert("", "end", text="Unused files in pack (%d)" % len(extras),
                                         values=("extras:",), tags=("extras",))
            self.sets_tree.tag_configure("extras", foreground="#8a6d3b")
            _ = node

        first = None
        for cat in self.sets_tree.get_children():
            kids = self.sets_tree.get_children(cat)
            if kids:
                first = kids[0]
                break
        if first:
            self.sets_tree.selection_set(first)
            self.sets_tree.focus(first)
            self.current_set = self.sets_tree.item(first, "values")[0]
            self._populate_slots()

    def _on_set_selected(self):
        sel = self.sets_tree.selection()
        if not sel:
            return
        values = self.sets_tree.item(sel[0], "values")
        if not values:
            return
        key = values[0]
        if key.startswith("cat:"):
            self.sets_tree.item(sel[0], open=True)
            return
        self.current_set = key
        self._populate_slots()

    def _populate_slots(self):
        self.slots.delete(*self.slots.get_children())
        if self.current_set == "extras:":
            self.set_header.config(text="Unused files in this pack")
            self.set_desc.config(text="Files that no sound set references. Harmless, but you can delete them.")
            for name in self.store.extra_files(self.current_pack):
                path = self.store.slot_path(self.current_pack, name)
                info = self.store.cached_info(path)
                self.slots.insert("", "end", iid=name,
                                  values=("-", "unreferenced", name, STATE_PACK, self._len(info), self._rate(info),
                                          (info or {}).get("channels", "-"), human_size(os.path.getsize(path))))
            self.info_var.set("")
            self._waveform = None
            self._redraw_waveform()
            return

        meta = soundcatalog.SET_BY_NAME.get(self.current_set)
        if not meta:
            return
        self.set_header.config(text="%s  [%s]" % (meta["label"], meta["name"]))
        self.set_desc.config(text=meta["desc"])
        for i, f in enumerate(meta["files"]):
            kind, path = self.store.slot_source(self.current_pack, f)
            info = self.store.cached_info(path) if path else None
            state = {"pack": STATE_PACK, "default": STATE_DEFAULT, None: STATE_MISSING}[kind]
            tags = ()
            if state == STATE_PACK:
                tags = ("custom",)
            elif state == STATE_MISSING:
                tags = ("missing",)
            self.slots.insert("", "end", iid=f, tags=tags,
                              values=(i + 1, soundcatalog.variant_label(self.current_set, i),
                                      f, state, self._len(info), self._rate(info),
                                      (info or {}).get("channels", "-"),
                                      human_size(os.path.getsize(path)) if path else "-"))
        if meta["files"]:
            first = meta["files"][0]
            self.slots.selection_set(first)
            self._on_slot_selected()

    def _len(self, info):
        if not info or not info.get("sample_rate"):
            return "-"
        return human_time(info["samples"] / float(info["sample_rate"]))

    def _rate(self, info):
        if not info or not info.get("sample_rate"):
            return "-"
        return "%d Hz" % info["sample_rate"]

    def _selected_slots(self):
        return [iid for iid in self.slots.selection() if iid]

    def _on_slot_selected(self):
        sel = self._selected_slots()
        if not sel:
            self.info_var.set("")
            self._waveform = None
            self._envelope = None
            self._redraw_waveform()
            return
        f = sel[0]
        kind, path = self.store.slot_source(self.current_pack, f)
        info = self.store.cached_info(path) if path else None
        if not path:
            self.info_var.set("%s - missing everywhere" % f)
            self._waveform = None
            self._envelope = None
            self._redraw_waveform()
            return
        bits = [os.path.basename(f),
                "from %s" % ("this pack" if kind == "pack" else "built-in default")]
        if info:
            bits.append("%s, %d Hz, %s, %s" % (self._len(info), info["sample_rate"],
                                               "mono" if info["channels"] == 1 else "stereo",
                                               human_size(info["size"])))
        bits.append(os.path.dirname(path))
        self.info_var.set("   |   ".join(bits))
        self._wave_token = getattr(self, "_wave_token", 0) + 1
        token = self._wave_token
        if self._waveform is not None and self._waveform.get("path") == path:
            self._redraw_waveform()
            return

        def job():
            dec = self.store.read_slot_pcm(self.current_pack, f)
            env = build_envelope(dec) if dec else None
            return dec, env

        self.run_bg(job, on_done=lambda res: self._show_waveform(token, path, res),
                    message="Loading waveform...")

    # ---------------- waveform ----------------

    def _show_waveform(self, token, path, result):
        if token != getattr(self, "_wave_token", 0):
            return
        dec, env = result
        if not dec:
            self._waveform = None
            self._envelope = None
            self._redraw_waveform()
            return
        dec["path"] = path
        self._waveform = dec
        self._envelope = env
        self._redraw_waveform()

    def _redraw_waveform(self):
        c = self.wave
        c.delete("all")
        w = max(c.winfo_width(), 10)
        h = max(c.winfo_height(), 10)
        c.create_line(0, h // 2, w, h // 2, fill="#e0e0e0")
        env = getattr(self, "_envelope", None)
        dec = self._waveform
        if not env or not dec:
            c.create_text(w // 2, h // 2, text="no waveform", fill="#aaaaaa")
            return
        scale = w / float(len(env))
        for i, peak in enumerate(env):
            amp = peak * (h / 2 - 2)
            x0 = i * scale
            x1 = max(x0 + 0.8, (i + 1) * scale)
            c.create_line(x0, h / 2 - amp, x0, h / 2 + amp, fill="#2e7d32", width=max(1, int(x1 - x0)))
        sel = self._selected_slots()
        label = sel[0] if sel else ""
        c.create_text(6, 10, anchor="w", fill="#666",
                      text="%s   %s   %d Hz   %s" % (
                          label, human_time(dec["samples"] / float(dec["sample_rate"])),
                          dec["sample_rate"], "mono" if dec["channels"] == 1 else "stereo"))

    # ---------------- slot actions ----------------

    def _import_files(self):
        if not self._require_pack() or not self._require_codec():
            return
        if self.current_set == "extras:":
            messagebox.showinfo(APP_NAME, "Unreferenced files cannot be replaced by import.\n"
                                          "Pick a sound set on the left.", parent=self)
            return
        slots = self._selected_slots()
        if not slots:
            messagebox.showinfo(APP_NAME, "Select the sample(s) you want to replace first.", parent=self)
            return
        if slots == ["extras:"]:
            return
        paths = filedialog.askopenfilenames(
            parent=self, title="Choose audio files",
            filetypes=[("Audio", "*.wav *.wv *.aiff *.aif *.mp3 *.ogg *.flac *.opus *.m4a"),
                       ("WavPack", "*.wv"), ("Wave", "*.wav"), ("All files", "*.*")])
        if not paths:
            return
        paths = list(paths)
        targets = list(slots)
        if len(paths) > len(targets):
            meta = soundcatalog.SET_BY_NAME.get(self.current_set)
            if meta:
                start = meta["files"].index(targets[-1])
                extra = [f for f in meta["files"][start + 1:] if f not in targets]
                targets += extra[: len(paths) - len(targets)]
        if len(targets) < len(paths):
            messagebox.showwarning(APP_NAME, "Only %d sample(s) selected, extra files ignored." % len(targets),
                                   parent=self)
            paths = paths[: len(targets)]

        def job():
            results = []
            for src, dst_file in zip(paths, targets):
                results.append((dst_file, self.store.import_into_slot(src, self.current_pack, dst_file,
                                                                     self.convert_options)))
            return results

        def done(results):
            ok = sum(1 for _f, r in results if r.ok)
            failed = [(f, r.message) for f, r in results if not r.ok]
            self.refresh_sets()
            if failed:
                messagebox.showwarning(APP_NAME, "%d imported, %d failed:\n\n%s" % (
                    ok, len(failed), "\n".join("%s: %s" % (f, m) for f, m in failed[:8])), parent=self)
            self.status_var.set("%d sample(s) imported into %s" % (ok, self.current_pack))

        self.run_bg(job, done, message="Importing %d file(s)..." % len(paths))

    def _restore_slots(self):
        if not self._require_pack():
            return
        slots = self._selected_slots()
        if not slots:
            return
        if self.current_set == "extras:":
            if not messagebox.askyesno(APP_NAME, "Delete %d unused file(s) from the pack?" % len(slots), parent=self):
                return
            for f in slots:
                self.store.delete_slot(self.current_pack, f)
            self.refresh_sets()
            return
        removed = sum(1 for f in slots if self.store.delete_slot(self.current_pack, f))
        self.refresh_sets()
        self.status_var.set("Restored %d sample(s) to the built-in default" % removed)

    def _show_slot_file(self):
        sel = self._selected_slots()
        if not sel:
            return
        kind, path = self.store.slot_source(self.current_pack, sel[0])
        if path:
            self._reveal(path)

    def _open_copy_dialog(self):
        if not self._require_pack():
            return
        CopyFromPackDialog(self)

    # ---------------- playback ----------------

    def _preview_selected(self):
        sel = self._selected_slots()
        if not sel or not self.codec:
            return
        f = sel[0]
        loop = self.loop_var.get()

        def job():
            return self.store.read_slot_pcm(self.current_pack, f)

        def done(dec):
            if not dec:
                messagebox.showerror(APP_NAME, "Could not decode %s" % f, parent=self)
                return
            try:
                play_pcm(dec["pcm"], dec["sample_rate"], dec["channels"], loop)
                self.status_var.set("Playing %s" % f)
            except (RuntimeError, OSError) as e:
                self.status_var.set("Playback failed: %s" % e)

        self.run_bg(job, done, message="Decoding %s..." % f)

    def _stop_playback(self):
        stop_playback()

    # ---------------- pack-level tools ----------------

    def _export_zip(self):
        if not self._require_pack():
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".zip",
                                            initialfile="%s.zip" % self.current_pack,
                                            filetypes=[("Zip", "*.zip")])
        if not path:
            return
        n = self.store.export_zip(self.current_pack, path)
        self.status_var.set("Exported %d file(s) to %s" % (n, path))

    def _import_zip(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("Zip", "*.zip")])
        if not path:
            return
        name = SimplePrompt(self, "Import pack", "Pack name to create:", initial="imported").result
        if not name:
            return
        try:
            n = self.store.import_zip(path, name)
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e), parent=self)
            return
        self.refresh_packs(select=packstore.sanitize_pack_name(name))
        self.status_var.set("Imported %d file(s) into %s" % (n, self.current_pack))

    def _validate_pack(self):
        if not self._require_pack() or not self._require_codec():
            return

        def job():
            return self.store.validate_pack(self.current_pack)

        def done(report):
            bad = [r for r in report if not r["ok"]]
            notes = [r for r in report if r["ok"] and r.get("note")]
            lines = ["Checked %d file(s) in pack '%s'" % (len(report), self.current_pack), ""]
            if bad:
                lines.append("%d problem(s):" % len(bad))
                for r in bad:
                    lines.append("  %s - %s" % (r["file"], r["reason"]))
            else:
                lines.append("All files are valid 16-bit WavPack the engine can load.")
            if notes:
                lines.append("")
                lines.append("Notes:")
                for r in notes[:20]:
                    lines.append("  %s - %s" % (r["file"], r["note"]))
            TextViewer(self, "Validation", "\n".join(lines))

        self.run_bg(job, done, message="Validating pack...")

    def _open_settings(self):
        SettingsDialog(self)

    def _show_help(self):
        TextViewer(self, "How audio packs work", HELP_TEXT)


class SimplePrompt(tk.Toplevel):
    def __init__(self, master, title, label, initial=""):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.result = None
        self.transient(master)
        ttk.Label(self, text=label, padding=(12, 12, 12, 4)).pack(anchor="w")
        self.var = tk.StringVar(value=initial)
        entry = ttk.Entry(self, textvariable=self.var, width=44)
        entry.pack(padx=12, fill="x")
        entry.focus_set()
        entry.select_range(0, "end")
        row = ttk.Frame(self, padding=12)
        row.pack(fill="x")
        ttk.Button(row, text="OK", command=self._ok).pack(side="right")
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="right", padx=(0, 6))
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        master.wait_window(self)

    def _ok(self):
        self.result = self.var.get().strip()
        self.destroy()


class TextViewer(tk.Toplevel):
    def __init__(self, master, title, text):
        super().__init__(master)
        self.title(title)
        self.geometry("760x520")
        frame = ttk.Frame(self, padding=8)
        frame.pack(fill="both", expand=True)
        widget = tk.Text(frame, wrap="word", font=("Consolas", 9))
        scroll = ttk.Scrollbar(frame, orient="vertical", command=widget.yview)
        widget.configure(yscrollcommand=scroll.set)
        widget.insert("1.0", text)
        widget.configure(state="disabled")
        scroll.pack(side="right", fill="y")
        widget.pack(fill="both", expand=True)


class CopyFromPackDialog(tk.Toplevel):
    def __init__(self, app: AudioPackApp):
        super().__init__(app)
        self.app = app
        self.title("Take sounds from another pack")
        self.geometry("940x660")
        self.minsize(760, 520)
        self.transient(app)
        self.store = app.store
        self.source = tk.StringVar()
        self.search = tk.StringVar()
        self.loop_var = tk.BooleanVar(value=False)
        self.current_set = ""
        self._waveform = None
        self._envelope = None
        self._wave_token = 0

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Source pack:").pack(side="left")
        packs = [p for p in self.store.packs() if p != app.current_pack]
        self.combo = ttk.Combobox(top, textvariable=self.source, values=packs, state="readonly", width=30)
        self.combo.pack(side="left", padx=(6, 12))
        if packs:
            self.combo.current(0)
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self._reload())
        ttk.Label(top, text="Filter:").pack(side="left")
        filt = ttk.Entry(top, textvariable=self.search, width=22)
        filt.pack(side="left", padx=(6, 0))
        filt.bind("<KeyRelease>", lambda _e: self._reload())

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10)
        left = ttk.Frame(body)
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_set())
        body.add(left, weight=1)

        right = ttk.Frame(body)
        self.list = ttk.Treeview(right, columns=("file", "length", "rate", "size"),
                                 show="headings", selectmode="extended")
        for col, text, width, anchor in (("file", "file", 250, "w"), ("length", "length", 80, "e"),
                                         ("rate", "rate", 80, "e"), ("size", "size", 80, "e")):
            self.list.heading(col, text=text)
            self.list.column(col, width=width, anchor=anchor, stretch=(col == "file"))
        self.list.pack(fill="both", expand=True)
        self.list.bind("<<TreeviewSelect>>", lambda _e: self._load_waveform())
        self.list.bind("<Double-1>", lambda _e: self._preview())
        self.list.bind("<Return>", lambda _e: self._preview())
        self.list.bind("<space>", lambda _e: self._preview())

        self.wave = tk.Canvas(right, height=96, background="#ffffff", highlightthickness=1,
                              highlightbackground="#c8c8c8")
        self.wave.pack(fill="x", pady=(6, 2))
        self.wave.bind("<Configure>", lambda _e: self.after_idle(self._redraw_waveform))
        body.add(right, weight=2)

        bottom = ttk.Frame(self, padding=10)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Preview", command=self._preview).pack(side="left")
        ttk.Button(bottom, text="Stop", command=stop_playback).pack(side="left", padx=(4, 0))
        ttk.Checkbutton(bottom, text="loop", variable=self.loop_var).pack(side="left", padx=(6, 12))
        ttk.Button(bottom, text="Copy selected sample(s)", command=self._copy_selected).pack(side="left")
        ttk.Button(bottom, text="Copy whole set", command=self._copy_set).pack(side="left", padx=(6, 0))
        ttk.Button(bottom, text="Copy every sound in pack", command=self._copy_all).pack(side="left", padx=(6, 0))
        ttk.Button(bottom, text="Close", command=self._close).pack(side="right")
        self.status = ttk.Label(bottom, text="", foreground="#555")
        self.status.pack(side="left", padx=12)

        self.protocol("WM_DELETE_WINDOW", self._close)
        self._reload()
        self.grab_set()

    def _close(self):
        stop_playback()
        self.destroy()

    def _reload(self):
        self.tree.delete(*self.tree.get_children())
        src = self.source.get()
        needle = self.search.get().lower()
        if not src:
            self.status.config(text="No other pack available - create another pack to copy from")
            return
        found = 0
        for category in soundcatalog.CATEGORIES:
            cat_node = None
            for name, cat, label, _desc, files in soundcatalog.SETS:
                if cat != category:
                    continue
                present = [f for f in files if self.store.has_slot(src, f)]
                if not present:
                    continue
                if needle and needle not in label.lower() and needle not in name.lower() \
                        and not any(needle in f.lower() for f in present):
                    continue
                if cat_node is None:
                    cat_node = self.tree.insert("", "end", text=category, open=True, values=("cat:" + category,))
                self.tree.insert(cat_node, "end", text="%s  (%d)" % (label, len(present)), values=(name,))
                found += 1
        self.list.delete(*self.list.get_children())
        self.current_set = ""
        self._clear_waveform()
        if found:
            self.status.config(text="Source: %s - %d set(s) with overrides" % (src, found))
        else:
            self.status.config(text="Source: %s - nothing overridden in that pack" % src)

    def _on_set(self):
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        if not values or values[0].startswith("cat:"):
            return
        self.current_set = values[0]
        self._fill_list()

    def _fill_list(self):
        self.list.delete(*self.list.get_children())
        self._clear_waveform()
        src = self.source.get()
        meta = soundcatalog.SET_BY_NAME.get(self.current_set)
        if not meta:
            return
        for f in meta["files"]:
            if not self.store.has_slot(src, f):
                continue
            info = self.store.cached_info(self.store.slot_path(src, f))
            self.list.insert("", "end", iid=f, values=(
                f, self.app._len(info), self.app._rate(info), human_size((info or {}).get("size", 0))))
        if meta["files"]:
            first = self.list.get_children()
            if first:
                self.list.selection_set(first[0])

    # ---------------- preview ----------------

    def _clear_waveform(self):
        self._wave_token += 1
        self._waveform = None
        self._envelope = None
        self._redraw_waveform()

    def _load_waveform(self):
        sel = self.list.selection()
        src = self.source.get()
        if not sel or not src or not self.store.codec:
            self._clear_waveform()
            return
        f = sel[0]
        path = self.store.slot_path(src, f)
        self._wave_token += 1
        token = self._wave_token
        if self._waveform is not None and self._waveform.get("path") == path:
            self._redraw_waveform()
            return

        def job():
            try:
                dec = self.store.codec.decode_file(path)
            except WavpackError:
                return None, None
            return dec, build_envelope(dec)

        def done(result):
            if token != self._wave_token:
                return
            dec, env = result
            if not dec:
                self._waveform = None
                self._envelope = None
            else:
                dec["path"] = path
                self._waveform = dec
                self._envelope = env
            self._redraw_waveform()

        self.app.run_bg(job, done, message="Loading waveform...")

    def _preview(self):
        sel = self.list.selection()
        src = self.source.get()
        if not sel or not src:
            return
        if not self.store.codec:
            self.status.config(text="libwavpack not loaded, cannot decode")
            return
        f = sel[0]
        path = self.store.slot_path(src, f)
        loop = self.loop_var.get()
        self.status.config(text="Decoding %s..." % f)

        def job():
            return self.store.codec.decode_file(path)

        def done(dec):
            if not self._alive():
                return
            try:
                play_pcm(dec["pcm"], dec["sample_rate"], dec["channels"], loop)
                self.status.config(text="Playing %s from %s" % (f, src))
            except (RuntimeError, OSError) as e:
                self.status.config(text="Playback failed: %s" % e)

        self.app.run_bg(job, done, message="Decoding %s..." % f)

    def _alive(self):
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    def _redraw_waveform(self):
        if not self._alive():
            return
        c = self.wave
        c.delete("all")
        w = max(c.winfo_width(), 10)
        h = max(c.winfo_height(), 10)
        c.create_line(0, h // 2, w, h // 2, fill="#e0e0e0")
        env = self._envelope
        dec = self._waveform
        if not env or not dec:
            c.create_text(w // 2, h // 2, text="select a sample to see its waveform", fill="#aaaaaa")
            return
        scale = w / float(len(env))
        for i, peak in enumerate(env):
            amp = peak * (h / 2 - 2)
            x0 = i * scale
            c.create_line(x0, h / 2 - amp, x0, h / 2 + amp, fill="#2e7d32",
                          width=max(1, int(max(0.8, scale))))
        sel = self.list.selection()
        c.create_text(6, 10, anchor="w", fill="#666",
                      text="%s   %s   %d Hz   %s" % (
                          sel[0] if sel else "", human_time(dec["samples"] / float(dec["sample_rate"])),
                          dec["sample_rate"], "mono" if dec["channels"] == 1 else "stereo"))

    def _copy_selected(self):
        src = self.source.get()
        files = list(self.list.selection())
        if not src or not files:
            return
        n = 0
        for f in files:
            if self.store.copy_slot(src, f, self.app.current_pack):
                n += 1
        self.app.refresh_sets()
        self.status.config(text="Copied %d sample(s) from %s" % (n, src))

    def _copy_set(self):
        src = self.source.get()
        if not src or not self.current_set:
            return
        files = soundcatalog.SET_BY_NAME[self.current_set]["files"]
        n = sum(1 for f in files if self.store.copy_slot(src, f, self.app.current_pack))
        self.app.refresh_sets()
        self.status.config(text="Copied whole set (%d sample(s)) from %s" % (n, src))

    def _copy_all(self):
        src = self.source.get()
        if not src:
            return
        if not messagebox.askyesno(APP_NAME,
                                   "Copy every overridden sound of '%s' into '%s'?" % (src, self.app.current_pack),
                                   parent=self):
            return
        n = 0
        for f in soundcatalog.ALL_FILES:
            if self.store.has_slot(src, f) and self.store.copy_slot(src, f, self.app.current_pack):
                n += 1
        self.app.refresh_sets()
        self.status.config(text="Copied %d sample(s) from %s" % (n, src))


class SettingsDialog(tk.Toplevel):
    def __init__(self, app: AudioPackApp):
        super().__init__(app)
        self.app = app
        self.title("Settings")
        self.geometry("700x460")
        self.transient(app)
        s = app.settings

        self.dll_var = tk.StringVar(value=s.get("wavpack_dll") or (app.codec.dll_path if app.codec else ""))
        self.data_var = tk.StringVar(value=s.get("data_audio") or (app.store.default_pack_dir() or ""))
        self.ffmpeg_var = tk.StringVar(value=s.get("ffmpeg") or (packstore.find_ffmpeg() or ""))
        self.rate_var = tk.StringVar(value=str(s.get("target_rate", 0) or 0))
        self.gain_var = tk.StringVar(value=str(s.get("gain_db", 0) or 0))
        self.fade_var = tk.StringVar(value=str(s.get("fade_ms", 0) or 0))
        self.norm_var = tk.BooleanVar(value=bool(s.get("normalize", False)))
        self.trim_var = tk.BooleanVar(value=bool(s.get("trim_silence", False)))
        self.mono_var = tk.BooleanVar(value=bool(s.get("force_mono", False)))
        self.fast_var = tk.BooleanVar(value=bool(s.get("fast_encode", True)))

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(frm, text="WavPack DLL").grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=self.dll_var).grid(row=row, column=1, sticky="ew", padx=6)
        ttk.Button(frm, text="Browse...", command=lambda: self._pick(self.dll_var, [("DLL", "*.dll")])
                   ).grid(row=row, column=2)
        row += 1
        ttk.Label(frm, text="Built-in data folder").grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=self.data_var).grid(row=row, column=1, sticky="ew", padx=6)
        ttk.Button(frm, text="Browse...", command=lambda: self._pick_dir(self.data_var)).grid(row=row, column=2)
        row += 1
        ttk.Label(frm, text="ffmpeg (optional)").grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=self.ffmpeg_var).grid(row=row, column=1, sticky="ew", padx=6)
        ttk.Button(frm, text="Browse...", command=lambda: self._pick(self.ffmpeg_var, [("ffmpeg", "ffmpeg.exe")])
                   ).grid(row=row, column=2)
        row += 1

        ttk.Separator(frm).grid(row=row, column=0, columnspan=3, sticky="ew", pady=10)
        row += 1
        ttk.Label(frm, text="Import defaults", font=("Segoe UI", 9, "bold")).grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1
        ttk.Label(frm, text="Resample to (0 = keep)").grid(row=row, column=0, sticky="w", pady=3)
        ttk.Combobox(frm, textvariable=self.rate_var, values=("0", "44100", "48000", "22050"),
                     state="readonly", width=12).grid(row=row, column=1, sticky="w", padx=6)
        row += 1
        ttk.Label(frm, text="Gain (dB)").grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=self.gain_var, width=12).grid(row=row, column=1, sticky="w", padx=6)
        row += 1
        ttk.Label(frm, text="Fade edges (ms)").grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=self.fade_var, width=12).grid(row=row, column=1, sticky="w", padx=6)
        row += 1
        ttk.Checkbutton(frm, text="Peak normalize to -0.2 dB", variable=self.norm_var).grid(
            row=row, column=1, sticky="w", padx=6, pady=2)
        row += 1
        ttk.Checkbutton(frm, text="Trim leading/trailing silence", variable=self.trim_var).grid(
            row=row, column=1, sticky="w", padx=6, pady=2)
        row += 1
        ttk.Checkbutton(frm, text="Force mono output", variable=self.mono_var).grid(
            row=row, column=1, sticky="w", padx=6, pady=2)
        row += 1
        ttk.Checkbutton(frm, text="Fast encoding", variable=self.fast_var).grid(
            row=row, column=1, sticky="w", padx=6, pady=2)
        row += 1

        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Save", command=self._save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(0, 6))
        self.grab_set()

    def _pick(self, var, types):
        p = filedialog.askopenfilename(parent=self, filetypes=types)
        if p:
            var.set(p)

    def _pick_dir(self, var):
        p = filedialog.askdirectory(parent=self)
        if p:
            var.set(p)

    def _save(self):
        try:
            rate = int(self.rate_var.get() or 0)
            gain = float(self.gain_var.get() or 0)
            fade = float(self.fade_var.get() or 0)
        except ValueError:
            messagebox.showerror(APP_NAME, "Numbers please.", parent=self)
            return
        self.app.settings.update({
            "wavpack_dll": self.dll_var.get().strip(),
            "data_audio": self.data_var.get().strip(),
            "ffmpeg": self.ffmpeg_var.get().strip(),
            "target_rate": rate, "gain_db": gain, "fade_ms": fade,
            "normalize": self.norm_var.get(), "trim_silence": self.trim_var.get(),
            "force_mono": self.mono_var.get(), "fast_encode": self.fast_var.get(),
        })
        packstore.save_settings(self.app.settings)
        self.app.convert_options = self.app._options_from_settings()
        self.app._load_codec()
        self.app.store.codec = self.app.codec
        self.app._refresh_codec_state()
        self.app.refresh_sets()
        self.destroy()


HELP_TEXT = """Audio packs in this client
=========================

The engine resolves every sound through the sound set table generated from
datasrc/content.py (loaded by src/game/client/components/sounds.cpp). For each
sample it first looks for

    <config>/assets/audio/<pack>/<filename>.wv

and falls back to the built-in data/audio copy when the file is absent.

<config> is %APPDATA%\\DDNet on Windows. The pack is selected in game under
Settings -> Assets -> Audio (cl_config_asset_audio).

Requirements the engine enforces (src/engine/client/sound.cpp):
  * WavPack (.wv) container
  * 16 bits per sample
  * mono or stereo (max 2 channels)

Anything else is rejected at load time, which is why this tool validates every
file it writes.

Editing model
-------------
The pack folder is the project: a file present in it overrides that one sample,
deleting the file restores the built-in sound. There is no extra project file,
so a pack can be copied, zipped or shared as-is.

Per-sample control
------------------
* Sound sets are listed on the left; each set expands into its individual
  variants on the right (e.g. the 12 pain grunts, the 8 footstep samples).
* Import file(s) - converts wav/aiff (built in) or mp3/ogg/flac (needs ffmpeg)
  into 16-bit WavPack, or copies a .wv unchanged.
* Take from pack - copy a single sample, a whole set, or every overridden sound
  from another pack. The dialog has its own Preview / Stop buttons, a waveform
  and a loop toggle, so you can audition a donor pack before copying from it.
* Restore default - delete the override so the built-in sound plays again.
* Double-click a sample to preview it; the waveform is drawn underneath.

Unused files
------------
The built-in data folder also ships a few legacy .wv files that no sound set
references (ninja/sledge voices, strong hit). They are listed under "Unused
files in pack" so you can spot or clean them; the engine never plays them.
"""


def main():
    cleanup_preview_files()
    app = AudioPackApp()
    app.mainloop()


if __name__ == "__main__":
    main()
