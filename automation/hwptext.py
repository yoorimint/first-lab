"""HWP(한글) 공고 파일의 본문 글자 뽑기 — 검수자가 소진공 등 HWP 공고 원문을 읽게 (11/9 #2346: 정부24 기록만 보고 전직장려수당 순서를 틀림). 사용: python3 -I automation/hwptext.py <파일>"""
import olefile, zlib, struct, sys
ole = olefile.OleFileIO(sys.argv[1])
hdr = ole.openstream('FileHeader').read(); compressed = hdr[36] & 1
out = []
for e in sorted(x for x in ole.listdir() if x[0] == 'BodyText'):
    data = ole.openstream(e).read()
    if compressed: data = zlib.decompress(data, -15)
    i = 0
    while i < len(data):
        h = struct.unpack_from('<I', data, i)[0]; tag = h & 0x3ff; size = (h >> 20) & 0xfff; i += 4
        if size == 0xfff: size = struct.unpack_from('<I', data, i)[0]; i += 4
        if tag == 67:
            t = data[i:i+size].decode('utf-16le', 'ignore')
            out.append(''.join(ch for ch in t if ch >= ' ' or ch == '\n'))
        i += size
print('\n'.join(out))
