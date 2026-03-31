import hashlib
from enum import Enum
from pathlib import Path

import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from models.paystub import Paystub


PAGE_WIDTH, PAGE_HEIGHT = LETTER
PRINT_SAFE_MARGIN = 24

BLACK = colors.black
DARK = colors.HexColor("#222222")
MID = colors.HexColor("#D9D9D9")
LIGHT = colors.HexColor("#F1F1F1")
LINE = colors.HexColor("#B8B8B8")
WHITE = colors.white
CHARCOAL = colors.HexColor("#111111")
MUTED = colors.HexColor("#5C5C5C")
BORDER = colors.HexColor("#CFCFCF")
PANEL = colors.HexColor("#F6F6F4")
PANEL_ALT = colors.HexColor("#ECECE8")
PANEL_DARK = colors.HexColor("#1B1B1B")
SUCCESS_PANEL = colors.HexColor("#EEF3EE")
WARNING_PANEL = colors.HexColor("#F4EEEE")

ADP_BLUE = colors.HexColor("#114a8b")
ADP_LIGHT = colors.HexColor("#eaf2fb")
ADP_BORDER = colors.HexColor("#9bb9d8")
SOFT_GREEN = colors.HexColor("#eff7f1")
SOFT_RED = colors.HexColor("#fbefef")
SOFT_GRAY = colors.HexColor("#f7f7f7")
INK = colors.HexColor("#161616")
TEXT = colors.HexColor("#222222")
TEXT_MUTED = colors.HexColor("#666662")
GRID = colors.HexColor("#B7B7B2")
GRID_STRONG = colors.HexColor("#91918B")
PAPER = colors.HexColor("#FBFBF8")
SURFACE = colors.HexColor("#F3F3EF")
SURFACE_ALT = colors.HexColor("#ECECE7")
SURFACE_DEEP = colors.HexColor("#E3E3DC")
SURFACE_SOFT = colors.HexColor("#F7F7F4")


class PaystubTemplate(str, Enum):
    ADP = "adp"
    SIMPLE = "simple"
    DETACHED_CHECK = "detached_check"


FONT_REGULAR = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
ADP_LOGO_PATH = Path(__file__).resolve().parents[1] / "assets" / "branding" / "adp-logo.png"
ADP_LOGO_READER: ImageReader | None = None


# -- Number formatting helpers -------------------------------------------------

def num(value: float) -> str:
    return f"{value:,.2f}"


def money(value: float) -> str:
    return f"${value:,.2f}"


def money_display(value: float) -> str:
    return f"$ {value:,.2f}"


def neg(value: float) -> str:
    if value == 0:
        return ""
    return f"-{value:,.2f}"


def amount_to_words(amount: float) -> str:
    _ones = [
        "", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE",
        "TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", "SIXTEEN",
        "SEVENTEEN", "EIGHTEEN", "NINETEEN",
    ]
    _tens = ["", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY", "NINETY"]

    def say_below_100(n: int) -> str:
        if n == 0:
            return ""
        if n < 20:
            return _ones[n]
        t = _tens[n // 10]
        o = _ones[n % 10]
        return f"{t}-{o}" if o else t

    def say_below_1000(n: int) -> str:
        if n == 0:
            return ""
        if n < 100:
            return say_below_100(n)
        h = _ones[n // 100] + " HUNDRED"
        rest = say_below_100(n % 100)
        return f"{h} {rest}".strip() if rest else h

    rounded_amount = round(amount + 1e-9, 2)
    dollars = int(rounded_amount)
    cents = int(round((rounded_amount - dollars) * 100))
    if cents == 100:
        dollars += 1
        cents = 0

    if dollars == 0:
        words = "ZERO"
    else:
        parts = []
        rem = dollars
        if rem >= 1_000_000:
            parts.append(say_below_1000(rem // 1_000_000) + " MILLION")
            rem %= 1_000_000
        if rem >= 1_000:
            parts.append(say_below_1000(rem // 1_000) + " THOUSAND")
            rem %= 1_000
        if rem > 0:
            parts.append(say_below_1000(rem))
        words = " ".join(parts)

    return f"{words} AND {cents:02d}/100 DOLLARS ONLY"


# -- Drawing primitives --------------------------------------------------------


def _register_font_family() -> None:
    global FONT_REGULAR, FONT_BOLD

    regular_name = "PaystubSans"
    bold_name = "PaystubSans-Bold"
    if regular_name in pdfmetrics.getRegisteredFontNames() and bold_name in pdfmetrics.getRegisteredFontNames():
        FONT_REGULAR = regular_name
        FONT_BOLD = bold_name
        return

    fonts_dir = Path(reportlab.__file__).resolve().parent / "fonts"
    regular_path = fonts_dir / "Vera.ttf"
    bold_path = fonts_dir / "VeraBd.ttf"

    if regular_path.exists() and bold_path.exists():
        pdfmetrics.registerFont(TTFont(regular_name, str(regular_path)))
        pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
        FONT_REGULAR = regular_name
        FONT_BOLD = bold_name


def _font_name(bold: bool = False) -> str:
    _register_font_family()
    return FONT_BOLD if bold else FONT_REGULAR


def _get_adp_logo() -> ImageReader | None:
    global ADP_LOGO_READER
    if ADP_LOGO_READER is not None:
        return ADP_LOGO_READER
    if not ADP_LOGO_PATH.exists():
        return None
    try:
        ADP_LOGO_READER = ImageReader(str(ADP_LOGO_PATH))
    except Exception:
        ADP_LOGO_READER = None
    return ADP_LOGO_READER


def _company_code(paystub: Paystub) -> str:
    parts = [part[:1] for part in str(paystub.company_name or "").split() if part]
    return ("".join(parts)[:3] or "PAY").upper()


def _barcode_digits(paystub: Paystub) -> str:
    seed = f"{paystub.employee_id}|{paystub.pay_date}|{paystub.payroll_check_number}|{paystub.net_pay_current}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    digits = "".join(str(int(char, 16) % 10) for char in digest)
    return digits[:12]


def _format_address(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "\n" in text:
        return text
    return "\n".join(part.strip() for part in text.split(",") if part.strip())


def _address_lines(value: str, *, max_lines: int | None = None) -> list[str]:
    lines = [line.strip() for line in _format_address(value).splitlines() if line.strip()]
    if max_lines is not None:
        lines = lines[:max_lines]
    return lines


def _masked_account(value: str) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if not digits:
        return ""
    return f"XXXX{digits[-4:]}"


def _coerced_font_size(size: float) -> float:
    return round(float(size), 2)


def ellipsize_text_to_width(c, text, width, size=8, bold=False) -> str:
    value = str(text)
    if width <= 0:
        return ""
    if text_width(c, value, size=size, bold=bold) <= width:
        return value

    ellipsis = "..."
    trimmed = value
    while trimmed and text_width(c, f"{trimmed}{ellipsis}", size=size, bold=bold) > width:
        trimmed = trimmed[:-1]
    return f"{trimmed.rstrip()}{ellipsis}" if trimmed else ellipsis

def draw_text(c, x, y, text, size=9, bold=False, color=BLACK):
    c.setFont(_font_name(bold), _coerced_font_size(size))
    c.setFillColor(color)
    c.drawString(x, y, str(text))
    c.setFillColor(BLACK)


def draw_right(c, x, y, text, size=9, bold=False, color=BLACK):
    c.setFont(_font_name(bold), _coerced_font_size(size))
    c.setFillColor(color)
    c.drawRightString(x, y, str(text))
    c.setFillColor(BLACK)


def draw_center(c, x, y, text, size=9, bold=False, color=BLACK):
    c.setFont(_font_name(bold), _coerced_font_size(size))
    c.setFillColor(color)
    c.drawCentredString(x, y, str(text))
    c.setFillColor(BLACK)


def text_width(c, text, size=9, bold=False) -> float:
    return c.stringWidth(str(text), _font_name(bold), _coerced_font_size(size))


def draw_fit_text(c, x, y, width, text, size=9, min_size=6, bold=False, color=BLACK, align="left"):
    font_size = float(size)
    while font_size > min_size and text_width(c, text, size=font_size, bold=bold) > width:
        font_size -= 0.5
    final_text = ellipsize_text_to_width(c, text, width, size=font_size, bold=bold)

    if align == "right":
        draw_right(c, x, y, final_text, size=font_size, bold=bold, color=color)
    elif align == "center":
        draw_center(c, x, y, final_text, size=font_size, bold=bold, color=color)
    else:
        draw_text(c, x, y, final_text, size=font_size, bold=bold, color=color)
    return font_size


def draw_box(c, x, y, w, h, fill=None, stroke=BLACK, lw=0.8):
    c.setLineWidth(lw)
    c.setStrokeColor(stroke)
    if fill is not None:
        c.setFillColor(fill)
        c.rect(x, y, w, h, fill=1, stroke=1)
        c.setFillColor(BLACK)
    else:
        c.rect(x, y, w, h, fill=0, stroke=1)


def fill_rect(c, x, y, w, h, fill):
    c.saveState()
    c.setFillColor(fill)
    c.rect(x, y, w, h, fill=1, stroke=0)
    c.restoreState()


def draw_round_box(c, x, y, w, h, radius=10, fill=None, stroke=BLACK, lw=0.8):
    c.setLineWidth(lw)
    c.setStrokeColor(stroke)
    if fill is not None:
        c.setFillColor(fill)
        c.roundRect(x, y, w, h, radius, fill=1, stroke=1)
        c.setFillColor(BLACK)
    else:
        c.roundRect(x, y, w, h, radius, fill=0, stroke=1)


def draw_rule(c, x1, y1, x2, y2, color=LINE, lw=0.5):
    c.setStrokeColor(color)
    c.setLineWidth(lw)
    c.line(x1, y1, x2, y2)
    c.setStrokeColor(BLACK)


def section_header(c, x, y, w, title, fill=MID, text_color=BLACK):
    h = 16
    draw_box(c, x, y - h, w, h, fill=fill, stroke=fill, lw=0.6)
    draw_text(c, x + 6, y - 11, title, size=8, bold=True, color=text_color)
    return y - h


def sub_section_header(c, x, y, w, title):
    h = 12
    draw_box(c, x, y - h, w, h, fill=PANEL_ALT, stroke=BORDER, lw=0.4)
    draw_text(c, x + 6, y - 9, title, size=7, bold=True, color=DARK)
    return y - h


def wrap_block_lines(c, text, width, size=8, bold=False, max_lines: int | None = None) -> list[str]:
    paragraphs = [line.strip() for line in str(text).splitlines() if line.strip()]
    if not paragraphs:
        paragraphs = [str(text).strip()]

    lines: list[str] = []
    truncated = False
    for paragraph in paragraphs:
        lines.extend(wrap_text_lines(c, paragraph, width, size=size, bold=bold))
        if max_lines is not None and len(lines) >= max_lines:
            truncated = True
            break

    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    if truncated and max_lines is not None and lines:
        lines[-1] = ellipsize_text_to_width(c, lines[-1], width, size=size, bold=bold)
    return lines


def draw_address_block(c, x, y, text, size=8, leading=10, bold=False, color=BLACK, width=None, max_lines: int | None = None):
    lines = (
        wrap_block_lines(c, text, width, size=size, bold=bold, max_lines=max_lines)
        if width is not None
        else [str(line) for line in str(text).splitlines()]
    )
    for line in lines:
        draw_text(c, x, y, line, size=size, bold=bold, color=color)
        y -= leading
    return y


def draw_simple_rows(c, x, y, w, rows, col_positions, row_h=12, size=8, last_bold_labels=None):
    if last_bold_labels is None:
        last_bold_labels = set()
    label_width = max(col_positions[1] - col_positions[0] - 8, 24)
    for row in rows:
        label = row[0]
        values = row[1:]
        draw_rule(c, x, y - 2, x + w, y - 2)
        draw_fit_text(
            c,
            x + col_positions[0],
            y,
            label_width,
            label,
            size=size,
            min_size=max(size - 1.0, 6),
            bold=label in last_bold_labels,
            color=DARK,
        )
        for idx, value in enumerate(values, start=1):
            if value:
                draw_right(
                    c,
                    x + col_positions[idx],
                    y,
                    value,
                    size=size,
                    bold=label in last_bold_labels,
                )
        y -= row_h
    return y


def draw_label_value(c, x, y, label, value, label_size=7, value_size=8, color=DARK):
    draw_text(c, x, y, label, size=label_size, color=color)
    draw_text(c, x, y - 10, value, size=value_size, bold=True)


def draw_summary_box(c, x, y, w, h, title, value, fill):
    dark_fill = fill in {CHARCOAL, PANEL_DARK, ADP_BLUE}
    label_color = WHITE if dark_fill else MUTED
    value_color = WHITE if dark_fill else CHARCOAL
    stroke = CHARCOAL if dark_fill else BORDER
    draw_round_box(c, x, y - h, w, h, radius=12, fill=fill, stroke=stroke, lw=0.6)
    draw_text(c, x + 10, y - 16, title, size=7, bold=True, color=label_color)
    draw_fit_text(
        c,
        x + w - 10,
        y - 35,
        w - 20,
        value,
        size=15,
        min_size=11,
        bold=True,
        color=value_color,
        align="right",
    )


def wrap_text_lines(c, text, width, size=8, bold=False, max_lines: int | None = None) -> list[str]:
    lines: list[str] = []
    paragraphs = [paragraph.strip() for paragraph in str(text).splitlines() if paragraph.strip()]
    if not paragraphs:
        paragraphs = [str(text).strip()]

    for paragraph in paragraphs:
        words = [
            ellipsize_text_to_width(c, word, width, size=size, bold=bold)
            if text_width(c, word, size=size, bold=bold) > width
            else word
            for word in paragraph.split()
        ]
        if not words:
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if text_width(c, candidate, size=size, bold=bold) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)

    if not lines:
        return []

    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines and text_width(c, f"{lines[-1]}...", size=size, bold=bold) > width:
            parts = lines[-1].split()
            if len(parts) <= 1:
                break
            lines[-1] = " ".join(parts[:-1])
        if lines:
            lines[-1] = f"{lines[-1]}..."

    return lines


def draw_wrapped_text(
    c,
    x,
    y,
    width,
    text,
    size=8,
    bold=False,
    color=BLACK,
    leading=10,
    max_lines: int | None = None,
):
    lines = wrap_text_lines(c, text, width, size=size, bold=bold, max_lines=max_lines)
    if not lines:
        return y

    cursor = y
    for line in lines:
        draw_text(c, x, cursor, line, size=size, bold=bold, color=color)
        cursor -= leading
    return cursor


def draw_info_card(
    c,
    x,
    top,
    w,
    h,
    label,
    value,
    secondary: str | None = None,
    *,
    fill=WHITE,
    stroke=BORDER,
    label_color=MUTED,
    value_color=CHARCOAL,
    value_size=9,
    max_value_lines=2,
):
    draw_round_box(c, x, top - h, w, h, radius=12, fill=fill, stroke=stroke, lw=0.6)
    draw_text(c, x + 10, top - 14, label, size=6.5, bold=True, color=label_color)
    cursor = draw_wrapped_text(
        c,
        x + 10,
        top - 30,
        w - 20,
        value,
        size=value_size,
        bold=True,
        color=value_color,
        leading=value_size + 2,
        max_lines=max_value_lines,
    )
    if secondary:
        draw_wrapped_text(
            c,
            x + 10,
            min(cursor - 3, top - h + 15),
            w - 20,
            secondary,
            size=7,
            color=MUTED,
            leading=9,
            max_lines=2,
        )


def draw_note_panel(c, x, top, w, h, title, lines, *, fill=PANEL, stroke=BORDER, max_lines=6):
    draw_round_box(c, x, top - h, w, h, radius=12, fill=fill, stroke=stroke, lw=0.6)
    draw_text(c, x + 10, top - 14, title, size=7, bold=True, color=MUTED)
    cursor = top - 30
    wrapped_lines: list[str] = []
    truncated = False
    if lines:
        for line in lines:
            wrapped_lines.extend(wrap_text_lines(c, line, w - 20, size=7.5, max_lines=2))
            if len(wrapped_lines) >= max_lines:
                truncated = True
                break
    else:
        wrapped_lines = ["No additional notes."]

    if len(wrapped_lines) > max_lines:
        wrapped_lines = wrapped_lines[:max_lines]
        truncated = True
    if truncated and wrapped_lines:
        wrapped_lines[-1] = ellipsize_text_to_width(c, wrapped_lines[-1], w - 20, size=7.5)

    for line in wrapped_lines:
        draw_text(c, x + 10, cursor, line, size=7.5, color=DARK)
        cursor -= 10
        if cursor < top - h + 10:
            break


def draw_form_field(
    c,
    x,
    top,
    w,
    h,
    label,
    value,
    secondary: str | None = None,
    *,
    fill=WHITE,
    stroke=GRID_STRONG,
    label_color=TEXT_MUTED,
    value_color=TEXT,
    value_size=8.2,
    secondary_size=6.6,
    max_value_lines=2,
    max_secondary_lines=2,
):
    draw_box(c, x, top - h, w, h, fill=fill, stroke=stroke, lw=0.55)
    draw_text(c, x + 7, top - 12, label.upper(), size=6.1, bold=True, color=label_color)
    cursor = draw_wrapped_text(
        c,
        x + 7,
        top - 25,
        w - 14,
        value,
        size=value_size,
        bold=True,
        color=value_color,
        leading=value_size + 1.8,
        max_lines=max_value_lines,
    )
    if secondary:
        draw_wrapped_text(
            c,
            x + 7,
            max(top - h + 11, cursor - 3),
            w - 14,
            secondary,
            size=secondary_size,
            color=TEXT_MUTED,
            leading=secondary_size + 1.8,
            max_lines=max_secondary_lines,
        )


def draw_form_summary_cell(c, x, top, w, h, label, value, *, dark=False):
    fill = INK if dark else SURFACE
    stroke = INK if dark else GRID_STRONG
    label_color = PAPER if dark else TEXT_MUTED
    value_color = PAPER if dark else TEXT
    draw_box(c, x, top - h, w, h, fill=fill, stroke=stroke, lw=0.55)
    draw_text(c, x + 7, top - 11, label.upper(), size=6.1, bold=True, color=label_color)
    draw_fit_text(
        c,
        x + w - 7,
        top - 29,
        w - 14,
        value,
        size=13.5,
        min_size=10,
        bold=True,
        color=value_color,
        align="right",
    )


def draw_monogram_badge(c, x, y, w, h, text="PG", *, fill=WHITE, stroke=GRID_STRONG, color=TEXT):
    draw_box(c, x, y, w, h, fill=fill, stroke=stroke, lw=0.55)
    draw_center(c, x + (w / 2), y + h * 0.28, text, size=max(8, h * 0.58), bold=True, color=color)


def draw_logo_or_badge(c, x, y, w, h, *, text="PG", preserve_aspect=True):
    logo = _get_adp_logo()
    if logo is not None:
        c.drawImage(logo, x, y, width=w, height=h, mask="auto", preserveAspectRatio=preserve_aspect, anchor="c")
    return


def draw_form_note_panel(
    c,
    x,
    top,
    w,
    h,
    title,
    lines,
    *,
    fill=WHITE,
    stroke=GRID_STRONG,
    max_lines: int | None = None,
):
    draw_box(c, x, top - h, w, h, fill=fill, stroke=stroke, lw=0.55)
    fill_rect(c, x, top - 15, w, 15, SURFACE_ALT)
    draw_rule(c, x, top - 15, x + w, top - 15, color=GRID_STRONG, lw=0.4)
    draw_text(c, x + 7, top - 10, title.upper(), size=6.2, bold=True, color=TEXT)

    body_top = top - 23
    body_bottom = top - h + 8
    line_gap = 9
    rule_y = body_top - 5
    while rule_y > body_bottom:
        draw_rule(c, x + 7, rule_y, x + w - 7, rule_y, color=SURFACE_ALT, lw=0.35)
        rule_y -= line_gap

    available_lines = max(1, int((body_top - body_bottom) / line_gap))
    if max_lines is not None:
        available_lines = min(available_lines, max_lines)

    content_lines: list[str] = []
    source_lines = lines or ["No additional notes."]
    truncated = False
    for line in source_lines:
        wrapped = wrap_text_lines(c, line, w - 14, size=7.0)
        for wrapped_line in wrapped:
            content_lines.append(wrapped_line)
            if len(content_lines) >= available_lines:
                truncated = True
                break
        if truncated:
            break

    if truncated and content_lines:
        content_lines[-1] = ellipsize_text_to_width(c, content_lines[-1], w - 14, size=7.0)

    text_y = body_top - 1
    for line in content_lines[:available_lines]:
        draw_text(c, x + 7, text_y, line, size=7.0, color=TEXT)
        text_y -= line_gap


def draw_form_table(
    c,
    x,
    top,
    w,
    title,
    headers,
    rows,
    *,
    column_widths: tuple[float, ...] | None = None,
    emphasize_labels: set[str] | None = None,
    first_col_max_lines=2,
    title_fill=SURFACE_DEEP,
    zebra_fill=SURFACE_SOFT,
    placeholder="None reported",
    value_size=7.0,
    header_size=6.1,
):
    if emphasize_labels is None:
        emphasize_labels = set()
    if not rows:
        rows = [(placeholder,) + ("",) * (len(headers) - 1)]

    normalized_rows = [tuple("" if cell is None else str(cell) for cell in row) for row in rows]
    if column_widths is None:
        column_widths = tuple(1 for _ in headers)
    scale = w / float(sum(column_widths))
    widths = [segment * scale for segment in column_widths]

    row_layouts: list[tuple[tuple[str, ...], list[str], float]] = []
    for row in normalized_rows:
        label_lines = wrap_text_lines(
            c,
            row[0],
            max(widths[0] - 10, 24),
            size=value_size,
            max_lines=first_col_max_lines,
        ) or [""]
        row_h = max(16.0, len(label_lines) * (value_size + 1.2) + 5)
        row_layouts.append((row, label_lines, row_h))

    title_h = 15
    header_h = 16
    body_h = sum(layout[2] for layout in row_layouts)
    total_h = title_h + header_h + body_h
    bottom = top - total_h

    draw_box(c, x, bottom, w, total_h, fill=WHITE, stroke=GRID_STRONG, lw=0.55)
    fill_rect(c, x, top - title_h, w, title_h, title_fill)
    fill_rect(c, x, top - title_h - header_h, w, header_h, PAPER)
    draw_rule(c, x, top - title_h, x + w, top - title_h, color=GRID_STRONG, lw=0.4)
    draw_rule(c, x, top - title_h - header_h, x + w, top - title_h - header_h, color=GRID, lw=0.35)
    draw_text(c, x + 7, top - 10, title.upper(), size=6.2, bold=True, color=TEXT)

    column_edges = [x]
    cursor_x = x
    for width in widths:
        cursor_x += width
        column_edges.append(cursor_x)
    for edge in column_edges[1:-1]:
        draw_rule(c, edge, top - title_h, edge, bottom, color=GRID, lw=0.3)

    header_y = top - title_h - 10
    for idx, header in enumerate(headers):
        if idx == 0:
            draw_fit_text(
                c,
                x + 7,
                header_y,
                widths[idx] - 10,
                header,
                size=header_size,
                min_size=5.6,
                bold=True,
                color=TEXT_MUTED,
            )
        else:
            draw_fit_text(
                c,
                column_edges[idx + 1] - 7,
                header_y,
                widths[idx] - 10,
                header,
                size=header_size,
                min_size=5.6,
                bold=True,
                color=TEXT_MUTED,
                align="right",
            )

    cursor_y = top - title_h - header_h
    for index, (row, label_lines, row_h) in enumerate(row_layouts):
        row_bottom = cursor_y - row_h
        if zebra_fill is not None and index % 2 == 0:
            fill_rect(c, x, row_bottom, w, row_h, zebra_fill)
        draw_rule(c, x, row_bottom, x + w, row_bottom, color=GRID, lw=0.3)

        label_y = cursor_y - 9
        row_bold = row[0] in emphasize_labels
        for label_line in label_lines:
            draw_text(c, x + 7, label_y, label_line, size=value_size, bold=row_bold, color=TEXT)
            label_y -= value_size + 1.2

        value_y = row_bottom + (row_h / 2) - (value_size * 0.33)
        for cell_index, cell in enumerate(row[1:], start=1):
            if cell:
                draw_right(
                    c,
                    column_edges[cell_index + 1] - 7,
                    value_y,
                    cell,
                    size=value_size,
                    bold=row_bold,
                    color=TEXT,
                )
        cursor_y = row_bottom

    return bottom


def draw_compact_table(c, x, y, w, title, headers, rows, fill=SOFT_GRAY):
    current_y = section_header(c, x, y, w, title, fill=fill)
    col_count = len(headers)
    spacing = w / max(col_count, 1)
    draw_box(c, x, current_y - 16, w, 16, fill=WHITE, stroke=BORDER, lw=0.35)
    for idx, header in enumerate(headers):
        if idx == 0:
            draw_fit_text(c, x + 8, current_y - 11, spacing - 16, header, size=7, min_size=6, bold=True, color=MUTED)
        else:
            draw_fit_text(
                c,
                x + spacing * (idx + 1) - 8,
                current_y - 11,
                spacing - 16,
                header,
                size=7,
                min_size=6,
                bold=True,
                color=MUTED,
                align="right",
            )
    draw_rule(c, x, current_y - 16, x + w, current_y - 16, color=BORDER, lw=0.5)
    current_y -= 27

    for index, row in enumerate(rows):
        if index % 2 == 0:
            draw_box(c, x, current_y - 8, w, 12, fill=WHITE, stroke=WHITE, lw=0)
        for idx, cell in enumerate(row):
            if idx == 0:
                draw_fit_text(c, x + 8, current_y, spacing - 16, cell, size=7, min_size=6, color=DARK)
            else:
                draw_right(c, x + spacing * (idx + 1) - 8, current_y, cell, size=7)
        draw_rule(c, x, current_y - 4, x + w, current_y - 4, color=BORDER, lw=0.35)
        current_y -= 13

    return current_y


def _coerce_template(template: PaystubTemplate | str) -> PaystubTemplate:
    if isinstance(template, PaystubTemplate):
        return template

    normalized = str(template).strip().lower().replace("-", "_")
    aliases = {
        "adp": PaystubTemplate.ADP,
        "adp_like": PaystubTemplate.ADP,
        "simple": PaystubTemplate.SIMPLE,
        "stub": PaystubTemplate.SIMPLE,
        "detached_check": PaystubTemplate.DETACHED_CHECK,
        "check": PaystubTemplate.DETACHED_CHECK,
        "detached": PaystubTemplate.DETACHED_CHECK,
    }
    if normalized not in aliases:
        raise ValueError(f"Unknown paystub template: {template}")
    return aliases[normalized]


def _table_rows_for_earnings(paystub: Paystub) -> list[tuple[str, str, str, str, str]]:
    rows = []
    for item in paystub.earnings:
        if item.current == 0 and item.ytd == 0:
            continue
        rows.append(
            (
                item.label,
                num(item.rate) if item.rate else "",
                num(item.hours) if item.hours else "",
                num(item.current),
                num(item.ytd),
            )
        )
    rows.append(("Gross Pay", "", "", money_display(paystub.gross_pay_current), num(paystub.gross_pay_ytd)))
    return rows


def _table_rows_for_taxes(paystub: Paystub) -> list[tuple[str, str, str]]:
    rows = [(item.label, neg(item.current), num(item.ytd)) for item in paystub.taxes]
    rows.append(("Total Taxes", neg(paystub.total_taxes_current), num(paystub.total_taxes_ytd)))
    return rows


def _table_rows_for_deductions(paystub: Paystub) -> list[tuple[str, str, str]]:
    rows = [(item.label, neg(item.current), num(item.ytd)) for item in paystub.deductions]
    if paystub.adjustments:
        rows.extend(
            (
                item.label,
                f"+{item.current:,.2f}" if item.current > 0 else neg(abs(item.current)),
                num(item.ytd) if item.ytd else "",
            )
            for item in paystub.adjustments
        )
    rows.append(("Total Dedns", neg(paystub.total_deductions_current), num(paystub.total_deductions_ytd)))
    return rows


def _table_rows_for_benefits(paystub: Paystub) -> list[tuple[str, str, str]]:
    return [
        (
            item.label,
            num(item.current) if item.current else "",
            num(item.ytd) if item.ytd else "",
        )
        for item in paystub.other_benefits
    ]


def _render_simple_stub(c: canvas.Canvas, paystub: Paystub) -> None:
    margin = PRINT_SAFE_MARGIN + 4
    width = PAGE_WIDTH - margin * 2
    page_top = PAGE_HEIGHT - margin
    footer_y = margin + 12

    draw_box(c, margin, margin, width, PAGE_HEIGHT - margin * 2, fill=PAPER, stroke=GRID_STRONG, lw=0.7)
    fill_rect(c, margin, page_top - 5, width, 5, INK)

    header_h = 58
    net_w = 166
    draw_box(c, margin, page_top - header_h, width, header_h, fill=WHITE, stroke=GRID_STRONG, lw=0.55)
    draw_text(c, margin + 10, page_top - 15, "PAYROLL STATEMENT", size=6.2, bold=True, color=TEXT_MUTED)
    draw_fit_text(c, margin + 10, page_top - 31, width - net_w - 30, paystub.company_name.upper(), size=15.5, min_size=10.5, bold=True, color=INK)
    draw_wrapped_text(c, margin + 10, page_top - 44, width - net_w - 30, _format_address(paystub.company_address), size=6.6, color=TEXT_MUTED, leading=8, max_lines=2)
    draw_box(c, margin + width - net_w - 10, page_top - header_h + 8, net_w, header_h - 16, fill=INK, stroke=INK, lw=0.55)
    draw_text(c, margin + width - net_w, page_top - 17, "NET PAY", size=6.1, bold=True, color=PAPER)
    draw_fit_text(c, margin + width - 18, page_top - 38, net_w - 16, money(paystub.net_pay_current), size=19, min_size=13, bold=True, color=PAPER, align="right")

    info_top = page_top - header_h - 10
    info_gap = 8
    info_w = (width - info_gap * 3) / 4
    draw_form_field(c, margin, info_top, info_w, 44, "Employee", paystub.employee_name.upper(), max_value_lines=2)
    draw_form_field(c, margin + info_w + info_gap, info_top, info_w, 44, "Employee ID", paystub.employee_id, max_value_lines=1)
    draw_form_field(c, margin + (info_w + info_gap) * 2, info_top, info_w, 44, "Pay Date", paystub.pay_date, max_value_lines=1)
    draw_form_field(
        c,
        margin + (info_w + info_gap) * 3,
        info_top,
        info_w,
        44,
        "Pay Period",
        f"{paystub.pay_period_start} to {paystub.pay_period_end}",
        secondary=f"Check no. {paystub.payroll_check_number or 'N/A'}",
        value_size=7.6,
        secondary_size=6.4,
    )

    address_top = info_top - 52
    address_w = (width - 8) / 2
    draw_form_field(c, margin, address_top, address_w, 50, "Employee Address", _format_address(paystub.employee_address) or "Not provided", value_size=7.4)
    draw_form_field(c, margin + address_w + 8, address_top, address_w, 50, "Company Address", _format_address(paystub.company_address), value_size=7.4)

    summary_top = address_top - 58
    summary_w = (width - 12) / 4
    draw_form_summary_cell(c, margin, summary_top, summary_w, 34, "Gross Pay", money_display(paystub.gross_pay_current))
    draw_form_summary_cell(c, margin + summary_w + 4, summary_top, summary_w, 34, "Taxes", neg(paystub.total_taxes_current))
    draw_form_summary_cell(c, margin + (summary_w + 4) * 2, summary_top, summary_w, 34, "Deductions", neg(paystub.total_deductions_current))
    draw_form_summary_cell(c, margin + (summary_w + 4) * 3, summary_top, summary_w, 34, "Net Pay", money_display(paystub.net_pay_current), dark=True)

    earnings_top = summary_top - 42
    earnings_bottom = draw_form_table(
        c,
        margin,
        earnings_top,
        width,
        "Earnings",
        ("Description", "Rate", "Hours", "Current", "YTD"),
        _table_rows_for_earnings(paystub),
        column_widths=(4.3, 1.3, 1.25, 1.7, 1.7),
        emphasize_labels={"Gross Pay"},
        value_size=7.0,
    )

    lower_top = earnings_bottom - 10
    lower_w = (width - 8) / 2
    taxes_bottom = draw_form_table(
        c,
        margin,
        lower_top,
        lower_w,
        "Taxes",
        ("Description", "Current", "YTD"),
        _table_rows_for_taxes(paystub),
        column_widths=(3.4, 1.6, 1.6),
        emphasize_labels={"Total Taxes"},
    )
    deductions_bottom = draw_form_table(
        c,
        margin + lower_w + 8,
        lower_top,
        lower_w,
        "Deductions and Benefits",
        ("Description", "Current", "YTD"),
        _table_rows_for_deductions(paystub) + _table_rows_for_benefits(paystub),
        column_widths=(3.4, 1.6, 1.6),
        emphasize_labels={"Total Dedns"},
    )

    notes_top = min(taxes_bottom, deductions_bottom) - 10
    notes_h = max(88, notes_top - (footer_y + 18))
    draw_form_note_panel(c, margin, notes_top, width, notes_h, "Payroll Notes", paystub.important_notes + paystub.footnotes)

    draw_rule(c, margin, footer_y + 8, margin + width, footer_y + 8, color=GRID, lw=0.35)
    draw_text(c, margin, footer_y, "Employee earnings statement", size=6.0, color=TEXT_MUTED)
    draw_right(c, margin + width, footer_y, f"Check no. {paystub.payroll_check_number or 'N/A'}", size=6.0, color=TEXT_MUTED)


def _render_adp_like_statement(c: canvas.Canvas, paystub: Paystub) -> None:
    margin = PRINT_SAFE_MARGIN
    width = PAGE_WIDTH - margin * 2
    page_top = PAGE_HEIGHT - margin
    footer_y = margin + 12

    draw_box(c, margin, margin, width, PAGE_HEIGHT - margin * 2, fill=PAPER, stroke=GRID_STRONG, lw=0.7)
    fill_rect(c, margin, page_top - 6, width, 6, INK)

    header_h = 64
    net_w = 168
    draw_box(c, margin, page_top - header_h, width, header_h, fill=WHITE, stroke=GRID_STRONG, lw=0.55)
    draw_text(c, margin + 10, page_top - 15, "EARNINGS STATEMENT", size=6.2, bold=True, color=TEXT_MUTED)
    draw_fit_text(c, margin + 10, page_top - 31, width - net_w - 30, paystub.company_name.upper(), size=15.2, min_size=10.2, bold=True, color=INK)
    draw_wrapped_text(c, margin + 10, page_top - 44, width - net_w - 30, _format_address(paystub.company_address), size=6.6, color=TEXT_MUTED, leading=8, max_lines=2)
    draw_box(c, margin + width - net_w - 10, page_top - header_h + 9, net_w, header_h - 18, fill=INK, stroke=INK, lw=0.55)
    draw_text(c, margin + width - net_w, page_top - 16, "CURRENT NET PAY", size=6.0, bold=True, color=PAPER)
    draw_fit_text(c, margin + width - 18, page_top - 37, net_w - 16, money(paystub.net_pay_current), size=18.5, min_size=13, bold=True, color=PAPER, align="right")

    block_top = page_top - header_h - 10
    block_gap = 8
    block_w = (width - block_gap * 2) / 3
    draw_form_field(c, margin, block_top, block_w, 46, "Employee", paystub.employee_name.upper(), max_value_lines=2)
    draw_form_field(c, margin + block_w + block_gap, block_top, block_w, 46, "Pay Date", paystub.pay_date, max_value_lines=1)
    draw_form_field(c, margin + (block_w + block_gap) * 2, block_top, block_w, 46, "Pay Period", f"{paystub.pay_period_start} to {paystub.pay_period_end}", value_size=7.6)

    meta_top = block_top - 54
    meta_gap = 6
    meta_w = (width - meta_gap * 3) / 4
    draw_form_field(c, margin, meta_top, meta_w, 40, "Employee ID", paystub.employee_id, max_value_lines=1)
    draw_form_field(c, margin + meta_w + meta_gap, meta_top, meta_w, 40, "Check No.", paystub.payroll_check_number or "N/A", max_value_lines=1)
    draw_form_field(c, margin + (meta_w + meta_gap) * 2, meta_top, meta_w, 40, "SSN", paystub.social_security_number or "Not provided", max_value_lines=1)
    draw_form_field(c, margin + (meta_w + meta_gap) * 3, meta_top, meta_w, 40, "Marital Status", paystub.taxable_marital_status or "Not provided", secondary=f"Allowances {paystub.exemptions_allowances or '0'}", max_value_lines=1)

    address_top = meta_top - 48
    address_w = (width - 8) / 2
    draw_form_field(c, margin, address_top, address_w, 46, "Employee Address", _format_address(paystub.employee_address) or "Not provided", value_size=7.2)
    draw_form_field(c, margin + address_w + 8, address_top, address_w, 46, "Company Address", _format_address(paystub.company_address), value_size=7.2)

    summary_top = address_top - 54
    summary_w = (width - 12) / 4
    draw_form_summary_cell(c, margin, summary_top, summary_w, 34, "Gross", money_display(paystub.gross_pay_current))
    draw_form_summary_cell(c, margin + summary_w + 4, summary_top, summary_w, 34, "Taxes", neg(paystub.total_taxes_current))
    draw_form_summary_cell(c, margin + (summary_w + 4) * 2, summary_top, summary_w, 34, "Deductions", neg(paystub.total_deductions_current))
    draw_form_summary_cell(c, margin + (summary_w + 4) * 3, summary_top, summary_w, 34, "YTD Net", money_display(paystub.net_pay_ytd), dark=True)

    earnings_top = summary_top - 42
    earnings_bottom = draw_form_table(
        c,
        margin,
        earnings_top,
        width,
        "Earnings",
        ("Description", "Rate", "Hours", "Current", "YTD"),
        _table_rows_for_earnings(paystub),
        column_widths=(4.5, 1.25, 1.2, 1.55, 1.55),
        emphasize_labels={"Gross Pay"},
        value_size=7.0,
    )

    lower_top = earnings_bottom - 10
    lower_gap = 6
    lower_w = (width - lower_gap * 2) / 3
    taxes_bottom = draw_form_table(
        c,
        margin,
        lower_top,
        lower_w,
        "Taxes",
        ("Description", "Current", "YTD"),
        _table_rows_for_taxes(paystub),
        column_widths=(3.2, 1.5, 1.5),
        emphasize_labels={"Total Taxes"},
    )
    deductions_bottom = draw_form_table(
        c,
        margin + lower_w + lower_gap,
        lower_top,
        lower_w,
        "Deductions",
        ("Description", "Current", "YTD"),
        _table_rows_for_deductions(paystub),
        column_widths=(3.2, 1.5, 1.5),
        emphasize_labels={"Total Dedns"},
    )
    benefits_bottom = draw_form_table(
        c,
        margin + (lower_w + lower_gap) * 2,
        lower_top,
        lower_w,
        "Other Benefits",
        ("Description", "Current", "YTD"),
        _table_rows_for_benefits(paystub),
        column_widths=(3.2, 1.5, 1.5),
        placeholder="No employer-paid items",
    )

    notes_top = min(taxes_bottom, deductions_bottom, benefits_bottom) - 10
    notes_h = max(84, notes_top - (footer_y + 18))
    draw_form_note_panel(c, margin, notes_top, width, notes_h, "Important Notes", paystub.important_notes + paystub.footnotes)

    draw_rule(c, margin, footer_y + 8, margin + width, footer_y + 8, color=GRID, lw=0.35)
    draw_text(c, margin, footer_y, "Payroll record for employee reference", size=6.0, color=TEXT_MUTED)
    draw_right(c, margin + width, footer_y, f"Statement date {paystub.pay_date}", size=6.0, color=TEXT_MUTED)


def _render_detached_check(c: canvas.Canvas, paystub: Paystub) -> None:
    margin = PRINT_SAFE_MARGIN
    frame_w = PAGE_WIDTH - margin * 2
    frame_h = PAGE_HEIGHT - margin * 2
    page_top = PAGE_HEIGHT - margin
    draw_box(c, margin, margin, frame_w, frame_h, fill=PAPER, stroke=GRID_STRONG, lw=0.7)
    fill_rect(c, margin, page_top - 3, frame_w, 3, INK)

    bc_x, bc_y = margin + 4, PAGE_HEIGHT - margin - 22
    bc_w, bc_h = 22, 17
    c.setFillColor(INK)
    c.rect(bc_x, bc_y, bc_w, bc_h, fill=1, stroke=0)
    c.setFillColor(WHITE)
    for i in range(1, 6):
        bh = 1.5 if i % 2 == 0 else 0.8
        c.rect(bc_x + 2, bc_y + i * 2.8, bc_w - 4, bh, fill=1, stroke=0)
    c.setFillColor(BLACK)

    top_y = PAGE_HEIGHT - margin - 4
    for label, xpos in [("CO.", 42), ("FILE", 66), ("DEPT.", 92), ("CLOCK", 122), ("NUMBER", 160)]:
        draw_text(c, xpos, top_y - 10, label, size=5, color=TEXT_MUTED)

    draw_text(c, 42, top_y - 20, _company_code(paystub), size=6, color=TEXT)
    draw_text(c, 66, top_y - 20, paystub.employee_id[:8] if paystub.employee_id else "", size=6, color=TEXT)
    draw_text(c, 92, top_y - 20, paystub.work_state or "", size=6, color=TEXT)
    draw_text(c, 122, top_y - 20, "", size=6, color=TEXT)
    draw_text(c, 160, top_y - 20, paystub.payroll_check_number or _barcode_digits(paystub)[:9], size=6, color=TEXT)

    left_text_x = 38
    right_block_x = 348
    right_block_w = 190

    draw_fit_text(c, left_text_x, top_y - 36, 240, paystub.company_name.upper(), size=9.5, min_size=7.2, bold=True, color=TEXT)
    draw_address_block(c, left_text_x, top_y - 47, _format_address(paystub.company_address), size=7.0, leading=8, color=TEXT, width=240, max_lines=3)

    draw_text(c, right_block_x, top_y - 20, "Earnings Statement", size=13, bold=True, color=TEXT)
    draw_logo_or_badge(c, right_block_x + 162, top_y - 29, 50, 18, text="PG")
    draw_text(c, right_block_x, top_y - 39, "Period ending:", size=6.6, color=TEXT)
    draw_right(c, right_block_x + 110, top_y - 39, paystub.pay_period_end, size=6.6, color=TEXT)
    draw_text(c, right_block_x, top_y - 50, "Pay date:", size=6.6, color=TEXT)
    draw_right(c, right_block_x + 110, top_y - 50, paystub.pay_date, size=6.6, color=TEXT)
    draw_wrapped_text(c, right_block_x, top_y - 70, right_block_w, paystub.employee_name.upper(), size=8.8, bold=True, color=TEXT, leading=9, max_lines=3)
    draw_wrapped_text(c, right_block_x, top_y - 96, right_block_w, (_format_address(paystub.employee_address) or "NOT PROVIDED").upper(), size=7.6, bold=True, color=TEXT, leading=8.5, max_lines=3)

    tax_y = top_y - 102
    draw_text(c, left_text_x, tax_y, f"Social Security Number: {paystub.social_security_number or 'Not provided'}", size=6.4, color=TEXT)
    draw_text(c, left_text_x, tax_y - 9, f"Taxable Marital Status: {paystub.taxable_marital_status or 'Not provided'}", size=6.4, color=TEXT)
    draw_text(c, left_text_x, tax_y - 18, f"Federal: {paystub.exemptions_allowances or '0'}", size=6.4, color=TEXT)
    draw_text(c, left_text_x, tax_y - 27, f"Additional Tax: {num(paystub.additional_federal_withholding)}", size=6.4, color=TEXT)
    draw_text(c, left_text_x, tax_y - 36, f"State: {paystub.work_state or ''}", size=6.4, color=TEXT)

    statement_top = 590
    left_x = margin + 6
    left_w = 270
    right_x = left_x + left_w + 16
    right_w = PAGE_WIDTH - margin - right_x - 6

    earnings_bottom = draw_form_table(
        c,
        left_x,
        statement_top,
        left_w,
        "Earnings",
        ("Description", "rate", "hours", "this period", "total to date"),
        _table_rows_for_earnings(paystub),
        column_widths=(4.0, 1.1, 1.1, 1.65, 1.65),
        emphasize_labels={"Gross Pay"},
        value_size=6.5,
        header_size=5.5,
        title_fill=SURFACE_ALT,
        zebra_fill=None,
    )

    deductions_rows: list[tuple[str, str, str]] = []
    if paystub.taxes:
        deductions_rows.append(("Statutory", "", ""))
        deductions_rows.extend((item.label, neg(item.current), num(item.ytd)) for item in paystub.taxes)
    if paystub.deductions:
        deductions_rows.append(("Other", "", ""))
        deductions_rows.extend((item.label, neg(item.current), num(item.ytd)) for item in paystub.deductions)
    if paystub.adjustments:
        deductions_rows.append(("Adjustment", "", ""))
        deductions_rows.extend(
            (
                item.label,
                f"+{item.current:,.2f}" if item.current > 0 else neg(abs(item.current)),
                num(item.ytd) if item.ytd else "",
            )
            for item in paystub.adjustments
        )
    deductions_rows.append(("Net Pay", money_display(paystub.net_pay_current), ""))

    deductions_bottom = draw_form_table(
        c,
        left_x,
        earnings_bottom - 8,
        left_w,
        "Deductions",
        ("Description", "this period", "total to date"),
        deductions_rows,
        column_widths=(3.7, 1.6, 1.6),
        emphasize_labels={"Statutory", "Other", "Adjustment", "Net Pay"},
        value_size=6.45,
        header_size=5.5,
        title_fill=SURFACE_ALT,
        zebra_fill=None,
    )

    footnote_y = deductions_bottom - 10
    for note_line in paystub.footnotes[:2]:
        footnote_y = draw_wrapped_text(c, left_x + 2, footnote_y, left_w - 4, note_line, size=6.3, color=TEXT, leading=8, max_lines=2) - 2
    if footnote_y > 208:
        draw_text(c, left_x + 2, footnote_y - 4, f"Your federal wages this period are {money(paystub.gross_pay_current)}", size=6.3, color=TEXT_MUTED)

    benefits_bottom = statement_top
    if paystub.other_benefits:
        benefits_bottom = draw_form_table(
            c,
            right_x,
            statement_top,
            right_w,
            "Other Benefits and Information",
            ("Description", "this period", "total to date"),
            _table_rows_for_benefits(paystub),
            column_widths=(3.2, 1.35, 1.35),
            value_size=6.45,
            header_size=5.5,
            title_fill=SURFACE_ALT,
            zebra_fill=None,
        )

    tear_y = 184
    if paystub.important_notes:
        notes_top = benefits_bottom - 8
        notes_h = max(86, notes_top - (tear_y + 8))
        draw_form_note_panel(c, right_x, notes_top, right_w, notes_h, "Important Notes", paystub.important_notes, fill=WHITE, stroke=GRID_STRONG)

    c.setDash(3, 3)
    draw_rule(c, margin, tear_y, PAGE_WIDTH - margin, tear_y, color=LINE, lw=0.6)
    c.setDash()

    c.saveState()
    c.setFont("Helvetica", 5)
    c.setFillColor(TEXT)
    c.translate(PAGE_WIDTH - 14, tear_y)
    c.rotate(90)
    c.drawCentredString(0, 0, "TEAR HERE")
    c.restoreState()

    ck_x = margin
    ck_y = margin + 8
    ck_w = PAGE_WIDTH - margin * 2
    ck_h = 144
    strip_h = 32
    div_x = ck_x + 230
    side_strip = 8

    security_fill = colors.HexColor("#C9C9C4")
    band_top_y = ck_y + ck_h
    band_top_h = tear_y - band_top_y
    c.setFillColor(security_fill)
    c.rect(margin, band_top_y, PAGE_WIDTH - margin * 2, max(0, band_top_h), fill=1, stroke=0)
    c.rect(margin, margin, PAGE_WIDTH - margin * 2, ck_y - margin, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 3.5)
    sec_msg = "SECURITY BACKGROUND ON ORIGINAL DOCUMENT  *  "
    for band_y in [band_top_y + 1, margin + 1]:
        x_pos = margin + 2
        while x_pos < PAGE_WIDTH - margin - 10:
            c.drawString(x_pos, band_y, sec_msg)
            x_pos += 138
    c.setFillColor(BLACK)

    draw_box(c, ck_x, ck_y, ck_w, ck_h, fill=colors.HexColor("#F6F4EE"), stroke=GRID_STRONG, lw=0.7)
    fill_rect(c, ck_x, ck_y, side_strip, ck_h, colors.HexColor("#8D8D88"))
    fill_rect(c, ck_x + ck_w - side_strip, ck_y, side_strip, ck_h, colors.HexColor("#8D8D88"))
    fill_rect(c, ck_x + side_strip, ck_y + ck_h - strip_h, ck_w - side_strip * 2, strip_h, colors.HexColor("#EFEEE8"))
    draw_rule(c, ck_x + side_strip, ck_y + ck_h - strip_h, ck_x + ck_w - side_strip, ck_y + ck_h - strip_h, color=GRID, lw=0.4)
    draw_rule(c, div_x, ck_y + ck_h - strip_h + 2, div_x, ck_y + ck_h - 2, color=GRID, lw=0.4)

    logo_sz = 20
    logo_x = ck_x + side_strip + 8
    logo_y_pos = ck_y + ck_h - strip_h + (strip_h - logo_sz) // 2
    draw_logo_or_badge(c, logo_x, logo_y_pos, 36, logo_sz, text="PG")

    draw_fit_text(c, logo_x + logo_sz + 18, ck_y + ck_h - 10, 155, paystub.company_name.upper(), size=7, min_size=6, bold=True, color=TEXT)
    draw_address_block(c, logo_x + logo_sz + 18, ck_y + ck_h - 20, _format_address(paystub.company_address), size=6, color=TEXT, width=155, max_lines=2)

    draw_text(c, div_x + 8, ck_y + ck_h - 9, "Payroll check number:", size=6, bold=True, color=TEXT)
    draw_text(c, div_x + 110, ck_y + ck_h - 9, paystub.payroll_check_number or _barcode_digits(paystub)[:9], size=6, color=TEXT)
    draw_text(c, div_x + 8, ck_y + ck_h - 19, "Pay date:", size=6, bold=True, color=TEXT)
    draw_text(c, div_x + 110, ck_y + ck_h - 19, paystub.pay_date, size=6, color=TEXT)
    draw_text(c, div_x + 8, ck_y + ck_h - 29, "Social Security No.", size=6, bold=True, color=TEXT)
    draw_text(c, div_x + 110, ck_y + ck_h - 29, paystub.social_security_number, size=6, color=TEXT)
    draw_text(c, div_x + 8, ck_y + ck_h - 39, "Net pay:", size=6, bold=True, color=TEXT)
    draw_text(c, div_x + 110, ck_y + ck_h - 39, money(paystub.net_pay_current), size=6, bold=True, color=INK)
    if paystub.bank_name:
        draw_text(c, div_x + 8, ck_y + ck_h - 49, "Bank:", size=6, bold=True, color=TEXT)
        draw_fit_text(c, div_x + 110, ck_y + ck_h - 49, 96, paystub.bank_name, size=6, min_size=5.2, color=TEXT)
    if paystub.deposit_account_type or paystub.account_number:
        draw_text(c, div_x + 8, ck_y + ck_h - 59, "Deposit:", size=6, bold=True, color=TEXT)
        deposit_bits = []
        if paystub.deposit_account_type:
            deposit_bits.append(str(paystub.deposit_account_type).title())
        if paystub.account_number:
            deposit_bits.append(_masked_account(paystub.account_number))
        draw_fit_text(c, div_x + 110, ck_y + ck_h - 59, 96, " ".join(deposit_bits), size=6, min_size=5.2, color=TEXT)
    if paystub.routing_number:
        draw_text(c, div_x + 8, ck_y + ck_h - 69, "Routing:", size=6, bold=True, color=TEXT)
        draw_text(c, div_x + 110, ck_y + ck_h - 69, str(paystub.routing_number), size=6, color=TEXT)

    body_top = ck_y + ck_h - strip_h

    payee_x = ck_x + side_strip + 10
    payee_line_x = ck_x + side_strip + 64
    payee_line_w = 300
    draw_text(c, payee_x, body_top - 12, "PAY TO THE", size=6, bold=True, color=TEXT)
    draw_text(c, payee_x, body_top - 21, "ORDER OF", size=6, bold=True, color=TEXT)
    draw_fit_text(c, payee_line_x, body_top - 20, payee_line_w - 4, paystub.employee_name.upper(), size=9, min_size=7, bold=True, color=INK)
    draw_rule(c, payee_line_x, body_top - 23, payee_line_x + payee_line_w, body_top - 23, color=BLACK, lw=0.5)

    words = amount_to_words(paystub.net_pay_current)
    amount_words_x = payee_line_x
    amount_words_y = body_top - 38
    amount_words_w = 318
    draw_wrapped_text(c, amount_words_x, amount_words_y, amount_words_w, words, size=7, bold=False, color=TEXT, leading=9, max_lines=2)
    draw_rule(c, amount_words_x, body_top - 49, amount_words_x + amount_words_w, body_top - 49, color=BLACK, lw=0.4)

    amt_box_w = 92
    amt_box_h = 20
    amt_box_x = ck_x + ck_w - side_strip - amt_box_w - 12
    amt_box_y = body_top - 48
    draw_box(c, amt_box_x, amt_box_y, amt_box_w, amt_box_h, fill=WHITE, lw=0.8)
    draw_right(c, amt_box_x + amt_box_w - 6, amt_box_y + 6, money(paystub.net_pay_current), size=10, bold=True, color=INK)

    sig_x1 = ck_x + ck_w - 190
    sig_x2 = ck_x + ck_w - side_strip - 14
    sig_y = ck_y + 38
    draw_rule(c, sig_x1, sig_y, sig_x2, sig_y, color=BLACK, lw=0.7)

    c.saveState()
    c.setStrokeColor(TEXT)
    c.setLineWidth(0.9)
    path = c.beginPath()
    sx, sy2 = sig_x1 + 10, sig_y + 9
    path.moveTo(sx, sy2)
    path.curveTo(sx + 12, sy2 + 9, sx + 24, sy2 - 3, sx + 36, sy2 + 7)
    path.curveTo(sx + 48, sy2 + 15, sx + 60, sy2 + 1, sx + 72, sy2 + 10)
    path.curveTo(sx + 82, sy2 + 16, sx + 94, sy2 + 4, sx + 104, sy2 + 8)
    c.drawPath(path, stroke=1, fill=0)
    c.restoreState()

    draw_text(c, sig_x1 + 8, sig_y - 7, "AUTHORIZED SIGNATURE", size=5, color=TEXT)
    company_address_lines = _address_lines(paystub.company_address, max_lines=2)
    draw_text(c, ck_x + side_strip + 10, ck_y + 54, paystub.company_name.upper(), size=6, color=TEXT)
    draw_text(c, ck_x + side_strip + 10, ck_y + 45, company_address_lines[0] if company_address_lines else "", size=6, color=TEXT)
    draw_text(c, ck_x + side_strip + 10, ck_y + 36, company_address_lines[1] if len(company_address_lines) > 1 else "", size=6, color=TEXT)

    check_num = paystub.payroll_check_number or _barcode_digits(paystub)[:9]
    micr_seed = _barcode_digits(paystub)
    draw_text(c, ck_x + 118, ck_y + 6, f"\u2448{check_num}\u2448  :{micr_seed[:9]}:  {micr_seed[3:12]}\u2448", size=9, color=TEXT)


def generate_paystub_pdf(
    data: dict,
    output_dir: str = "output",
    template: PaystubTemplate | str = PaystubTemplate.DETACHED_CHECK,
) -> str:
    paystub = Paystub(**data)
    resolved_template = _coerce_template(template)

    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    filename = (
        f"paystub_{paystub.employee_id}_{paystub.pay_date}_{resolved_template.value}.pdf"
    ).replace(":", "-")
    output_path = output_dir_path / filename

    c = canvas.Canvas(str(output_path), pagesize=LETTER)
    c.setTitle(f"Paystub {paystub.employee_name} {paystub.pay_date}")
    c.setAuthor(paystub.company_name)
    c.setSubject(f"{resolved_template.value} payroll statement")
    if resolved_template == PaystubTemplate.SIMPLE:
        _render_simple_stub(c, paystub)
    elif resolved_template == PaystubTemplate.ADP:
        _render_adp_like_statement(c, paystub)
    else:
        _render_detached_check(c, paystub)

    c.save()
    return str(output_path)
