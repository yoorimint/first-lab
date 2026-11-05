"""AI 가 쓴 HTML → 워드프레스 블록 형식.

일반 HTML 로 저장하면 앱 편집기에서 표 등이 '?'(지원 안 되는 블록)로 보인다.
최상위 태그를 블록 주석으로 감싸서 편집기에서 바로 고칠 수 있게 한다.
"""
from __future__ import annotations

import re

_OPEN = re.compile(r"<(h2|h3|h4|p|ul|ol|table|figure|blockquote)\b[^>]*>", re.I)
# 계산기 자리 (wordpress/mu-plugins/first-lab-calculators.php). 문단 안에 두면 <p> 안에 <div> 가 들어가므로 따로 뺀다.
_CALC = re.compile(r"^\s*(\[firstlab_calc\b[^\]]*\])\s*$")


def _end(html: str, start: int, tag: str) -> int:
    """start 에서 열린 <tag> 의 짝이 되는 닫는 태그 끝 위치 (같은 태그가 안에 또 있어도 — 목록 안 목록).
    11/6 #872: '.*?</ul>' 가 안쪽 목록의 </ul> 에서 끊어 남은 '</li></ul>' 이 빈 문단이 되고 목록이 깨짐."""
    pat = re.compile(rf"<(/?){tag}\b[^>]*>", re.I)
    depth = 0
    for m in pat.finditer(html, start):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return m.end()
    return len(html)   # 닫히지 않음 — 끝까지 한 덩어리


def _items(body: str) -> list[str]:
    """목록 안쪽에서 바깥 단계의 <li> 내용만 (안쪽 목록의 li 는 그 항목 안에 남음)."""
    out, pos = [], 0
    li = re.compile(r"<li\b[^>]*>", re.I)
    while (m := li.search(body, pos)):
        end = _end(body, m.start(), "li")
        out.append(re.sub(r"</li>\s*$", "", body[m.end():end], flags=re.I))
        pos = end
    return out


def _list(tag: str, html: str) -> str:
    body = re.sub(rf"^\s*<{tag}\b[^>]*>|</{tag}>\s*$", "", html, flags=re.I)
    inner = "".join(f"<!-- wp:list-item -->\n<li>{_nested(i.strip())}</li>\n<!-- /wp:list-item -->\n" for i in _items(body))
    attrs = ' {"ordered":true}' if tag == "ol" else ""
    return f'<!-- wp:list{attrs} -->\n<{tag} class="wp-block-list">\n{inner}</{tag}>\n<!-- /wp:list -->'


def _nested(item: str) -> str:
    """항목 안의 하위 목록도 목록 블록으로 (편집기가 항목 안 목록을 블록으로 읽음)."""
    m = re.search(r"<(ul|ol)\b[^>]*>", item, re.I)
    if not m:
        return item
    end = _end(item, m.start(), m.group(1).lower())
    return item[:m.start()].rstrip() + "\n" + _list(m.group(1).lower(), item[m.start():end]) + _nested(item[end:])


def _block(tag: str, html: str) -> str:
    tag = tag.lower()
    if tag in ("h2", "h3", "h4"):
        level = int(tag[1])
        attrs = "" if level == 2 else f' {{"level":{level}}}'
        text = re.sub(rf"^<{tag}\b[^>]*>|</{tag}>$", "", html.strip(), flags=re.I)
        return f'<!-- wp:heading{attrs} -->\n<{tag} class="wp-block-heading">{text}</{tag}>\n<!-- /wp:heading -->'
    if tag == "p":
        calc = _CALC.match(re.sub(r"^<p\b[^>]*>|</p>$", "", html.strip(), flags=re.I))
        if calc:
            return f"<!-- wp:shortcode -->\n{calc.group(1)}\n<!-- /wp:shortcode -->"
        return f"<!-- wp:paragraph -->\n{html.strip()}\n<!-- /wp:paragraph -->"
    if tag in ("ul", "ol"):
        return _list(tag, html)
    if tag == "table":  # 최신 워드프레스 표 블록 기본값(고정 레이아웃)과 맞춤
        table = re.sub(r"^<table\b[^>]*>", '<table class="has-fixed-layout">', html.strip(), flags=re.I)
        return f'<!-- wp:table -->\n<figure class="wp-block-table">{table}</figure>\n<!-- /wp:table -->'
    if tag == "blockquote":
        return f'<!-- wp:quote -->\n<blockquote class="wp-block-quote">{html.strip()[12:-13]}</blockquote>\n<!-- /wp:quote -->'
    return f"<!-- wp:html -->\n{html.strip()}\n<!-- /wp:html -->"


def to_blocks(html: str) -> str:
    if "<!-- wp:" in html:
        return html  # 이미 블록 형식
    parts, pos = [], 0
    while (m := _OPEN.search(html, pos)):
        tag = m.group(1).lower()
        end = _end(html, m.start(), tag)
        parts.extend(_loose(html[pos:m.start()]))
        parts.append(_block(tag, html[m.start():end]))
        pos = end
    parts.extend(_loose(html[pos:]))
    return "\n\n".join(parts)


_BLOCK = re.compile(r"<!-- wp:(?P<kind>[a-z-]+)(?: [^>]*)? -->\n?(?P<body>.*?)\n?<!-- /wp:(?P=kind) -->", re.S)
_BROKEN_START = re.compile(r"<!-- wp:list(?: [^>]*)? -->(?:(?!<!-- /wp:list -->).)*<!-- /wp:list -->\s*<!-- wp:paragraph -->\n?<p>\s*</li>", re.S)


def _raw(block: str) -> str:
    """블록 주석을 걷어낸 HTML (문단은 <p> 겉 태그도 걷음 — 깨진 조각이 문단으로 감싸여 있음)."""
    inner = re.sub(r"<!-- /?wp:[^>]*-->\n?", "", block)
    return re.sub(r"^\s*<p>|</p>\s*$", "", inner) if block.startswith("<!-- wp:paragraph") else inner


def _balanced(html: str) -> bool:
    return all(len(re.findall(rf"<{t}\b", html, re.I)) == len(re.findall(rf"</{t}>", html, re.I)) for t in ("ul", "ol", "li"))


def repair_lists(content: str) -> tuple[str, int]:
    """예전 변환(11/6 이전)이 깨뜨린 목록 안 목록 되살리기: 목록 블록 + '</li>…' 로 시작하는 문단 조각들을
    짝이 맞을 때까지 이어 붙여 다시 변환. 짝이 끝내 안 맞으면 그대로 둔다 (확실하지 않으면 안 바꿈)."""
    fixed = 0
    while (m := _BROKEN_START.search(content)):
        raw, pos = "", m.start()
        while (b := _BLOCK.match(content, pos)) and not (raw and _balanced(raw)):
            raw += _raw(b.group(0))
            pos = b.end()
            pos += len(content[pos:]) - len(content[pos:].lstrip())
        if not _balanced(raw):
            break
        tag = re.match(r"\s*<(ul|ol)", raw, re.I)
        if not tag:
            break
        content = content[:m.start()] + _list(tag.group(1).lower(), raw.strip()) + "\n\n" + content[pos:]
        fixed += 1
    return content, fixed


def _loose(text: str) -> list[str]:
    """태그 밖 텍스트 → 문단 (계산기 자리는 문단으로 감싸지 않음)."""
    text = text.strip()
    if not text:
        return []
    return [_block("p", f"<p>{text}</p>")]
