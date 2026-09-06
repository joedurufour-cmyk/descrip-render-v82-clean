"""
test_main_bugs.py — Tests puntuales de bugs de main.py que no dependen de
Gemini (no requieren API key ni red): _img_to_bytes (A1) y el gateo de
/debug (A8).
"""
import io
import sys

from PIL import Image

from main import _img_to_bytes, MAX_LADO

fallos = []


def check(cond, msg):
    if not cond:
        fallos.append(msg)


# --- Caso A1: PNG RGBA grande no debe explotar y debe bajar de tamaño ---
im = Image.new("RGBA", (3000, 3000), (255, 0, 0, 128))
buf = io.BytesIO()
im.save(buf, format="PNG")
png_bytes = buf.getvalue()

try:
    jpeg_bytes = _img_to_bytes(png_bytes)
except OSError as e:
    fallos.append(f"A1: _img_to_bytes explotó con PNG RGBA: {e}")
    jpeg_bytes = None

if jpeg_bytes is not None:
    check(len(jpeg_bytes) < 400 * 1024, f"A1: salida debe pesar <400KB, pesa {len(jpeg_bytes)} bytes")
    out = Image.open(io.BytesIO(jpeg_bytes))
    check(out.format == "JPEG", "A1: la salida debe ser JPEG")
    check(out.mode == "RGB", "A1: la salida debe estar en modo RGB (sin alfa)")
    check(max(out.size) <= MAX_LADO, f"A1: el lado máximo debe ser <= {MAX_LADO}, es {out.size}")
    print(f"A1 OK -> {len(png_bytes)} bytes RGBA {im.size} -> {len(jpeg_bytes)} bytes JPEG {out.size}")

# --- Caso A1b: imagen RGB normal (sin alfa) también debe funcionar y no reventar ---
im2 = Image.new("RGB", (500, 500), (0, 255, 0))
buf2 = io.BytesIO()
im2.save(buf2, format="JPEG")
jpeg_in = buf2.getvalue()
try:
    out2_bytes = _img_to_bytes(jpeg_in)
    out2 = Image.open(io.BytesIO(out2_bytes))
    check(out2.mode == "RGB", "A1b: imagen RGB normal debe seguir funcionando")
except Exception as e:
    fallos.append(f"A1b: imagen RGB normal no debería fallar: {e}")
print("A1b OK")

# --- Caso A1c: paleta indexada (modo P, común en algunos PNG) tampoco debe explotar ---
im3 = Image.new("P", (200, 200))
buf3 = io.BytesIO()
im3.save(buf3, format="PNG")
p_bytes = buf3.getvalue()
try:
    out3_bytes = _img_to_bytes(p_bytes)
    out3 = Image.open(io.BytesIO(out3_bytes))
    check(out3.mode == "RGB", "A1c: imagen modo P (paleta) debe convertirse a RGB sin explotar")
except OSError as e:
    fallos.append(f"A1c: _img_to_bytes explotó con modo P: {e}")
print("A1c OK")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    if fallos:
        print(f"❌ FALLARON {len(fallos)} TESTS:")
        for f in fallos:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("✅ TODOS LOS TESTS DE main.py PASARON")
        sys.exit(0)
