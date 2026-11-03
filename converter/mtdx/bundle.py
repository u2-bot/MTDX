"""One Switch asset bundle -> the Windows bundle the MTDX player loads.

Measured on the Rescue Team DX dump (2,670 bundles, StreamingAssets/ab) against the Windows bundles of the RTDX PC rebuild:
both are UnityFS bundles of Unity 2018.3.12f1 with serialized-file version 17 and type trees; bundle names and internal file
names (CAB-...) are the same, so references between bundles need no change. What a Windows player needs different:
  - the target platform field of every serialized file (38 Switch -> 19 StandaloneWindows64);
  - texture data (textures.py: ASTC decoded to RGBA32, the rest copied);
  - Shader objects: our Windows build of each one (shaderpack.py), keeping the Switch path ID;
  - a new .resS per serialized file holding the converted texture data and the untouched mesh streams.
Every other object keeps its bytes. Audio (.resource archive entries: FSB5 Vorbis, the same format Unity writes for Windows)
is carried byte for byte. The archive is written LZ4 chunk-compressed, like Unity's own bundles.
"""
import os

import UnityPy
from UnityPy.files.SerializedFile import SerializedFile

from . import shaderpack, textures
from .unityfiles import ConvertError, StreamFile, StreamSource, serialized_files, set_windows

TEXTURE_TYPES = ('Texture2D', 'Cubemap')
STREAM_TYPES = ('Mesh',)
UNHANDLED_STREAM_TYPES = ('VideoClip', 'Texture2DArray', 'Texture3D', 'SparseTexture', 'CubemapArray')


def convert_texture(obj, streams, out_stream, stats):
    tree = obj.read_typetree()
    sd = tree.get('m_StreamData') or {}
    data = streams.read(sd) if sd.get('size') else bytes(tree.get('image data', b'') or b'')
    images = tree.get('m_ImageCount', 1) or 1
    fmt, new, kind = textures.convert(tree['m_TextureFormat'], tree['m_Width'], tree['m_Height'], tree['m_MipCount'], images,
                                      data)
    stats[kind] = stats.get(kind, 0) + 1
    if kind == 'copy':
        # bytes unchanged: only the stream offset moves (the .resS is rebuilt)
        if sd.get('size'):
            tree['m_StreamData'] = dict(sd, offset=out_stream.add(new), size=len(new))
            obj.save_typetree(tree)
        return
    tree['m_TextureFormat'] = fmt
    tree['m_CompleteImageSize'] = len(new) // images
    if sd.get('size'):
        tree['image data'] = b''
        tree['m_StreamData'] = dict(sd, offset=out_stream.add(new), size=len(new))
    else:
        tree['image data'] = new
    obj.save_typetree(tree)


def convert_stream_object(obj, streams, out_stream, stats):
    tree = obj.read_typetree()
    sd = tree.get('m_StreamData') or {}
    if sd.get('size'):
        data = streams.read(sd)
        tree['m_StreamData'] = dict(sd, offset=out_stream.add(data), size=len(data))
        obj.save_typetree(tree)
        stats['streams'] = stats.get('streams', 0) + 1


def convert_serialized(sf, sources, own_res, container, pack, ids, stats, extra=None):
    """Converts one SerializedFile in place. sources = {stream entry name: bytes}; own_res = the name of its .resS.
    extra(obj) -> True when it handled obj (player-file rules). Every byte of the sources must be read by some object (proof
    nothing was dropped). -> {stream entry name: new bytes}."""
    set_windows(sf)
    streams = StreamSource(sources)
    out = StreamFile()
    cache = {}
    for obj in list(sf.objects.values()):
        t = obj.type.name
        if extra is not None and extra(obj):
            continue
        if t in TEXTURE_TYPES:
            convert_texture(obj, streams, out, stats)
        elif t in STREAM_TYPES:
            convert_stream_object(obj, streams, out, stats)
        elif t == 'Shader':
            name = shaderpack.shader_name(obj)
            shaderpack.apply(obj, sf, pack.get(container, name), ids, cache)
            stats['shaders'] = stats.get('shaders', 0) + 1
        elif t in UNHANDLED_STREAM_TYPES:
            raise ConvertError(f'{container}: {t} objects are not handled yet')
    gaps = streams.uncovered()
    if gaps:
        raise ConvertError(f'{container}: stream data no converted object accounts for: {gaps}')
    res = {}
    if own_res in sources or len(out):
        res[own_res] = out.bytes()
    return res


def convert_bundle(src, dst, container, pack, ids):
    """src: Switch bundle path; dst: output path; container: the bundle file name ('shader_pack.ab')."""
    env = UnityPy.load(src)
    bf = env.file
    entries = dict(bf.files)
    sfs = serialized_files(env)
    if not sfs:
        raise ConvertError(f'{container}: no serialized file in the bundle')
    raw = {n: f for n, f in entries.items() if not isinstance(f, SerializedFile)}
    for n in raw:
        if not n.endswith(('.resS', '.resource')):
            raise ConvertError(f'{container}: archive entry {n} is not a .resS / .resource (not handled yet)')
    sources = {n: f.bytes for n, f in raw.items() if n.endswith('.resS')}
    stats = {}
    new_streams = {}
    for name, sf in sfs:
        mine = {n: b for n, b in sources.items() if n == name + '.resS'}
        new_streams.update(convert_serialized(sf, mine, name + '.resS', container, pack, ids, stats))
    leftover = set(sources) - set(new_streams)
    if leftover:
        raise ConvertError(f'{container}: stream entries without their serialized file: {sorted(leftover)}')
    # rebuild the archive in the original entry order; .resource (audio) entries keep their bytes
    from UnityPy.streams import EndianBinaryWriter
    files = {}
    for n, f in entries.items():
        if isinstance(f, SerializedFile):
            files[n] = f
        else:
            w = EndianBinaryWriter()
            w.write_bytes(new_streams[n] if n in new_streams else bytes(f.bytes))
            w.flags = f.flags
            files[n] = w
            if n.endswith('.resource'):
                stats['audio_bytes'] = stats.get('audio_bytes', 0) + len(f.bytes)
    bf.files = files
    data = bf.save(packer='lz4')
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + '.part'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, dst)
    stats['bytes'] = len(data)
    return stats
