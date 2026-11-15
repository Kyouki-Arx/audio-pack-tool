"""Pack discovery and file operations for DDNet / TClient audio asset packs.

A pack is a directory under <config>/assets/audio/ holding flat *.wv files.
Every sound the engine can play is listed in soundcatalog.py; a file that is
missing from the pack falls back to the client's built-in data/audio copy.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field

import soundcatalog
from wavpack_codec import WavpackCodec, WavpackError, load_wav_any, pcm_to_pcm16

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))


def _settings_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return TOOL_DIR


SETTINGS_PATH = os.path.join(_settings_dir(), "settings.json")
COMPRESSED_EXT = (".mp3", ".ogg", ".opus", ".flac", ".m4a", ".aac", ".wma")


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def guess_pack_root() -> str:
    settings = load_settings()
    if settings.get("pack_root") and os.path.isdir(settings["pack_root"]):
        return settings["pack_root"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        for name in ("DDNet", "Teeworlds", "TClient"):
            p = os.path.join(appdata, name, "assets", "audio")
            if os.path.isdir(p):
                return p
        return os.path.join(appdata, "DDNet", "assets", "audio")
    return os.path.join(os.path.expanduser("~"), "assets", "audio")


def list_packs(root: str, include_builtin: bool = False):
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root), key=str.lower):
        p = os.path.join(root, name)
        if not os.path.isdir(p) or name.startswith("."):
            continue
        if not include_builtin and name == "default":
            continue
        out.append(name)
    return out


def find_ffmpeg() -> str | None:
    settings = load_settings()
    hint = settings.get("ffmpeg")
    if hint and os.path.isfile(hint):
        return hint
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    local = os.environ.get("LOCALAPPDATA", "")
    for base in (os.path.join(local, "Microsoft", "WinGet", "Links"),
                 os.path.join(local, "Microsoft", "WinGet", "Packages"),
                 r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin"):
        if not base or not os.path.isdir(base):
            continue
        cand = os.path.join(base, "ffmpeg.exe")
        if os.path.isfile(cand):
            return cand
        if base.endswith("Packages"):
            for dirpath, _dirs, files in os.walk(base):
                if "ffmpeg.exe" in files:
                    return os.path.join(dirpath, "ffmpeg.exe")
    return None


@dataclass
class ConvertOptions:
    target_rate: int = 0
    normalize: bool = False
    gain_db: float = 0.0
    trim_silence: bool = False
    fade_ms: float = 0.0
    force_mono: bool = False
    fast: bool = True


@dataclass
class ImportResult:
    ok: bool
    message: str
    path: str = ""
    info: dict = field(default_factory=dict)


class PackStore:
    def __init__(self, codec: WavpackCodec | None = None, root: str | None = None):
        self.codec = codec
        self.root = root or guess_pack_root()
        self._cache = {}

    # ---------- discovery ----------

    def packs(self):
        return list_packs(self.root)

    def pack_dir(self, name: str) -> str:
        return os.path.join(self.root, name)

    def default_pack_dir(self):
        for name in ("default",):
            p = os.path.join(self.root, name)
            if os.path.isdir(p):
                return p
        settings = load_settings()
        if settings.get("data_audio") and os.path.isdir(settings["data_audio"]):
            return settings["data_audio"]
        return None

    def ensure_root(self) -> bool:
        try:
            os.makedirs(self.root, exist_ok=True)
            return True
        except OSError:
            return False

    # ---------- slot inspection ----------

    def slot_path(self, pack: str, filename: str) -> str:
        return os.path.join(self.root, pack, filename)

    def has_slot(self, pack: str, filename: str) -> bool:
        return os.path.isfile(self.slot_path(pack, filename))

    def slot_source(self, pack: str, filename: str):
        """Returns ('pack'|'default'|None, path)."""
        p = self.slot_path(pack, filename)
        if os.path.isfile(p):
            return "pack", p
        d = self.default_pack_dir()
        if d:
            dp = os.path.join(d, filename)
            if os.path.isfile(dp):
                return "default", dp
        return None, ""

    def cached_info(self, path: str):
        try:
            stamp = (os.path.getsize(path), os.path.getmtime(path))
        except OSError:
            return None
        hit = self._cache.get(path)
        if hit and hit[0] == stamp:
            return hit[1]
        info = None
        if self.codec:
            try:
                info = self.codec.info(path)
            except OSError:
                info = None
        self._cache[path] = (stamp, info)
        return info

    def override_count(self, pack: str) -> int:
        return sum(1 for f in soundcatalog.ALL_FILES if self.has_slot(pack, f))

    def extra_files(self, pack: str):
        d = self.pack_dir(pack)
        if not os.path.isdir(d):
            return []
        known = set(soundcatalog.ALL_FILES)
        out = []
        for name in sorted(os.listdir(d), key=str.lower):
            if os.path.isfile(os.path.join(d, name)) and name.lower().endswith(".wv") and name not in known:
                out.append(name)
        return out

    # ---------- pack management ----------

    def create_pack(self, name: str) -> str:
        name = sanitize_pack_name(name)
        if not name:
            raise ValueError("pack name is empty")
        d = self.pack_dir(name)
        if os.path.isdir(d):
            raise ValueError("a pack with that name already exists")
        os.makedirs(d)
        return d

    def delete_pack(self, name: str) -> None:
        d = self.pack_dir(name)
        if os.path.isdir(d):
            shutil.rmtree(d)

    def rename_pack(self, old: str, new: str) -> str:
        new = sanitize_pack_name(new)
        if not new:
            raise ValueError("pack name is empty")
        src, dst = self.pack_dir(old), self.pack_dir(new)
        if os.path.exists(dst):
            raise ValueError("target name already exists")
        os.rename(src, dst)
        return dst

    def duplicate_pack(self, src: str, new: str) -> str:
        new = sanitize_pack_name(new)
        dst = self.pack_dir(new)
        if os.path.exists(dst):
            raise ValueError("target name already exists")
        shutil.copytree(self.pack_dir(src), dst)
        return dst

    def clone_default(self, name: str, overwrite: bool = False) -> int:
        d = self.default_pack_dir()
        if not d:
            raise ValueError("built-in data/audio folder not found")
        dst = self.pack_dir(sanitize_pack_name(name))
        os.makedirs(dst, exist_ok=True)
        n = 0
        for f in sorted(os.listdir(d)):
            src = os.path.join(d, f)
            if os.path.isfile(src) and f.lower().endswith(".wv"):
                shutil.copy2(src, os.path.join(dst, f))
                n += 1
        return n

    # ---------- slot operations ----------

    def copy_slot(self, src_pack: str, filename: str, dst_pack: str) -> bool:
        src = self.slot_path(src_pack, filename)
        if not os.path.isfile(src):
            src = os.path.join(self.pack_dir(src_pack), filename)
        if not os.path.isfile(src):
            return False
        dst = self.slot_path(dst_pack, filename)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        self._cache.pop(dst, None)
        return True

    def copy_slot_from_path(self, src_path: str, filename: str, dst_pack: str) -> bool:
        if not os.path.isfile(src_path):
            return False
        dst = self.slot_path(dst_pack, filename)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src_path, dst)
        self._cache.pop(dst, None)
        return True

    def delete_slot(self, pack: str, filename: str) -> bool:
        p = self.slot_path(pack, filename)
        if os.path.isfile(p):
            os.remove(p)
            self._cache.pop(p, None)
            return True
        return False

    def read_slot_pcm(self, pack: str, filename: str):
        kind, path = self.slot_source(pack, filename)
        if not path or not self.codec:
            return None
        try:
            return self.codec.decode_file(path)
        except WavpackError:
            return None

    # ---------- import ----------

    def import_into_slot(self, src_path: str, dst_pack: str, filename: str,
                         options: ConvertOptions | None = None) -> ImportResult:
        options = options or ConvertOptions()
        dst = self.slot_path(dst_pack, filename)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        ext = os.path.splitext(src_path)[1].lower()
        try:
            if ext == ".wv":
                if not self.codec:
                    return ImportResult(False, "libwavpack not loaded, cannot verify file", "")
                info = self.codec.info(src_path)
                if not info:
                    return ImportResult(False, "not a WavPack file", "")
                if info["float"] or info["channels"] > 2 or info["samples"] <= 0:
                    return ImportResult(False, "incompatible WavPack (float / >2ch / empty)", "")
                dec = self.codec.decode_file(src_path)
                if dec["bits"] != 16:
                    return ImportResult(False, "%d-bit WavPack, the engine needs 16-bit" % dec["bits"], "")
                shutil.copy2(src_path, dst)
                self._cache.pop(dst, None)
                note = "copied WavPack" + (" (hybrid/lossy)" if info["hybrid"] else "")
                return ImportResult(True, note, dst, info)
            pcm, rate, channels = self._decode_source(src_path)
            pcm, rate, channels = self._process(pcm, rate, channels, options)
            if not self.codec:
                return ImportResult(False, "libwavpack not loaded, cannot encode", "")
            self.codec.encode_file(pcm, rate, channels, dst, fast=options.fast)
            info = self.codec.info(dst)
            return ImportResult(True, "encoded", dst, info or {})
        except (WavpackError, OSError, ValueError) as e:
            return ImportResult(False, str(e), "")

    def _decode_source(self, path: str):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".wv":
            dec = self.codec.decode_file(path)
            return dec["pcm"], dec["sample_rate"], dec["channels"]
        if ext in (".wav", ".wave", ".aif", ".aiff", ".aifc"):
            return load_wav_any(path)
        if ext in COMPRESSED_EXT:
            return self._decode_with_ffmpeg(path)
        raise WavpackError("unsupported file type: %s" % (ext or "unknown"))

    def _decode_with_ffmpeg(self, path: str):
        ff = find_ffmpeg()
        if not ff:
            raise WavpackError(
                "decoding %s needs ffmpeg (set the path in Settings)" % os.path.splitext(path)[1])
        tmp = os.path.join(tempfile.gettempdir(), "apt_decode.wav")
        cmd = [ff, "-y", "-v", "error", "-i", path, "-ac", "2", "-ar", "44100",
               "-c:a", "pcm_s16le", tmp]
        proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode != 0:
            raise WavpackError("ffmpeg failed: %s" % (proc.stderr.strip()[:200] or "unknown error"))
        try:
            pcm, rate, channels = load_wav_any(tmp)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return pcm, rate, channels

    def _process(self, pcm: bytes, rate: int, channels: int, opt: ConvertOptions):
        if channels > 2:
            pcm = _downmix_to_stereo(pcm, channels)
            channels = 2
        if opt.force_mono and channels == 2:
            pcm = _to_mono(pcm)
            channels = 1
        if opt.target_rate and rate != opt.target_rate:
            pcm = _resample(pcm, rate, opt.target_rate, channels)
            rate = opt.target_rate
        if opt.trim_silence:
            pcm = _trim_silence(pcm, channels)
        if opt.gain_db:
            pcm = _apply_gain(pcm, 10 ** (opt.gain_db / 20.0))
        if opt.normalize:
            pcm = _normalize(pcm, 0.98)
        if opt.fade_ms > 0:
            pcm = _fade(pcm, rate, channels, opt.fade_ms)
        return pcm, rate, channels

    # ---------- validation ----------

    def validate_pack(self, pack: str):
        report = []
        for f in soundcatalog.ALL_FILES:
            p = self.slot_path(pack, f)
            if not os.path.isfile(p):
                continue
            entry = {"file": f, "ok": True, "reason": "", "note": ""}
            if not self.codec:
                entry.update(ok=False, reason="libwavpack not loaded")
                report.append(entry)
                continue
            info = self.codec.info(p)
            if not info:
                entry.update(ok=False, reason="not a WavPack file")
            elif info["float"]:
                entry.update(ok=False, reason="32-bit float WavPack, engine needs 16-bit")
            elif info["channels"] > 2:
                entry.update(ok=False, reason="more than 2 channels")
            elif info["samples"] <= 0:
                entry.update(ok=False, reason="no samples")
            else:
                if info["hybrid"]:
                    entry["note"] = "hybrid/lossy WavPack (plays, slightly lossy)"
                try:
                    dec = self.codec.decode_file(p)
                    if dec["bits"] != 16:
                        entry.update(ok=False, reason="%d-bit, engine needs 16-bit" % dec["bits"])
                except WavpackError as e:
                    entry.update(ok=False, reason=str(e))
            entry["info"] = info
            report.append(entry)
        return report

    # ---------- sharing ----------

    def export_zip(self, pack: str, zip_path: str) -> int:
        n = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            d = self.pack_dir(pack)
            for name in sorted(os.listdir(d)):
                p = os.path.join(d, name)
                if os.path.isfile(p):
                    z.write(p, name)
                    n += 1
        return n

    def import_zip(self, zip_path: str, pack: str) -> int:
        d = self.pack_dir(sanitize_pack_name(pack))
        os.makedirs(d, exist_ok=True)
        n = 0
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                name = os.path.basename(info.filename)
                if not name.lower().endswith(".wv"):
                    continue
                with z.open(info) as src, open(os.path.join(d, name), "wb") as dst:
                    shutil.copyfileobj(src, dst)
                n += 1
        return n


def sanitize_pack_name(name: str) -> str:
    name = (name or "").strip()
    bad = '<>:"/\\|?*'
    name = "".join(c for c in name if c not in bad and ord(c) >= 32)
    return name.rstrip(". ")


# ---------- dsp helpers (pure python, 16-bit interleaved) ----------

def _downmix_to_stereo(pcm: bytes, channels: int) -> bytes:
    n = len(pcm) // 2 // channels
    vals = struct.unpack("<%dh" % (n * channels), pcm[: n * channels * 2])
    out = bytearray(n * 4)
    for i in range(n):
        base = i * channels
        left = sum(vals[base + c] for c in range(0, channels, 2)) // ((channels + 1) // 2)
        right = sum(vals[base + c] for c in range(1, channels, 2)) // (channels // 2) if channels > 1 else left
        struct.pack_into("<hh", out, i * 4, left, right)
    return bytes(out)


def _to_mono(pcm: bytes) -> bytes:
    n = len(pcm) // 4
    vals = struct.unpack("<%dh" % (n * 2), pcm[: n * 4])
    out = bytearray(n * 2)
    for i in range(n):
        struct.pack_into("<h", out, i * 2, (vals[i * 2] + vals[i * 2 + 1]) // 2)
    return bytes(out)


def _resample(pcm: bytes, rate: int, target: int, channels: int) -> bytes:
    frames = len(pcm) // (2 * channels)
    if frames <= 1:
        return pcm
    vals = struct.unpack("<%dh" % (frames * channels), pcm[: frames * channels * 2])
    out_frames = max(1, int(round(frames * target / float(rate))))
    ratio = rate / float(target)
    out = bytearray(out_frames * channels * 2)
    for i in range(out_frames):
        pos = i * ratio
        i0 = int(pos)
        if i0 >= frames - 1:
            i0 = frames - 2
        frac = pos - i0
        for c in range(channels):
            a = vals[i0 * channels + c]
            b = vals[(i0 + 1) * channels + c]
            v = int(round(a + (b - a) * frac))
            struct.pack_into("<h", out, (i * channels + c) * 2, max(-32768, min(32767, v)))
    return bytes(out)


def _trim_silence(pcm: bytes, channels: int, threshold: int = 96) -> bytes:
    frames = len(pcm) // (2 * channels)
    if frames <= 2:
        return pcm
    vals = struct.unpack("<%dh" % (frames * channels), pcm[: frames * channels * 2])
    start = 0
    while start < frames and all(abs(vals[start * channels + c]) < threshold for c in range(channels)):
        start += 1
    end = frames
    while end > start and all(abs(vals[(end - 1) * channels + c]) < threshold for c in range(channels)):
        end -= 1
    if end <= start:
        return pcm
    chunk = vals[start * channels: end * channels]
    return struct.pack("<%dh" % len(chunk), *chunk)


def _apply_gain(pcm: bytes, factor: float) -> bytes:
    n = len(pcm) // 2
    vals = struct.unpack("<%dh" % n, pcm[: n * 2])
    out = bytearray(n * 2)
    struct.pack_into("<%dh" % n, out, 0,
                     *[max(-32768, min(32767, int(v * factor))) for v in vals])
    return bytes(out)


def _normalize(pcm: bytes, target: float) -> bytes:
    n = len(pcm) // 2
    vals = struct.unpack("<%dh" % n, pcm[: n * 2])
    peak = max((abs(v) for v in vals), default=0)
    if peak == 0:
        return pcm
    return _apply_gain(pcm, (target * 32767.0) / peak)


def _fade(pcm: bytes, rate: int, channels: int, ms: float) -> bytes:
    frames = len(pcm) // (2 * channels)
    fade = min(int(rate * ms / 1000.0), frames // 2)
    if fade <= 0:
        return pcm
    vals = list(struct.unpack("<%dh" % (frames * channels), pcm[: frames * channels * 2]))
    for i in range(fade):
        g = i / float(fade)
        for c in range(channels):
            vals[i * channels + c] = int(vals[i * channels + c] * g)
            j = (frames - 1 - i) * channels + c
            vals[j] = int(vals[j] * g)
    return struct.pack("<%dh" % len(vals), *vals)


def write_wav(path: str, pcm: bytes, rate: int, channels: int) -> None:
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
