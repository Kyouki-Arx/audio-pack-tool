"""ctypes bindings for libwavpack (encode + decode), used by the pack tool.

The DDNet/TClient engine decodes pack sounds with WavPack (see
src/engine/client/sound.cpp: DecodeWV) and requires 16-bit mono or stereo.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys

SAMPLE_RATES = (6000, 8000, 9600, 11025, 12000, 16000, 22050, 24000,
                32000, 44100, 48000, 64000, 88200, 96000, 192000, 0)

QMODE_NO_STORE_WRAPPER = 0x200
QMODE_IGNORE_LENGTH = 0x800
CONFIG_FAST_FLAG = 0x200
CONFIG_VERY_HIGH_FLAG = 0x800
CONFIG_JOINT_STEREO = 0x10
CONFIG_HYBRID_FLAG = 8


class WavpackError(Exception):
    pass


class WavpackConfig(ctypes.Structure):
    _fields_ = [
        ("bitrate", ctypes.c_float),
        ("shaping_weight", ctypes.c_float),
        ("bits_per_sample", ctypes.c_int),
        ("bytes_per_sample", ctypes.c_int),
        ("qmode", ctypes.c_int),
        ("flags", ctypes.c_int),
        ("xmode", ctypes.c_int),
        ("num_channels", ctypes.c_int),
        ("float_norm_exp", ctypes.c_int),
        ("block_samples", ctypes.c_int32),
        ("worker_threads", ctypes.c_int32),
        ("sample_rate", ctypes.c_int32),
        ("channel_mask", ctypes.c_int32),
        ("md5_checksum", ctypes.c_ubyte * 16),
        ("md5_read", ctypes.c_ubyte),
        ("num_tag_strings", ctypes.c_int),
        ("tag_strings", ctypes.POINTER(ctypes.c_char_p)),
    ]


BLOCK_OUTPUT = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int32)


class StreamReader64(ctypes.Structure):
    _fields_ = [
        ("read_bytes", ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int32)),
        ("write_bytes", ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int32)),
        ("get_pos", ctypes.CFUNCTYPE(ctypes.c_int64, ctypes.c_void_p)),
        ("set_pos_abs", ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int64)),
        ("set_pos_rel", ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int64, ctypes.c_int)),
        ("push_back_byte", ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int)),
        ("get_length", ctypes.CFUNCTYPE(ctypes.c_int64, ctypes.c_void_p)),
        ("can_seek", ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)),
        ("truncate_here", ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)),
        ("close", ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)),
    ]


DLL_CANDIDATE_NAMES = ("libwavpack-1.dll", "wavpack.dll", "libwavpack.dll")
TOOL_DIR = os.path.dirname(os.path.abspath(__file__))


def _candidate_dirs():
    env = os.environ.get("TC_WAVPACK_DIR")
    if env:
        yield env
    if getattr(sys, "frozen", False):
        yield os.path.dirname(os.path.abspath(sys.executable))
    yield TOOL_DIR
    yield os.path.join(TOOL_DIR, "bin")
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    yield exe_dir
    for var in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "APPDATA"):
        base = os.environ.get(var)
        if not base:
            continue
        for sub in ("DDNet", "TClient", "Programs\\DDNet", "Programs\\TClient"):
            yield os.path.join(base, sub)
    yield os.path.join(os.path.dirname(TOOL_DIR), "TClient", "build")


def _bounded_search(roots, max_depth=5):
    seen = set()
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        root = os.path.abspath(root)
        if root in seen:
            continue
        seen.add(root)
        base_depth = root.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.count(os.sep) - base_depth >= max_depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d.lower() not in
                           ("windows", "winsxs", "system32", "node_modules", ".git",
                            "appdata", "installer", "winsxs", "$recycle.bin")]
            for name in filenames:
                if name.lower() in DLL_CANDIDATE_NAMES:
                    yield os.path.join(dirpath, name)


def find_dll(extra_hint=None):
    if extra_hint and os.path.isfile(extra_hint):
        return extra_hint
    for d in _candidate_dirs():
        for name in DLL_CANDIDATE_NAMES:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
    if os.name == "nt":
        home = os.path.expanduser("~")
        roots = [os.path.join(home, "Desktop"), os.path.join(home, "Downloads"),
                 os.path.join(home, "Documents")]
        for p in _bounded_search(roots, max_depth=4):
            return p
    return None


class WavpackCodec:
    def __init__(self, dll_path=None):
        path = find_dll(dll_path)
        if not path:
            raise WavpackError(
                "libwavpack-1.dll not found. Point the tool at the DLL shipped "
                "next to DDNet.exe / TClient.exe (Settings -> WavPack DLL).")
        self.dll_path = path
        dll_dir = os.path.dirname(path)
        if os.name == "nt" and hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(dll_dir)
            except OSError:
                pass
        self.lib = ctypes.CDLL(path)
        self._bind()

    def _bind(self):
        L = self.lib
        L.WavpackGetLibraryVersion.restype = ctypes.c_uint32
        L.WavpackGetLibraryVersionString.restype = ctypes.c_char_p
        L.WavpackGetErrorMessage.argtypes = [ctypes.c_void_p]
        L.WavpackGetErrorMessage.restype = ctypes.c_char_p
        L.WavpackCloseFile.argtypes = [ctypes.c_void_p]

        L.WavpackOpenFileOutput.argtypes = [BLOCK_OUTPUT, ctypes.c_void_p, ctypes.c_void_p]
        L.WavpackOpenFileOutput.restype = ctypes.c_void_p
        L.WavpackSetConfiguration64.argtypes = [ctypes.c_void_p, ctypes.POINTER(WavpackConfig),
                                                ctypes.c_int64, ctypes.c_char_p]
        L.WavpackSetConfiguration64.restype = ctypes.c_int
        L.WavpackPackInit.argtypes = [ctypes.c_void_p]
        L.WavpackPackInit.restype = ctypes.c_int
        L.WavpackPackSamples.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32), ctypes.c_uint32]
        L.WavpackPackSamples.restype = ctypes.c_int
        L.WavpackFlushSamples.argtypes = [ctypes.c_void_p]
        L.WavpackFlushSamples.restype = ctypes.c_int
        L.WavpackUpdateNumSamples.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        L.WavpackOpenFileInputEx64.argtypes = [ctypes.POINTER(StreamReader64), ctypes.c_void_p,
                                               ctypes.c_void_p, ctypes.c_char_p,
                                               ctypes.c_int, ctypes.c_int]
        L.WavpackOpenFileInputEx64.restype = ctypes.c_void_p
        L.WavpackGetNumSamples64.argtypes = [ctypes.c_void_p]
        L.WavpackGetNumSamples64.restype = ctypes.c_int64
        L.WavpackGetSampleRate.argtypes = [ctypes.c_void_p]
        L.WavpackGetSampleRate.restype = ctypes.c_uint
        L.WavpackGetNumChannels.argtypes = [ctypes.c_void_p]
        L.WavpackGetNumChannels.restype = ctypes.c_int
        L.WavpackGetBitsPerSample.argtypes = [ctypes.c_void_p]
        L.WavpackGetBitsPerSample.restype = ctypes.c_int
        L.WavpackGetBytesPerSample.argtypes = [ctypes.c_void_p]
        L.WavpackGetBytesPerSample.restype = ctypes.c_int
        L.WavpackGetMode.argtypes = [ctypes.c_void_p]
        L.WavpackGetMode.restype = ctypes.c_int
        L.WavpackUnpackSamples.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32), ctypes.c_uint32]
        L.WavpackUnpackSamples.restype = ctypes.c_uint32
        L.WavpackGetNumErrors.argtypes = [ctypes.c_void_p]
        L.WavpackGetNumErrors.restype = ctypes.c_int
        L.WavpackGetSampleIndex64.argtypes = [ctypes.c_void_p]
        L.WavpackGetSampleIndex64.restype = ctypes.c_int64

    # ---------------- decode ----------------

    def _open_mem(self, data: bytes):
        buf = ctypes.create_string_buffer(data, len(data))
        state = {"pos": 0, "size": len(data)}

        def read_bytes(_id, out, bcount):
            pos = state["pos"]
            n = min(int(bcount), state["size"] - pos)
            if n <= 0:
                return 0
            ctypes.memmove(out, ctypes.byref(buf, pos), n)
            state["pos"] = pos + n
            return n

        def write_bytes(_id, _data, _bcount):
            return 0

        def get_pos(_id):
            return state["pos"]

        def set_pos_abs(_id, pos):
            if pos < 0 or pos > state["size"]:
                return -1
            state["pos"] = int(pos)
            return 0

        def set_pos_rel(_id, delta, mode):
            base = state["pos"] if mode == 1 else (state["size"] if mode == 2 else 0)
            pos = base + int(delta)
            if pos < 0 or pos > state["size"]:
                return -1
            state["pos"] = pos
            return 0

        def push_back_byte(_id, _c):
            state["pos"] -= 1
            return 0

        def get_length(_id):
            return state["size"]

        def can_seek(_id):
            return 1

        def truncate_here(_id):
            return 0

        def close(_id):
            return 0

        callbacks = [read_bytes, write_bytes, get_pos, set_pos_abs, set_pos_rel,
                     push_back_byte, get_length, can_seek, truncate_here, close]
        reader = StreamReader64()
        reader.read_bytes = StreamReader64._fields_[0][1](read_bytes)
        reader.write_bytes = StreamReader64._fields_[1][1](write_bytes)
        reader.get_pos = StreamReader64._fields_[2][1](get_pos)
        reader.set_pos_abs = StreamReader64._fields_[3][1](set_pos_abs)
        reader.set_pos_rel = StreamReader64._fields_[4][1](set_pos_rel)
        reader.push_back_byte = StreamReader64._fields_[5][1](push_back_byte)
        reader.get_length = StreamReader64._fields_[6][1](get_length)
        reader.can_seek = StreamReader64._fields_[7][1](can_seek)
        reader.truncate_here = StreamReader64._fields_[8][1](truncate_here)
        reader.close = StreamReader64._fields_[9][1](close)
        return reader, callbacks

    def decode_file(self, path: str):
        with open(path, "rb") as f:
            return self.decode_memory(f.read())

    def decode_memory(self, data: bytes):
        reader, _keepalive = self._open_mem(data)
        err = ctypes.create_string_buffer(80)
        wpc = self.lib.WavpackOpenFileInputEx64(ctypes.byref(reader), None, None, err, 0, 0)
        if not wpc:
            raise WavpackError(err.value.decode("utf-8", "replace") or "cannot open WavPack stream")
        try:
            channels = self.lib.WavpackGetNumChannels(wpc)
            rate = int(self.lib.WavpackGetSampleRate(wpc))
            bits = self.lib.WavpackGetBitsPerSample(wpc)
            total = int(self.lib.WavpackGetNumSamples64(wpc))
            if total <= 0:
                total = 0
            out = (ctypes.c_int32 * max(total * channels, 1))()
            got = 0
            while got < total:
                want = min(total - got, 1 << 16)
                ptr = ctypes.cast(ctypes.byref(out, got * channels * 4), ctypes.POINTER(ctypes.c_int32))
                n = self.lib.WavpackUnpackSamples(wpc, ptr, want)
                if n == 0:
                    break
                got += n
            errors = self.lib.WavpackGetNumErrors(wpc)
            if errors:
                raise WavpackError(f"WavPack decode reported {errors} error(s)")
            pcm = bytearray(got * channels * 2)
            struct.pack_into(f"<{got * channels}h", pcm, 0,
                             *[max(-32768, min(32767, out[i])) for i in range(got * channels)])
            return {"pcm": bytes(pcm), "sample_rate": rate, "channels": channels,
                    "bits": bits, "samples": got}
        finally:
            self.lib.WavpackCloseFile(wpc)

    # ---------------- encode ----------------

    def encode_file(self, pcm: bytes, sample_rate: int, channels: int, out_path: str,
                    fast: bool = True) -> int:
        if channels not in (1, 2):
            raise WavpackError("only mono or stereo is supported by the game")
        frames = len(pcm) // (2 * channels)
        if frames <= 0:
            raise WavpackError("empty audio")
        samples = list(struct.unpack(f"<{frames * channels}h", pcm[:frames * channels * 2]))
        cfg = WavpackConfig()
        cfg.bits_per_sample = 16
        cfg.bytes_per_sample = 2
        cfg.num_channels = channels
        cfg.sample_rate = int(sample_rate)
        cfg.channel_mask = 4 if channels == 1 else 3
        cfg.flags = CONFIG_FAST_FLAG if fast else 0
        if channels == 2:
            cfg.flags |= CONFIG_JOINT_STEREO
        cfg.qmode = QMODE_NO_STORE_WRAPPER
        cfg.block_samples = 0
        cfg.worker_threads = 1

        chunks = []
        total = [0]

        def blockout(_id, data, bcount):
            chunks.append(ctypes.string_at(data, bcount))
            total[0] += bcount
            return 1

        cb = BLOCK_OUTPUT(blockout)
        tmp_path = out_path + ".part"
        first_block = []

        try:
            with open(tmp_path, "wb") as f:
                def sink(_id, data, bcount):
                    if not first_block:
                        first_block.append(ctypes.string_at(data, bcount))
                    f.write(ctypes.string_at(data, bcount))
                    return 1
                cb_file = BLOCK_OUTPUT(sink)
                wpc = self.lib.WavpackOpenFileOutput(cb_file, None, None)
                if not wpc:
                    raise WavpackError("WavpackOpenFileOutput failed")
                try:
                    if not self.lib.WavpackSetConfiguration64(wpc, ctypes.byref(cfg), frames, None):
                        raise WavpackError(self.lib.WavpackGetErrorMessage(wpc).decode("utf-8", "replace"))
                    if not self.lib.WavpackPackInit(wpc):
                        raise WavpackError(self.lib.WavpackGetErrorMessage(wpc).decode("utf-8", "replace"))
                    buf = (ctypes.c_int32 * len(samples))(*samples)
                    if not self.lib.WavpackPackSamples(wpc, buf, frames):
                        raise WavpackError(self.lib.WavpackGetErrorMessage(wpc).decode("utf-8", "replace"))
                    if not self.lib.WavpackFlushSamples(wpc):
                        raise WavpackError(self.lib.WavpackGetErrorMessage(wpc).decode("utf-8", "replace"))
                    if first_block:
                        head = first_block[0]
                        fixed = ctypes.create_string_buffer(head, len(head))
                        self.lib.WavpackUpdateNumSamples(wpc, fixed)
                        f.seek(0)
                        f.write(fixed.raw)
                finally:
                    self.lib.WavpackCloseFile(wpc)
                _ = cb
            with open(tmp_path, "rb") as f:
                head = f.read(32)
            if head[:4] != b"wvpk":
                raise WavpackError("encoder produced an invalid block")
            os.replace(tmp_path, out_path)
            return os.path.getsize(out_path)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise

    def _fix_total_samples(self, block: bytes, frames: int):
        if len(block) < 32:
            return None
        buf = ctypes.create_string_buffer(block, len(block))
        err = ctypes.create_string_buffer(80)
        reader, _keep = self._open_mem(block)
        wpc = self.lib.WavpackOpenFileInputEx64(ctypes.byref(reader), None, None, err, 0, 0)
        if not wpc:
            return None
        try:
            self.lib.WavpackUpdateNumSamples(wpc, buf)
            return buf.raw
        finally:
            self.lib.WavpackCloseFile(wpc)

    def info(self, path: str):
        with open(path, "rb") as f:
            head = f.read(64)
        if len(head) < 32 or head[:4] != b"wvpk":
            return None
        total, _bi, _bs, flags = struct.unpack("<IIII", head[12:28])
        if total == 0xFFFFFFFF:
            total = 0
        rate = SAMPLE_RATES[(flags >> 23) & 0xF] or 0
        mono = bool(flags & 0x4)
        return {"sample_rate": rate, "channels": 1 if mono else 2,
                "samples": total, "hybrid": bool(flags & 0x8),
                "float": bool(flags & 0x80), "size": os.path.getsize(path)}


def load_wav_any(path: str):
    """Decode .wav/.aiff (stdlib) into (pcm16 bytes, rate, channels)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".wav", ".wave"):
        import wave
        with wave.open(path, "rb") as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
    elif ext in (".aif", ".aiff", ".aifc"):
        try:
            import aifc
        except ImportError as e:
            raise WavpackError("AIFF support needs Python <= 3.12 (aifc was removed): %s" % e)
        with aifc.open(path, "rb") as a:
            ch, width, rate, n = a.getnchannels(), a.getsampwidth(), a.getframerate(), a.getnframes()
            raw = a.readframes(n)
    else:
        raise WavpackError(f"unsupported container: {ext or path}")
    if ch not in (1, 2):
        raise WavpackError(f"{ch} channels found, the game needs mono or stereo")
    return pcm_to_pcm16(raw, width, ch), rate, ch


def pcm_to_pcm16(raw: bytes, width: int, channels: int) -> bytes:
    if width == 2:
        return raw
    if width == 1:
        out = bytearray(len(raw) * 2)
        for i, b in enumerate(raw):
            v = (b - 128) << 8
            struct.pack_into("<h", out, i * 2, v)
        return bytes(out)
    if width == 3:
        n = len(raw) // 3
        out = bytearray(n * 2)
        for i in range(n):
            b0, b1, b2 = raw[i * 3], raw[i * 3 + 1], raw[i * 3 + 2]
            v = (b1 << 8) | (b0 << 0) | (b2 << 16)
            if v & 0x800000:
                v -= 1 << 24
            struct.pack_into("<h", out, i * 2, max(-32768, min(32767, v >> 8)))
        return bytes(out)
    if width == 4:
        n = len(raw) // 4
        vals = struct.unpack(f"<{n}i", raw[:n * 4])
        out = bytearray(n * 2)
        struct.pack_into(f"<{n}h", out, 0, *[max(-32768, min(32767, v >> 16)) for v in vals])
        return bytes(out)
    raise WavpackError(f"unsupported sample width {width * 8} bit")
