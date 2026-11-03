"""The data image of the game's native code: exefs/main (+ exefs/sdk) -> RTDX_Data/StreamingAssets/rtdx/rtdx_image.bin.

The player's rtdx_native.dll is the game's native C++ (Lua 5.1, the dungeon / data core, Bishamon effects ...) rebuilt for
x64. Its code is ours; its DATA is the console executable's: read-only data (.rodata), initialised data (.data) and the
layout of .bss, at the addresses the code was translated against. That image is made here from the player's own dump, the
same way the PC rebuild makes it (tools/nso_decompress.py + tools/native_image.py of the rebuild):
  - main and sdk are NSO0 files: three segments (text, rodata, data), each LZ4-compressed; laid out flat at their memory
    offsets;
  - image = header (magic RTDXIMG2, ro start, data end, bss end, counts) + the RELATIVE relocations and import slots of the
    range [ro start, data end) from main's dynamic section + the initial bytes of every DATA symbol main imports (from the
    sdk module's own definition, else zeros) + main's bytes [ro start, data end).
The result must be byte-identical to the image the player was tested with (its sha1 is in mtdx/release.json).
"""
import hashlib
import struct

import lz4.block

from .unityfiles import ConvertError

# main of version 1.0.2: the layout rtdx_native.dll was translated against
TEXT_END, RO_START, DATA_START = 0x2949e48, 0x294a000, 0x4822000
RO_END, DATA_END, BSS_SIZE = 0x294a000 + 0x1ed7290, 0x4822000 + 0x483698, 0x8ec968
BSS_END = (DATA_END + BSS_SIZE + 0xfff) & ~0xfff


def nso_flat(raw):
    """NSO0 bytes -> the flat image (segments at their memory offsets; ends where .bss starts)."""
    b = bytes(raw)
    if b[:4] != b'NSO0':
        raise ConvertError('not an NSO0 executable')
    flags = struct.unpack_from('<I', b, 0xC)[0]
    segs = [struct.unpack_from('<III', b, off) for off in (0x10, 0x20, 0x30)]
    csz = struct.unpack_from('<III', b, 0x60)
    out = bytearray(max(m + s for _, m, s in segs))
    for i, (foff, moff, size) in enumerate(segs):
        data = b[foff:foff + csz[i]]
        if flags & (1 << i):
            data = lz4.block.decompress(data, uncompressed_size=size)
        if len(data) != size:
            raise ConvertError(f'NSO segment {i}: {len(data)} bytes, expected {size}')
        out[moff:moff + size] = data
    return bytes(out)


def _dynamic(b):
    mod0 = struct.unpack_from('<I', b, 4)[0]
    off = mod0 + struct.unpack_from('<i', b, mod0 + 4)[0]
    dyn = {}
    while True:
        tag, val = struct.unpack_from('<qQ', b, off)
        if tag == 0:
            return dyn
        dyn.setdefault(tag, val)
        off += 16


def build_image(main, sdk):
    """main / sdk = flat images -> rtdx_image.bin bytes (format RTDXIMG2, see rt/image.cpp of the rebuild)."""
    b = main
    if len(b) < DATA_END:
        raise ConvertError(f'main is {len(b)} bytes, the image needs {DATA_END}')
    dyn = _dynamic(b)
    strtab, symtab = dyn[5], dyn[6]

    def symname(idx):
        st_name = struct.unpack_from('<I', b, symtab + idx * 24)[0]
        e = b.index(b'\0', strtab + st_name)
        return b[strtab + st_name:e].decode('utf-8', 'replace')

    def symtype(idx):
        return b[symtab + idx * 24 + 4] & 0xf

    rel, imp = [], []
    for base, size in ((dyn.get(7), dyn.get(8)), (dyn.get(23), dyn.get(2))):
        if not base:
            continue
        for off in range(base, base + size, 24):
            r_off, r_info, r_add = struct.unpack_from('<QQq', b, off)
            typ, sym = r_info & 0xffffffff, r_info >> 32
            if not (RO_START <= r_off < BSS_END):
                continue
            if typ == 1027:                              # R_AARCH64_RELATIVE
                rel.append((r_off, r_add & 0xffffffffffffffff))
            elif typ in (1025, 1026, 257):               # GLOB_DAT, JUMP_SLOT, ABS64
                imp.append((r_off, typ, symtype(sym), r_add, symname(sym)))
    sd = _dynamic(sdk)
    sdefs = {}
    for k in range((sd[5] - sd[6]) // 24):
        o = sd[6] + 24 * k
        st_name, st_info, st_other, st_shndx, st_value, st_size = struct.unpack_from('<IBBHQQ', sdk, o)
        if st_shndx == 0 or st_name == 0:
            continue
        e = sdk.index(b'\0', sd[5] + st_name)
        sdefs[sdk[sd[5] + st_name:e].decode('utf-8', 'replace')] = (st_value, st_size)
    data, seen = [], set()
    for o, t, stt, a, n in imp:
        if stt == 2 or n in seen:
            continue
        seen.add(n)
        v, sz = sdefs.get(n, (0, 0))
        if sz:
            blob = sdk[v:v + sz] if v + sz <= len(sdk) else bytes(sz)
        else:
            sz = 16 if stt == 0 else 64
            blob = bytes(sz)
        data.append((n, blob))
    out = bytearray(b'RTDXIMG2' + struct.pack('<7Q', RO_START, DATA_END, BSS_END, len(rel), len(imp), len(data), 0))
    for o, a in rel:
        out += struct.pack('<QQ', o, a)
    for o, t, stt, a, n in imp:
        nb = n.encode('utf-8')
        out += struct.pack('<QIIqI', o, t, stt, a, len(nb)) + nb
    for n, blob in data:
        nb = n.encode('utf-8')
        out += struct.pack('<I', len(nb)) + nb + struct.pack('<Q', len(blob)) + blob
    out += b[RO_START:DATA_END]
    return bytes(out)


def make(main_path, sdk_path, want_sha1=None):
    """-> rtdx_image.bin bytes from the dump's exefs/main and exefs/sdk; want_sha1 = the release's image (ConvertError on a
    mismatch: this dump's executable is not the one the player's native code was rebuilt from)."""
    with open(main_path, 'rb') as f:
        main = nso_flat(f.read())
    with open(sdk_path, 'rb') as f:
        sdk = nso_flat(f.read())
    img = build_image(main, sdk)
    if want_sha1 and hashlib.sha1(img).hexdigest() != want_sha1:
        raise ConvertError('the native data image made from this dump differs from the one the player was built for')
    return img
