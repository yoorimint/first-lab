"""터미널 표 출력용: 한글(전각) 폭을 고려한 칸 맞춤."""
import unicodedata


def width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def ljust(text: str, size: int) -> str:
    return text + " " * max(0, size - width(text))


def rjust(text: str, size: int) -> str:
    return " " * max(0, size - width(text)) + text
