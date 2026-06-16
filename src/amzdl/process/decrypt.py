"""In-process CENC (Widevine) decryption — the pure-Python replacement for `mp4decrypt`. Decrypts the AES-CTR fragmented MP4 in place using the recovered content key, renaming the encryption-signalling boxes to `free` so no byte ever moves."""

import logging
import struct

from Crypto.Cipher import AES

_log = logging.getLogger("downloader.decrypt")

_TFHD_BASE_DATA_OFFSET = 0x000001
_TFHD_SAMPLE_DESC_INDEX = 0x000002
_TFHD_DEFAULT_DURATION = 0x000008
_TFHD_DEFAULT_SIZE = 0x000010
_TFHD_DEFAULT_FLAGS = 0x000020
_TFHD_DEFAULT_BASE_IS_MOOF = 0x020000

_TRUN_DATA_OFFSET = 0x000001
_TRUN_FIRST_SAMPLE_FLAGS = 0x000004
_TRUN_SAMPLE_DURATION = 0x000100
_TRUN_SAMPLE_SIZE = 0x000200
_TRUN_SAMPLE_FLAGS = 0x000400
_TRUN_SAMPLE_CTO = 0x000800

_SENC_SUBSAMPLES = 0x000002

_PROTECTION_BOXES = (b"senc", b"saiz", b"saio")


class DecryptError(Exception):
    pass


def _iter_boxes(buf, start, end):
    off = start
    while off + 8 <= end:
        size = struct.unpack(">I", buf[off : off + 4])[0]
        btype = bytes(buf[off + 4 : off + 8])
        header = 8
        if size == 1:
            if off + 16 > end:
                break
            size = struct.unpack(">Q", buf[off + 8 : off + 16])[0]
            header = 16
        elif size == 0:
            size = end - off
        if size < header or off + size > end:
            break
        yield btype, off, off + header, off + size
        off += size


def _find_box(buf, start, end, target):
    for btype, box_start, content_start, box_end in _iter_boxes(buf, start, end):
        if btype == target:
            return box_start, content_start, box_end
    return None


def _find_path(buf, start, end, *path):
    cur = (start, start, end)
    for target in path:
        found = _find_box(buf, cur[1], cur[2], target)
        if found is None:
            return None
        cur = found
    return cur


def _parse_tenc(buf, content_start, box_end):
    c = content_start
    if c + 8 + 16 > box_end:
        raise DecryptError("tenc box truncated")
    is_protected = buf[c + 6]
    iv_size = buf[c + 7]
    constant_iv = b""
    if iv_size == 0 and is_protected:
        off = c + 8 + 16
        if off < box_end:
            civ_size = buf[off]
            constant_iv = bytes(buf[off + 1 : off + 1 + civ_size])
    return iv_size, constant_iv


_AUDIO_SAMPLE_ENTRY_HEADER = 36


def _prepare_moov(buf, moov_content, moov_end):
    stsd = _find_path(
        buf, moov_content, moov_end,
        b"trak", b"mdia", b"minf", b"stbl", b"stsd",
    )
    if stsd is None:
        raise DecryptError("no moov/trak/mdia/minf/stbl/stsd box")

    iv_size = None
    constant_iv = b""
    for _btype, entry_start, _entry_content, entry_end in _iter_boxes(
        buf, stsd[1] + 8, stsd[2]
    ):
        child_start = entry_start + _AUDIO_SAMPLE_ENTRY_HEADER
        if child_start >= entry_end:
            continue
        sinf = _find_box(buf, child_start, entry_end, b"sinf")
        if sinf is None:
            continue

        frma = _find_box(buf, sinf[1], sinf[2], b"frma")
        if frma is not None:
            buf[entry_start + 4 : entry_start + 8] = buf[frma[1] : frma[1] + 4]

        schm = _find_box(buf, sinf[1], sinf[2], b"schm")
        if schm is not None:
            scheme = bytes(buf[schm[1] + 4 : schm[1] + 8])
            if scheme != b"cenc":
                raise DecryptError(
                    f"unsupported protection scheme {scheme!r}; only cenc (AES-CTR) is supported"
                )

        tenc = _find_path(buf, sinf[1], sinf[2], b"schi", b"tenc")
        if tenc is not None:
            iv_size, constant_iv = _parse_tenc(buf, tenc[1], tenc[2])

        buf[sinf[0] + 4 : sinf[0] + 8] = b"free"

    if iv_size is None:
        raise DecryptError("no protected sample entry / tenc found in moov")
    return iv_size, constant_iv


def _parse_tfhd(buf, content_start, box_end):
    flags = struct.unpack(">I", b"\x00" + buf[content_start + 1 : content_start + 4])[0]
    info = {
        "flags": flags,
        "base_data_offset": None,
        "default_sample_size": 0,
        "default_base_is_moof": bool(flags & _TFHD_DEFAULT_BASE_IS_MOOF),
    }
    off = content_start + 8
    if flags & _TFHD_BASE_DATA_OFFSET:
        info["base_data_offset"] = struct.unpack(">Q", buf[off : off + 8])[0]
        off += 8
    if flags & _TFHD_SAMPLE_DESC_INDEX:
        off += 4
    if flags & _TFHD_DEFAULT_DURATION:
        off += 4
    if flags & _TFHD_DEFAULT_SIZE:
        info["default_sample_size"] = struct.unpack(">I", buf[off : off + 4])[0]
        off += 4
    return info


def _parse_trun(buf, content_start, box_end, default_size):
    flags = struct.unpack(">I", b"\x00" + buf[content_start + 1 : content_start + 4])[0]
    sample_count = struct.unpack(">I", buf[content_start + 4 : content_start + 8])[0]
    off = content_start + 8
    data_offset = None
    if flags & _TRUN_DATA_OFFSET:
        data_offset = struct.unpack(">i", buf[off : off + 4])[0]
        off += 4
    if flags & _TRUN_FIRST_SAMPLE_FLAGS:
        off += 4
    sizes = []
    for _ in range(sample_count):
        if flags & _TRUN_SAMPLE_DURATION:
            off += 4
        if flags & _TRUN_SAMPLE_SIZE:
            sizes.append(struct.unpack(">I", buf[off : off + 4])[0])
            off += 4
        else:
            sizes.append(default_size)
        if flags & _TRUN_SAMPLE_FLAGS:
            off += 4
        if flags & _TRUN_SAMPLE_CTO:
            off += 4
    return sizes, data_offset


def _parse_senc(buf, content_start, box_end, iv_size):
    flags = struct.unpack(">I", b"\x00" + buf[content_start + 1 : content_start + 4])[0]
    sample_count = struct.unpack(">I", buf[content_start + 4 : content_start + 8])[0]
    off = content_start + 8
    entries = []
    for _ in range(sample_count):
        iv = bytes(buf[off : off + iv_size]) if iv_size else b""
        off += iv_size
        subsamples = []
        if flags & _SENC_SUBSAMPLES:
            subsample_count = struct.unpack(">H", buf[off : off + 2])[0]
            off += 2
            for _ in range(subsample_count):
                clear = struct.unpack(">H", buf[off : off + 2])[0]
                enc = struct.unpack(">I", buf[off + 2 : off + 6])[0]
                subsamples.append((clear, enc))
                off += 6
        entries.append((iv, subsamples))
    return entries


def _ctr_iv(iv, constant_iv):
    if not iv:
        iv = constant_iv
    if len(iv) == 16:
        return iv
    if len(iv) == 8:
        return iv + b"\x00" * 8
    if len(iv) < 16:
        return iv + b"\x00" * (16 - len(iv))
    return iv[:16]


def _decrypt_sample(buf, pos, size, iv16, subsamples, key):
    cipher = AES.new(key, AES.MODE_CTR, nonce=b"", initial_value=iv16)
    if not subsamples:
        buf[pos : pos + size] = cipher.decrypt(bytes(buf[pos : pos + size]))
        return
    off = pos
    for clear, enc in subsamples:
        off += clear
        if enc:
            buf[off : off + enc] = cipher.decrypt(bytes(buf[off : off + enc]))
            off += enc


def _process_traf(buf, traf_start, traf_content, traf_end, moof_start, iv_size, constant_iv, key):
    tfhd = _find_box(buf, traf_content, traf_end, b"tfhd")
    if tfhd is None:
        return
    tf = _parse_tfhd(buf, tfhd[1], tfhd[2])

    senc = _find_box(buf, traf_content, traf_end, b"senc")
    senc_entries = _parse_senc(buf, senc[1], senc[2], iv_size) if senc else []

    if not senc_entries and not constant_iv:
        return

    if tf["base_data_offset"] is not None:
        base = tf["base_data_offset"]
    else:
        base = moof_start

    sample_index = 0
    for btype, _bs, b_content, b_end in _iter_boxes(buf, traf_content, traf_end):
        if btype != b"trun":
            continue
        sizes, data_offset = _parse_trun(buf, b_content, b_end, tf["default_sample_size"])
        pos = base + (data_offset or 0)
        for size in sizes:
            if sample_index < len(senc_entries):
                iv, subsamples = senc_entries[sample_index]
            else:
                iv, subsamples = b"", []
            if iv or constant_iv:
                _decrypt_sample(buf, pos, size, _ctr_iv(iv, constant_iv), subsamples, key)
            pos += size
            sample_index += 1

    for btype, box_start, b_content, _b_end in _iter_boxes(buf, traf_content, traf_end):
        if btype in _PROTECTION_BOXES:
            buf[box_start + 4 : box_start + 8] = b"free"
        elif btype in (b"sbgp", b"sgpd"):
            if bytes(buf[b_content + 4 : b_content + 8]) == b"seig":
                buf[box_start + 4 : box_start + 8] = b"free"


def decrypt_mp4(encrypted_path, key, output_path):
    key_hex = key.split(":")[-1].strip()
    key_bytes = bytes.fromhex(key_hex)

    with open(encrypted_path, "rb") as f:
        buf = bytearray(f.read())
    end = len(buf)

    moov = _find_box(buf, 0, end, b"moov")
    if moov is None:
        raise DecryptError("no moov box found")
    iv_size, constant_iv = _prepare_moov(buf, moov[1], moov[2])

    fragment_count = 0
    pending_moof = None
    for btype, box_start, content_start, box_end in _iter_boxes(buf, 0, end):
        if btype == b"moof":
            pending_moof = (box_start, content_start, box_end)
        elif btype == b"mdat" and pending_moof is not None:
            moof_start, moof_content, moof_end = pending_moof
            for tb, ts, tc, te in _iter_boxes(buf, moof_content, moof_end):
                if tb == b"traf":
                    _process_traf(
                        buf, ts, tc, te, moof_start, iv_size, constant_iv, key_bytes
                    )
            fragment_count += 1
            pending_moof = None

    if fragment_count == 0:
        raise DecryptError("no moof/mdat fragments found to decrypt")

    with open(output_path, "wb") as f:
        f.write(buf)
    _log.debug("decrypted %s fragment(s) -> %s", fragment_count, output_path)
    return output_path
