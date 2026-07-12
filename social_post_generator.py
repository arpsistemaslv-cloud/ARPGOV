"""Gera artes para Instagram e TikTok a partir de dados do catálogo."""
from __future__ import annotations

import io
import os
import textwrap
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

FormatKey = Literal["instagram_feed", "instagram_story", "tiktok"]
LayoutKey = Literal["gov_pro", "gov_bold", "gov_clean", "gov_classic"]

SOCIAL_FORMATS: dict[str, dict] = {
    "instagram_feed": {
        "label": "Instagram — feed (1:1)",
        "size": (1080, 1080),
        "platform": "Instagram",
    },
    "instagram_story": {
        "label": "Instagram — Stories / Reels (9:16)",
        "size": (1080, 1920),
        "platform": "Instagram",
    },
    "tiktok": {
        "label": "TikTok (9:16)",
        "size": (1080, 1920),
        "platform": "TikTok",
    },
}

SOCIAL_LAYOUTS: dict[str, str] = {
    "gov_pro": "ARPGOV Premium",
    "gov_bold": "Destaque com preço",
    "gov_clean": "Minimalista claro",
}

COLOR_BLUE_DARK = (7, 29, 65)
COLOR_BLUE = (19, 81, 180)
COLOR_BLUE_LIGHT = (38, 112, 232)
COLOR_YELLOW = (255, 205, 7)
COLOR_WHITE = (255, 255, 255)
COLOR_BG = (244, 247, 251)
COLOR_CARD = (255, 255, 255)
COLOR_GRAY_TEXT = (92, 101, 112)
COLOR_MUTED = (120, 132, 148)
COLOR_FOOTER_BG = (241, 245, 250)
COLOR_WHATSAPP = (37, 211, 102)
COLOR_WHATSAPP_DARK = (18, 140, 72)


@dataclass
class SocialPostInput:
    static_root: str
    title: str
    unit_price: Decimal | None
    manufacturer: str | None
    sphere: str | None
    category_label: str | None
    product_image: str | None
    brand_primary: str
    brand_accent: str
    site_label: str
    product_url: str
    product_path: str
    whatsapp_url: str | None
    whatsapp_label: str | None
    format_key: str
    layout_key: str
    show_price: bool = True
    show_manufacturer: bool = True
    show_sphere: bool = True
    show_category: bool = True
    show_product_link: bool = True
    show_whatsapp: bool = True
    cta_text: str = "Quero aderir à ata"
    link_cta_text: str = "CLIQUE AQUI"
    whatsapp_cta_text: str = "CHAME NO WHATSAPP"
    headline_override: str | None = None


def _format_brl(val: Decimal | None) -> str:
    if val is None:
        return ""
    try:
        s = f"{val:,.2f}"
        return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return ""


def _font_path(static_root: str, weight: str) -> str | None:
    mapping = {
        "bold": "SourceSans3-Bold.ttf",
        "semibold": "SourceSans3-SemiBold.ttf",
        "regular": "SourceSans3-Regular.ttf",
    }
    bundled = os.path.join(static_root, "fonts", mapping.get(weight, mapping["regular"]))
    if os.path.isfile(bundled):
        return bundled
    win = os.environ.get("WINDIR", r"C:\Windows")
    fallbacks = {
        "bold": ["arialbd.ttf", "segoeuib.ttf"],
        "semibold": ["segoeui.ttf", "arial.ttf"],
        "regular": ["arial.ttf", "segoeui.ttf"],
    }
    for fname in fallbacks.get(weight, fallbacks["regular"]):
        path = os.path.join(win, "Fonts", fname)
        if os.path.isfile(path):
            return path
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.isfile(path):
            return path
    return None


def _font(static_root: str, size: int, weight: str = "regular"):
    path = _font_path(static_root, weight)
    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _tw(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    if not text:
        return 0
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _th(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    if not text:
        return 0
    box = draw.textbbox((0, 0), text, font=font)
    return box[3] - box[1]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int, max_lines: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    lines: list[str] = []
    cur: list[str] = []
    for word in words:
        trial = " ".join(cur + [word])
        if _tw(draw, trial, font) <= max_w or not cur:
            cur.append(word)
        else:
            lines.append(" ".join(cur))
            cur = [word]
        if len(lines) >= max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(" ".join(cur))
    if len(lines) == max_lines:
        joined = " ".join(words)
        if len(joined) > len(" ".join(lines)) + 3:
            lines[-1] = lines[-1].rstrip(".") + "…"
    return lines


def _gradient(canvas: Image.Image, box: tuple[int, int, int, int], top, bottom) -> None:
    x0, y0, x1, y1 = box
    height = max(y1 - y0, 1)
    grad = Image.new("RGB", (1, height))
    for y in range(height):
        r = y / max(height - 1, 1)
        grad.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * r) for i in range(3)))
    canvas.paste(grad.resize((x1 - x0, height), Image.Resampling.NEAREST), (x0, y0))


def _shorten(text: str, max_len: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return textwrap.shorten(text, width=max_len, placeholder="…")


def _draw_brand_mark(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> int:
    draw.rounded_rectangle((x, y, x + size, y + size), radius=max(8, size // 6), fill=COLOR_BLUE)
    bar_h = max(4, size // 12)
    draw.rectangle((x + size // 7, y + size - bar_h - size // 8, x + size - size // 7, y + size - size // 8), fill=COLOR_YELLOW)
    return size


def _draw_brand_row(
    draw: ImageDraw.ImageDraw,
    static_root: str,
    data: SocialPostInput,
    x: int,
    y: int,
    size: int,
    *,
    theme: Literal["dark", "light"] = "dark",
) -> int:
    """Desenha ARP+GOV e retorna a largura total do texto."""
    primary = (data.brand_primary or "ARP").strip() or "ARP"
    accent = (data.brand_accent or "GOV").strip() or "GOV"
    font = _font(static_root, size, "bold")
    w1 = _tw(draw, primary, font)
    w2 = _tw(draw, accent, font)
    if theme == "light":
        draw.text((x, y), primary, fill=COLOR_BLUE_DARK, font=font)
        draw.text((x + w1, y), accent, fill=COLOR_BLUE, font=font)
    else:
        draw.text((x, y), primary, fill=COLOR_WHITE, font=font)
        draw.text((x + w1, y), accent, fill=COLOR_YELLOW, font=font)
    return w1 + w2


def _draw_brand_lockup(
    draw: ImageDraw.ImageDraw,
    static_root: str,
    data: SocialPostInput,
    x: int,
    y: int,
    *,
    mark_size: int = 48,
    text_size: int = 34,
    theme: Literal["dark", "light"] = "dark",
    gap: int = 14,
) -> int:
    """Ícone + logotipo; retorna largura total."""
    mark_y = y + max(0, (text_size - mark_size) // 2)
    _draw_brand_mark(draw, x, mark_y, mark_size)
    text_x = x + mark_size + gap
    text_y = y + max(0, (mark_size - text_size) // 2)
    text_w = _draw_brand_row(draw, static_root, data, text_x, text_y, text_size, theme=theme)
    return mark_size + gap + text_w


def _draw_header(canvas: Image.Image, draw: ImageDraw.ImageDraw, static_root: str, data: SocialPostInput, w: int, height: int) -> None:
    _gradient(canvas, (0, 0, w, height), COLOR_BLUE_DARK, (12, 48, 102))
    draw.rectangle((0, height - 5, w, height), fill=COLOR_YELLOW)
    mark_x, mark_y = 44, (height - 52) // 2
    _draw_brand_mark(draw, mark_x, mark_y, 52)
    _draw_brand_row(draw, static_root, data, mark_x + 64, mark_y + 8, 38, theme="dark")
    badge_font = _font(static_root, 20, "bold")
    badge = "ATA VIGENTE"
    bw = _tw(draw, badge, badge_font)
    bx = w - bw - 56
    by = (height - 34) // 2
    draw.rounded_rectangle((bx - 14, by, bx + bw + 14, by + 34), radius=17, fill=COLOR_YELLOW)
    draw.text((bx, by + 6), badge, fill=COLOR_BLUE_DARK, font=badge_font)


def _draw_clean_header(canvas: Image.Image, draw: ImageDraw.ImageDraw, static_root: str, data: SocialPostInput, w: int, height: int = 92) -> None:
    canvas.paste(Image.new("RGB", (w, height), (248, 250, 252)), (0, 0))
    draw.rectangle((0, 0, w, 6), fill=COLOR_YELLOW)
    draw.line((0, height - 1, w, height - 1), fill=(225, 232, 240), width=1)
    lockup_y = (height - 48) // 2
    _draw_brand_lockup(draw, static_root, data, 40, lockup_y, mark_size=46, text_size=32, theme="light")
    badge_font = _font(static_root, 18, "bold")
    badge = "ATA VIGENTE"
    bw = _tw(draw, badge, badge_font)
    bx = w - bw - 44
    by = (height - 30) // 2
    draw.rounded_rectangle((bx - 12, by, bx + bw + 12, by + 30), radius=15, outline=COLOR_BLUE, width=2)
    draw.text((bx, by + 5), badge, fill=COLOR_BLUE_DARK, font=badge_font)


def _load_product_image(static_root: str, rel_path: str | None, max_w: int, max_h: int) -> Image.Image | None:
    if not rel_path:
        return None
    disk = os.path.join(static_root, rel_path.replace("/", os.sep))
    if not os.path.isfile(disk):
        return None
    try:
        img = Image.open(disk).convert("RGBA")
        img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
        return img
    except OSError:
        return None


def _paste_product_card(
    canvas: Image.Image,
    static_root: str,
    rel_path: str | None,
    box: tuple[int, int, int, int],
    padding: int = 28,
) -> None:
    x0, y0, x1, y1 = box
    card_w, card_h = x1 - x0, y1 - y0
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    shadow = (x0 + 6, y0 + 8, x1 + 6, y1 + 8)
    draw.rounded_rectangle(shadow, radius=22, fill=(7, 29, 65, 35))
    draw.rounded_rectangle((x0, y0, x1, y1), radius=22, fill=COLOR_CARD + (255,))
    draw.rounded_rectangle((x0, y0, x1, y1), radius=22, outline=(218, 225, 235), width=2)

    inner_w = card_w - padding * 2
    inner_h = card_h - padding * 2
    product = _load_product_image(static_root, rel_path, inner_w, inner_h)
    ix = x0 + padding
    iy = y0 + padding
    if product is not None:
        px = ix + (inner_w - product.width) // 2
        py = iy + (inner_h - product.height) // 2
        layer.paste(product, (px, py), product)
    else:
        ph_draw = ImageDraw.Draw(layer)
        msg = "Sem imagem do produto"
        mf = _font(static_root, 26, "semibold")
        ph_draw.text(
            (x0 + (card_w - _tw(ph_draw, msg, mf)) // 2, y0 + (card_h - _th(ph_draw, msg, mf)) // 2),
            msg,
            fill=COLOR_GRAY_TEXT,
            font=mf,
        )
    canvas.alpha_composite(layer)


def _draw_price_tag(draw: ImageDraw.ImageDraw, static_root: str, price: str, x: int, y: int) -> int:
    font = _font(static_root, 54, "bold")
    tw, th = _tw(draw, price, font), _th(draw, price, font)
    pad_x, pad_y = 22, 12
    draw.rounded_rectangle((x, y, x + tw + pad_x * 2, y + th + pad_y * 2), radius=14, fill=COLOR_YELLOW)
    draw.text((x + pad_x, y + pad_y - 2), price, fill=COLOR_BLUE_DARK, font=font)
    return y + th + pad_y * 2 + 14


def _draw_cta_button(
    draw: ImageDraw.ImageDraw,
    static_root: str,
    text: str,
    x: int,
    y: int,
    width: int,
    *,
    fill=COLOR_WHITE,
    text_fill=COLOR_BLUE_DARK,
    height: int = 64,
) -> int:
    label = _shorten(text.strip() or "Quero aderir", 40)
    font = _font(static_root, 30, "bold")
    draw.rounded_rectangle((x, y, x + width, y + height), radius=16, fill=fill)
    tw = _tw(draw, label, font)
    draw.text((x + (width - tw) // 2, y + (height - _th(draw, label, font)) // 2 - 2), label, fill=text_fill, font=font)
    return y + height + 12


def _draw_link_strip(
    draw: ImageDraw.ImageDraw,
    static_root: str,
    data: SocialPostInput,
    x: int,
    y: int,
    width: int,
) -> int:
    if not data.show_product_link:
        return y
    link_label = (data.link_cta_text or "CLIQUE AQUI").strip().upper()
    path = (data.product_path or data.product_url or "").strip()
    if not path:
        return y
    path = _shorten(path.replace("https://", "").replace("http://", ""), 46)

    box_h = 92
    draw.rounded_rectangle((x, y, x + width, y + box_h), radius=14, fill=(5, 22, 50))
    draw.rounded_rectangle((x, y, x + width, y + box_h), radius=14, outline=COLOR_YELLOW, width=3)

    head_font = _font(static_root, 28, "bold")
    head = f"👉  {link_label}"
    draw.text((x + 22, y + 14), head, fill=COLOR_YELLOW, font=head_font)

    url_font = _font(static_root, 22, "semibold")
    draw.text((x + 22, y + 50), path, fill=COLOR_WHITE, font=url_font)
    return y + box_h + 10


def _draw_whatsapp_strip(
    draw: ImageDraw.ImageDraw,
    static_root: str,
    data: SocialPostInput,
    x: int,
    y: int,
    width: int,
    *,
    compact: bool = False,
) -> int:
    if not data.show_whatsapp or not data.whatsapp_url:
        return y
    label = (data.whatsapp_cta_text or "CHAME NO WHATSAPP").strip().upper()
    sub = (data.whatsapp_label or data.whatsapp_url or "").strip()
    sub = _shorten(sub.replace("https://", "").replace("http://", ""), 44 if compact else 48)

    box_h = 74 if compact else 84
    draw.rounded_rectangle((x, y, x + width, y + box_h), radius=14, fill=COLOR_WHATSAPP)
    draw.rounded_rectangle((x, y, x + width, y + box_h), radius=14, outline=COLOR_WHATSAPP_DARK, width=2)

    head_font = _font(static_root, 26 if compact else 28, "bold")
    head = f"💬  {label}"
    draw.text((x + 20, y + 12), head, fill=COLOR_WHITE, font=head_font)

    sub_font = _font(static_root, 20 if compact else 22, "semibold")
    draw.text((x + 20, y + (46 if compact else 48)), sub, fill=(232, 255, 240), font=sub_font)
    return y + box_h + 8


def _draw_bottom_actions(
    draw: ImageDraw.ImageDraw,
    static_root: str,
    data: SocialPostInput,
    x: int,
    y: int,
    width: int,
    *,
    compact: bool = False,
) -> None:
    y = _draw_link_strip(draw, static_root, data, x, y, width)
    _draw_whatsapp_strip(draw, static_root, data, x, y, width, compact=compact)


def _draw_meta(draw: ImageDraw.ImageDraw, static_root: str, data: SocialPostInput, x: int, y: int, max_w: int, *, light=True) -> int:
    parts: list[str] = []
    if data.show_manufacturer and data.manufacturer:
        parts.append(data.manufacturer)
    if data.show_sphere and data.sphere:
        parts.append(data.sphere)
    if data.show_category and data.category_label:
        parts.append(data.category_label)
    if not parts:
        return y
    line = " · ".join(parts)
    font = _font(static_root, 22 if light else 24, "regular")
    color = (220, 228, 240) if light else COLOR_GRAY_TEXT
    if _tw(draw, line, font) > max_w:
        line = _shorten(line, 52)
    if not light:
        chip_h = _th(draw, line, font) + 14
        draw.rounded_rectangle((x, y, x + min(max_w, _tw(draw, line, font) + 24), y + chip_h), radius=10, fill=(238, 243, 250))
        draw.text((x + 12, y + 6), line, fill=color, font=font)
        return y + chip_h + 10
    draw.text((x, y), line, fill=color, font=font)
    return y + _th(draw, line, font) + 10


def _draw_site_footer(
    draw: ImageDraw.ImageDraw,
    static_root: str,
    data: SocialPostInput,
    y: int,
    w: int,
    *,
    height: int = 52,
) -> None:
    draw.rectangle((0, y, w, y + height), fill=COLOR_FOOTER_BG)
    draw.line((0, y, w, y), fill=(218, 225, 235), width=1)
    parts: list[str] = []
    label = (data.site_label or "").strip()
    if label:
        parts.append(label)
    if data.show_whatsapp and data.whatsapp_label:
        wa = data.whatsapp_label.replace("https://", "").replace("http://", "").strip()
        if wa:
            parts.append(wa)
    if not parts:
        return
    line = "  ·  ".join(parts)
    font = _font(static_root, 21, "semibold")
    if _tw(draw, line, font) > w - 80:
        line = _shorten(line, 56)
    tw = _tw(draw, line, font)
    th = _th(draw, line, font)
    tx = (w - tw) // 2
    ty = y + (height - th) // 2
    draw.text((tx, ty), line, fill=COLOR_BLUE_DARK, font=font)


def _render_pro_square(data: SocialPostInput, canvas: Image.Image) -> None:
    w, h = canvas.size
    canvas.paste(Image.new("RGB", (w, h), COLOR_BG))
    draw = ImageDraw.Draw(canvas)
    header_h = 96
    _draw_header(canvas, draw, data.static_root, data, w, header_h)

    _paste_product_card(canvas, data.static_root, data.product_image, (60, 116, w - 60, 620))

    panel_y = 640
    _gradient(canvas, (0, panel_y, w, h), COLOR_BLUE_DARK, COLOR_BLUE)
    draw = ImageDraw.Draw(canvas)

    margin = 52
    content_w = w - margin * 2
    title = (data.headline_override or data.title or "Produto em ata").strip()
    title_font = _font(data.static_root, 44, "bold")
    y = panel_y + 36
    for line in _wrap(draw, title, title_font, content_w, 2):
        draw.text((margin, y), line, fill=COLOR_WHITE, font=title_font)
        y += _th(draw, line, title_font) + 6

    if data.show_price and data.unit_price is not None:
        y = _draw_price_tag(draw, data.static_root, _format_brl(data.unit_price), margin, y + 4)

    y = _draw_meta(draw, data.static_root, data, margin, y + 2, content_w)
    y = _draw_cta_button(draw, data.static_root, data.cta_text, margin, y + 4, content_w, fill=COLOR_YELLOW, text_fill=COLOR_BLUE_DARK)
    _draw_bottom_actions(draw, data.static_root, data, margin, y, content_w, compact=True)


def _render_pro_vertical(data: SocialPostInput, canvas: Image.Image) -> None:
    w, h = canvas.size
    canvas.paste(Image.new("RGB", (w, h), COLOR_BG))
    draw = ImageDraw.Draw(canvas)
    header_h = 108
    _draw_header(canvas, draw, data.static_root, data, w, header_h)

    _paste_product_card(canvas, data.static_root, data.product_image, (48, 124, w - 48, 980))

    panel_y = 1000
    _gradient(canvas, (0, panel_y, w, h), COLOR_BLUE_DARK, (12, 48, 102))
    draw = ImageDraw.Draw(canvas)

    margin = 48
    content_w = w - margin * 2
    title = (data.headline_override or data.title or "Produto em ata").strip()
    title_font = _font(data.static_root, 48, "bold")
    y = panel_y + 40
    for line in _wrap(draw, title, title_font, content_w, 3):
        draw.text((margin, y), line, fill=COLOR_WHITE, font=title_font)
        y += _th(draw, line, title_font) + 8

    if data.show_price and data.unit_price is not None:
        y = _draw_price_tag(draw, data.static_root, _format_brl(data.unit_price), margin, y + 8)

    y = _draw_meta(draw, data.static_root, data, margin, y + 4, content_w)
    y = _draw_cta_button(draw, data.static_root, data.cta_text, margin, y + 8, content_w, fill=COLOR_YELLOW, text_fill=COLOR_BLUE_DARK, height=68)
    _draw_bottom_actions(draw, data.static_root, data, margin, y, content_w)


def _render_bold(data: SocialPostInput, canvas: Image.Image) -> None:
    w, h = canvas.size
    vertical = h > w
    _gradient(canvas, (0, 0, w, h), COLOR_BLUE, COLOR_BLUE_DARK)
    draw = ImageDraw.Draw(canvas)
    _draw_brand_lockup(draw, data.static_root, data, 44, 36 if vertical else 32, mark_size=44, text_size=34, theme="dark")

    card = (40, 120, w - 40, 980) if vertical else (48, 108, w - 48, 580)
    _paste_product_card(canvas, data.static_root, data.product_image, card, padding=32)

    draw = ImageDraw.Draw(canvas)
    margin = 48
    content_w = w - margin * 2
    title_font = _font(data.static_root, 46 if vertical else 40, "bold")
    title = (data.headline_override or data.title or "Produto em ata").strip()
    y = 1020 if vertical else 600
    for line in _wrap(draw, title, title_font, content_w, 3 if vertical else 2):
        draw.text((margin, y), line, fill=COLOR_WHITE, font=title_font)
        y += _th(draw, line, title_font) + 6

    if data.show_price and data.unit_price is not None:
        y = _draw_price_tag(draw, data.static_root, _format_brl(data.unit_price), margin, y + 6)

    y = _draw_meta(draw, data.static_root, data, margin, y, content_w)
    y = _draw_cta_button(draw, data.static_root, data.cta_text, margin, y + 6, content_w, fill=COLOR_YELLOW, text_fill=COLOR_BLUE_DARK)
    _draw_bottom_actions(draw, data.static_root, data, margin, y, content_w, compact=not vertical)


def _render_clean(data: SocialPostInput, canvas: Image.Image) -> None:
    w, h = canvas.size
    vertical = h > w
    canvas.paste(Image.new("RGB", (w, h), COLOR_WHITE))
    draw = ImageDraw.Draw(canvas)

    header_h = 92
    footer_h = 52
    _draw_clean_header(canvas, draw, data.static_root, data, w, header_h)

    margin = 44
    content_w = w - margin * 2
    bottom_reserve = footer_h + 16
    if data.show_product_link:
        bottom_reserve += 86
    if data.show_whatsapp and data.whatsapp_url:
        bottom_reserve += 78

    img_top = header_h + 16
    img_bottom = (1040 if vertical else 600) - bottom_reserve // (1 if vertical else 2)
    img_bottom = max(img_bottom, img_top + 280)
    img_box = (margin, img_top, w - margin, img_bottom)
    _paste_product_card(canvas, data.static_root, data.product_image, img_box, padding=32)

    draw = ImageDraw.Draw(canvas)
    title = (data.headline_override or data.title or "Produto em ata").strip()
    title_font = _font(data.static_root, 42 if vertical else 36, "bold")
    y = img_bottom + 24
    max_title_lines = 3 if vertical else 2
    for line in _wrap(draw, title, title_font, content_w, max_title_lines):
        draw.text((margin, y), line, fill=COLOR_BLUE_DARK, font=title_font)
        y += _th(draw, line, title_font) + 4

    if data.show_price and data.unit_price is not None:
        pf = _font(data.static_root, 48 if vertical else 42, "bold")
        price = _format_brl(data.unit_price)
        draw.text((margin, y + 6), price, fill=COLOR_BLUE, font=pf)
        y += _th(draw, price, pf) + 10

    y = _draw_meta(draw, data.static_root, data, margin, y, content_w, light=False)

    actions_y = y + 6
    max_actions_y = h - footer_h - 8
    compact = not vertical or (actions_y + 190 > max_actions_y)
    cta_h = 58 if compact else 64
    actions_y = _draw_cta_button(
        draw,
        data.static_root,
        data.cta_text,
        margin,
        actions_y,
        content_w,
        fill=COLOR_BLUE_DARK,
        text_fill=COLOR_WHITE,
        height=cta_h,
    )
    if actions_y + 140 <= max_actions_y:
        _draw_bottom_actions(draw, data.static_root, data, margin, actions_y, content_w, compact=compact)

    _draw_site_footer(draw, data.static_root, data, h - footer_h, w, height=footer_h)


def generate_social_post_image(data: SocialPostInput) -> bytes:
    fmt = SOCIAL_FORMATS.get(data.format_key) or SOCIAL_FORMATS["instagram_feed"]
    layout = data.layout_key
    if layout == "gov_classic":
        layout = "gov_pro"
    if layout not in SOCIAL_LAYOUTS:
        layout = "gov_pro"

    w, h = fmt["size"]
    canvas = Image.new("RGBA", (w, h), COLOR_WHITE + (255,))
    vertical = h > w

    if layout == "gov_bold":
        _render_bold(data, canvas)
    elif layout == "gov_clean":
        _render_clean(data, canvas)
    elif vertical:
        _render_pro_vertical(data, canvas)
    else:
        _render_pro_square(data, canvas)

    out = io.BytesIO()
    rgb = Image.new("RGB", canvas.size, COLOR_WHITE)
    rgb.paste(canvas, mask=canvas.split()[3])
    rgb.save(out, format="PNG", optimize=True)
    return out.getvalue()


def social_post_filename(slug: str, format_key: str, layout_key: str) -> str:
    safe_slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in (slug or "produto"))
    return f"{safe_slug}-{format_key}-{layout_key}.png"
