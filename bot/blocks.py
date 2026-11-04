"""AI 가 쓴 HTML → 워드프레스 블록 형식.

일반 HTML 로 저장하면 앱 편집기에서 표 등이 '?'(지원 안 되는 블록)로 보인다.
최상위 태그를 블록 주석으로 감싸서 편집기에서 바로 고칠 수 있게 한다.
"""
from __future__ import annotations

import re

_TOP = re.compile(r"<(h2|h3|h4|p|ul|ol|table|figure|blockquote)\b[^>]*>.*?</\1>", re.S | re.I)
# 계산기 자리 (wordpress/mu-plugins/first-lab-calculators.php). 문단 안에 두면 <p> 안에 <div> 가 들어가므로 따로 뺀다.
_CALC = re.compile(r"^\s*(\[firstlab_calc\b[^\]]*\])\s*$")


def _list(tag: str, html: str) -> str:
    items = re.findall(r"<li\b[^>]*>(.*?)</li>", html, re.S | re.I)
    inner = "".join(f"<!-- wp:list-item -->\n<li>{i.strip()}</li>\n<!-- /wp:list-item -->\n" for i in items)
    attrs = ' {"ordered":true}' if tag == "ol" else ""
    return f'<!-- wp:list{attrs} -->\n<{tag} class="wp-block-list">\n{inner}</{tag}>\n<!-- /wp:list -->'


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
    for m in _TOP.finditer(html):
        parts.extend(_loose(html[pos:m.start()]))
        parts.append(_block(m.group(1), m.group(0)))
        pos = m.end()
    parts.extend(_loose(html[pos:]))
    return "\n\n".join(parts)


def _loose(text: str) -> list[str]:
    """태그 밖 텍스트 → 문단 (계산기 자리는 문단으로 감싸지 않음)."""
    text = text.strip()
    if not text:
        return []
    return [_block("p", f"<p>{text}</p>")]
