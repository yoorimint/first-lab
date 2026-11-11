"""HWP·HWPX(한글) 공고 파일의 본문 글자 뽑기 — 검수자가 소진공 등 HWP 공고 원문을 읽게 (11/9 #2346: 정부24 기록만 보고 전직장려수당 순서를 틀림).
11/11: 보도자료 첨부가 .hwpx(압축 파일 안 XML)라 OLE 로 못 열어 검수자가 손으로 풀었음 → 둘 다 읽음.
사용: python3 -I automation/hwptext.py <파일>"""
import re
import struct
import sys
import zipfile
import zlib


def hwpx_text(path: str) -> str:
    """HWPX: Contents/section*.xml 의 글자(<hp:t>)를 문단(<hp:p>)마다 한 줄로."""
    out = []
    with zipfile.ZipFile(path) as z:
        names = sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n))
        for n in names:
            xml = z.read(n).decode("utf-8", "ignore")
            for para in re.findall(r"<hp:p\b.*?</hp:p>", xml, flags=re.S):
                text = "".join(re.findall(r"<hp:t\b[^>]*>(.*?)</hp:t>", para, flags=re.S))
                text = re.sub(r"<[^>]+>", "", text)
                text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
                if text.strip():
                    out.append(text)
    return "\n".join(out)


def hwp_text(path: str) -> str:
    import olefile
    ole = olefile.OleFileIO(path)
    hdr = ole.openstream('FileHeader').read()
    compressed = hdr[36] & 1
    out = []
    for e in sorted(x for x in ole.listdir() if x[0] == 'BodyText'):
        data = ole.openstream(e).read()
        if compressed:
            data = zlib.decompress(data, -15)
        i = 0
        while i < len(data):
            h = struct.unpack_from('<I', data, i)[0]
            tag = h & 0x3ff
            size = (h >> 20) & 0xfff
            i += 4
            if size == 0xfff:
                size = struct.unpack_from('<I', data, i)[0]
                i += 4
            if tag == 67:
                t = data[i:i + size].decode('utf-16le', 'ignore')
                out.append(''.join(ch for ch in t if ch >= ' ' or ch == '\n'))
            i += size
    return '\n'.join(out)


def text_of(path: str) -> str:
    return hwpx_text(path) if zipfile.is_zipfile(path) else hwp_text(path)


if __name__ == "__main__":
    print(text_of(sys.argv[1]))
