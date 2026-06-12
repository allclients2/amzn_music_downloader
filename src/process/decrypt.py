"""In-process CENC (Widevine) decryption — the pure-Python replacement for `mp4decrypt`.

Amazon Music serves a Widevine-protected fragmented MP4 (`urn:mpeg:cenc:2013`,
i.e. AES-128 **CTR** common encryption). The content key is already recovered by
`process/keys.py` via pywidevine; this module does only the byte-level decrypt that
`mp4decrypt` used to, so `ffmpeg` can then stream-copy the plaintext into its native
container exactly as before.

Strategy — **decrypt in place, never move a byte**. AES-CTR is length-preserving, so
each decrypted sample drops straight back into its original `mdat` position. The only
structural edits are size-preserving 4-byte renames: the protected sample entry's
fourcc (`enca`) is swapped back to its original format (read from `sinf/frma`), and the
encryption-signalling boxes (`sinf`, `senc`, `saiz`, `saio`, and the CENC
`sbgp`/`sgpd`) are renamed to `free` so decoders ignore them. Because no box ever
changes size, every `moof`/`trun` sample offset stays valid with zero fixup.

The `enca`→`frma` + `sinf`-stripping idea is adapted from gamdl's MIT-licensed
`amdecrypt.py` (https://github.com/glomatico/gamdl); the CTR cipher and in-place
box surgery here are new (gamdl targets Apple Music's FairPlay CBCS instead).
"""

import logging
import struct

from Crypto.Cipher import AES

_log = logging.getLogger("downloader.decrypt")

# tfhd flag bits (ISO/IEC 14496-12).
_TFHD_BASE_DATA_OFFSET = 0x000001
_TFHD_SAMPLE_DESC_INDEX = 0x000002
_TFHD_DEFAULT_DURATION = 0x000008
_TFHD_DEFAULT_SIZE = 0x000010
_TFHD_DEFAULT_FLAGS = 0x000020
_TFHD_DEFAULT_BASE_IS_MOOF = 0x020000

# trun flag bits.
_TRUN_DATA_OFFSET = 0x000001
_TRUN_FIRST_SAMPLE_FLAGS = 0x000004
_TRUN_SAMPLE_DURATION = 0x000100
_TRUN_SAMPLE_SIZE = 0x000200
_TRUN_SAMPLE_FLAGS = 0x000400
_TRUN_SAMPLE_CTO = 0x000800

# senc flag bit: per-sample subsample (clear/encrypted) ranges present.
_SENC_SUBSAMPLES = 0x000002

# Boxes that only describe the (now removed) encryption; renamed to `free` in place.
_PROTECTION_BOXES = (b"senc", b"saiz", b"saio")


class DecryptError(Exception):
    """Raised when the encrypted MP4 can't be parsed or isn't CENC/CTR."""


def _iter_boxes(buf, start, end):
    """Yield ``(type, box_start, content_start, box_end)`` for boxes in ``[start, end)``."""
    off = start
    while off + 8 <= end:
        size = struct.unpack(">I", buf[off : off + 4])[0]
        btype = bytes(buf[off + 4 : off + 8])
        header = 8
        if size == 1:  # 64-bit largesize
            if off + 16 > end:
                break
            size = struct.unpack(">Q", buf[off + 8 : off + 16])[0]
            header = 16
        elif size == 0:  # extends to end of container
            size = end - off
        if size < header or off + size > end:
            break
        yield btype, off, off + header, off + size
        off += size


def _find_box(buf, start, end, target):
    """Return ``(box_start, content_start, box_end)`` of the first ``target`` child, or None."""
    for btype, box_start, content_start, box_end in _iter_boxes(buf, start, end):
        if btype == target:
            return box_start, content_start, box_end
    return None


def _find_path(buf, start, end, *path):
    """Descend a chain of box types, returning the deepest ``(start, content, end)`` or None."""
    cur = (start, start, end)
    for target in path:
        found = _find_box(buf, cur[1], cur[2], target)
        if found is None:
            return None
        cur = found
    return cur


# --------------------------------------------------------------------------- #
# moov: read the CENC parameters and neutralise the protected sample entry.
# --------------------------------------------------------------------------- #


def _parse_tenc(buf, content_start, box_end):
    """Parse a `tenc` box → ``(per_sample_iv_size, constant_iv)``.

    `constant_iv` is the 16-byte fallback IV used when samples carry no per-sample IV.
    """
    c = content_start
    # FullBox: version(1) flags(3); payload: reserved(1) pattern(1) isProtected(1)
    # default_Per_Sample_IV_Size(1) default_KID(16) [constant_iv_size(1) constant_iv].
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


# Bytes from a sample entry's start to where its child boxes (sinf, codec config,
# …) begin. An ISO AudioSampleEntry is box header(8) + reserved/data_ref(8) +
# audio fields(20) = 36; every Amazon audio codec (enca/fLaC/Opus/ec-3/ac-4/mhm1)
# uses this layout.
_AUDIO_SAMPLE_ENTRY_HEADER = 36


def _prepare_moov(buf, moov_content, moov_end):
    """Read CENC params from the protected stsd entry and strip its encryption signalling.

    Returns ``(per_sample_iv_size, constant_iv)``. Mutates ``buf`` in place: the `enca`
    fourcc becomes its original format and the entry's `sinf` is renamed to `free`.
    """
    stsd = _find_path(
        buf, moov_content, moov_end,
        b"trak", b"mdia", b"minf", b"stbl", b"stsd",
    )
    if stsd is None:
        raise DecryptError("no moov/trak/mdia/minf/stbl/stsd box")

    iv_size = None
    constant_iv = b""
    # stsd content: version+flags(4) entry_count(4) then sample entries.
    for _btype, entry_start, _entry_content, entry_end in _iter_boxes(
        buf, stsd[1] + 8, stsd[2]
    ):
        # Child boxes start after the fixed audio sample-entry header, not at +8.
        child_start = entry_start + _AUDIO_SAMPLE_ENTRY_HEADER
        if child_start >= entry_end:
            continue
        sinf = _find_box(buf, child_start, entry_end, b"sinf")
        if sinf is None:
            continue  # not a protected entry

        # Original codec format from sinf/frma → restore the sample-entry fourcc.
        frma = _find_box(buf, sinf[1], sinf[2], b"frma")
        if frma is not None:
            buf[entry_start + 4 : entry_start + 8] = buf[frma[1] : frma[1] + 4]

        # Scheme must be CENC (AES-CTR). cbcs/cbc1 would need a different cipher.
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

        # Rename sinf → free so decoders treat the (now plaintext) entry as unencrypted.
        buf[sinf[0] + 4 : sinf[0] + 8] = b"free"

    if iv_size is None:
        raise DecryptError("no protected sample entry / tenc found in moov")
    return iv_size, constant_iv


# --------------------------------------------------------------------------- #
# moof/mdat: decrypt each sample and neutralise per-fragment encryption boxes.
# --------------------------------------------------------------------------- #


def _parse_tfhd(buf, content_start, box_end):
    """Parse a `tfhd` box → dict of the fields we need."""
    flags = struct.unpack(">I", b"\x00" + buf[content_start + 1 : content_start + 4])[0]
    info = {
        "flags": flags,
        "base_data_offset": None,
        "default_sample_size": 0,
        "default_base_is_moof": bool(flags & _TFHD_DEFAULT_BASE_IS_MOOF),
    }
    off = content_start + 8  # skip version+flags(4) + track_id(4)
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
    """Parse a `trun` box → ``(sample_sizes, data_offset_or_None)``."""
    version = buf[content_start]
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
    """Parse a `senc` box → list of ``(iv_bytes, subsamples)`` per sample."""
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
    """Build the 16-byte CTR initial counter block from a sample IV (8 or 16 bytes)."""
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
    """Decrypt one sample's bytes in place at ``buf[pos:pos+size]`` with AES-CTR."""
    cipher = AES.new(key, AES.MODE_CTR, nonce=b"", initial_value=iv16)
    if not subsamples:
        buf[pos : pos + size] = cipher.decrypt(bytes(buf[pos : pos + size]))
        return
    # Mixed clear/encrypted: clear bytes pass through and do not consume keystream;
    # encrypted runs share one contiguous CTR keystream across the sample.
    off = pos
    for clear, enc in subsamples:
        off += clear
        if enc:
            buf[off : off + enc] = cipher.decrypt(bytes(buf[off : off + enc]))
            off += enc


def _process_traf(buf, traf_start, traf_content, traf_end, moof_start, iv_size, constant_iv, key):
    """Decrypt every sample described by one `traf`, then neutralise its encryption boxes."""
    tfhd = _find_box(buf, traf_content, traf_end, b"tfhd")
    if tfhd is None:
        return
    tf = _parse_tfhd(buf, tfhd[1], tfhd[2])

    senc = _find_box(buf, traf_content, traf_end, b"senc")
    senc_entries = _parse_senc(buf, senc[1], senc[2], iv_size) if senc else []

    # Clear-lead: Amazon leaves the opening fragments unencrypted (so playback can
    # start before the license arrives) — those carry no `senc`. With no per-sample
    # IVs and no constant IV from `tenc`, the samples are already plaintext, so
    # touching them would corrupt the audio. Leave the whole fragment alone.
    if not senc_entries and not constant_iv:
        return

    # Base offset that trun.data_offset is relative to (ISO 14496-12 §8.8.7).
    if tf["base_data_offset"] is not None:
        base = tf["base_data_offset"]
    else:  # default-base-is-moof, or single-traf default
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
            else:  # senc covers fewer samples than the trun → remainder is clear
                iv, subsamples = b"", []
            # Only decrypt samples we actually have key material (an IV) for.
            if iv or constant_iv:
                _decrypt_sample(buf, pos, size, _ctr_iv(iv, constant_iv), subsamples, key)
            pos += size
            sample_index += 1

    # Rename the now-meaningless encryption boxes to `free` (size-preserving).
    for btype, box_start, b_content, b_end in _iter_boxes(buf, traf_content, traf_end):
        if btype in _PROTECTION_BOXES:
            buf[box_start + 4 : box_start + 8] = b"free"
        elif btype in (b"sbgp", b"sgpd"):
            # Only the CENC sample-group (grouping_type 'seig') is encryption metadata.
            if bytes(buf[b_content + 4 : b_content + 8]) == b"seig":
                buf[box_start + 4 : box_start + 8] = b"free"


def decrypt_mp4(encrypted_path, key, output_path):
    """Decrypt a Widevine CENC fragmented MP4 to a plaintext MP4 at ``output_path``.

    ``key`` is the ``kid:key`` (or bare ``key``) hex string from
    :meth:`process.keys.Keys.getContentKeys`. Raises :class:`DecryptError` if the file
    can't be parsed or isn't CENC (AES-CTR).
    """
    key_hex = key.split(":")[-1].strip()
    key_bytes = bytes.fromhex(key_hex)

    with open(encrypted_path, "rb") as f:
        buf = bytearray(f.read())
    end = len(buf)

    moov = _find_box(buf, 0, end, b"moov")
    if moov is None:
        raise DecryptError("no moov box found")
    iv_size, constant_iv = _prepare_moov(buf, moov[1], moov[2])

    # Each moof carries the sample table for the mdat that immediately follows it.
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
