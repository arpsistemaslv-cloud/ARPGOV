"""Gera PDF a partir das imagens locais já verificadas."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas

BASE = Path(r"c:\Users\Victor Hugo\Desktop\PortalGovCRM\trabalho_escolar_imagens")
OUTPUT = Path(r"c:\Users\Victor Hugo\Desktop\Trabalho_Animais_Cafe_Manha.pdf")

ANIMALS = [
    "01_cachorro.jpg", "02_gato.jpg", "03_cavalo.jpg", "04_vaca.jpg", "05_porco.jpg",
    "06_leao.jpg", "07_elefante.jpg", "08_passaro.jpg", "09_peixe.jpg", "10_coelho.jpg",
]
BREAKFAST = [
    "01_pao.jpg", "02_bolo.jpg", "03_pao_de_queijo.jpg", "04_cafe.jpg", "05_leite.jpg",
    "06_ovos.jpg", "07_manteiga.jpg", "08_suco.jpg", "09_cereal.jpg", "10_queijo.jpg",
]


def draw_page(c, title, subtitle, color, light, files, start, page_num, total):
    width, height = A4
    margin = 1.8 * cm
    header_h = 2.4 * cm

    c.setFillColor(colors.HexColor(color))
    c.rect(0, height - header_h, width, header_h, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(width / 2, height - 1.0 * cm, title)
    c.setFont("Helvetica", 11)
    c.drawCentredString(width / 2, height - 1.75 * cm, subtitle)
    c.setFont("Helvetica", 9)
    c.drawRightString(width - 1.2 * cm, height - 2.15 * cm, f"Página {page_num} de {total}")

    top = height - header_h - 0.5 * cm
    row_h = (top - 1.2 * cm) / 5
    img_size = min(5.5 * cm, row_h * 0.62)

    for i in range(5):
        row_top = top - i * row_h
        box_y = row_top - row_h + 0.25 * cm
        box_h = row_h - 0.5 * cm

        c.setFillColor(colors.HexColor(light))
        c.setStrokeColor(colors.HexColor(color))
        c.setLineWidth(0.8)
        c.roundRect(margin, box_y, width - 2 * margin, box_h, 6, fill=1, stroke=1)

        img_x = margin + 1.2 * cm
        img_y = box_y + (box_h - img_size) / 2
        img_path = BASE / files[start + i]

        c.setFillColor(colors.HexColor(color))
        c.setFont("Helvetica-Bold", 14)
        c.drawString(margin + 0.5 * cm, img_y + img_size / 2 - 0.15 * cm, f"{start + i + 1}.")
        c.drawImage(str(img_path), img_x, img_y, img_size, img_size, preserveAspectRatio=True, anchor="sw")

        line_x = img_x + img_size + 1.0 * cm
        line_w = width - margin - line_x - 0.6 * cm
        line_y = box_y + box_h * 0.38
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(line_x, line_y + 0.9 * cm, "Nome:")
        c.setStrokeColor(colors.HexColor("#333333"))
        c.setLineWidth(1.2)
        c.line(line_x, line_y, line_x + line_w, line_y)

    c.showPage()


def main():
    missing = [f for f in ANIMALS + BREAKFAST if not (BASE / f).exists()]
    if missing:
        raise FileNotFoundError(f"Imagens faltando: {missing}")

    c = canvas.Canvas(str(OUTPUT), pagesize=A4)
    draw_page(c, "ANIMAIS", "Escreva o nome de cada animal", "#2E6DA4", "#EBF4FB", ANIMALS, 0, 1, 2)
    draw_page(c, "ANIMAIS", "Escreva o nome de cada animal", "#2E6DA4", "#EBF4FB", ANIMALS, 5, 2, 2)
    draw_page(c, "CAFÉ DA MANHÃ", "Escreva o nome de cada alimento", "#C45C26", "#FDF3EC", BREAKFAST, 0, 1, 2)
    draw_page(c, "CAFÉ DA MANHÃ", "Escreva o nome de cada alimento", "#C45C26", "#FDF3EC", BREAKFAST, 5, 2, 2)
    c.save()
    print(f"Pronto: {OUTPUT}")


if __name__ == "__main__":
    main()
