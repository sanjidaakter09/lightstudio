from io import BytesIO
import math

from flask import Flask, render_template, request, jsonify, send_file
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024


@app.route("/")
def home():
    return render_template("index.html")


def number(name, default, minimum=None, maximum=None):
    try:
        value = float(request.form.get(name, default))
    except (TypeError, ValueError):
        value = float(default)

    if minimum is not None:
        value = max(minimum, value)

    if maximum is not None:
        value = min(maximum, value)

    return value


def flag(name):
    return request.form.get(name, "false").lower() in {
        "1", "true", "yes", "on"
    }


def apply_effect(image, effect):

    effect = (effect or "none").lower()

    if effect == "cool":
        r, g, b = image.split()
        r = r.point(lambda p: int(p * 0.92))
        b = b.point(lambda p: min(255, int(p * 1.10)))
        return Image.merge("RGB", (r, g, b))

    if effect == "sepia":
        gray = ImageOps.grayscale(image)
        return ImageOps.colorize(
            gray, "#3b2418", "#f4dfb5"
        ).convert("RGB")

    if effect == "negative":
        return ImageOps.invert(image)

    if effect == "grayscale":
        return ImageOps.grayscale(image).convert("RGB")

    if effect == "blackwhite":
        gray = ImageOps.grayscale(image)
        gray = gray.point(lambda p: 255 if p >= 128 else 0)
        return gray.convert("RGB")

    if effect == "cinematic":
        r, g, b = image.split()

        r = r.point(lambda p: min(255, int(p * 1.04 + 3)))
        b = b.point(lambda p: min(255, int(p * 1.08 + 2)))

        image = Image.merge("RGB", (r, g, b))
        image = ImageEnhance.Contrast(image).enhance(1.12)

        return ImageEnhance.Color(image).enhance(0.85)

    if effect == "vintage":
        gray = ImageOps.grayscale(image)

        toned = ImageOps.colorize(
            gray, "#49352a", "#e8d4a9"
        ).convert("RGB")

        return Image.blend(image, toned, 0.45)

    if effect == "retro":
        image = ImageEnhance.Color(image).enhance(1.18)
        image = ImageEnhance.Contrast(image).enhance(0.92)

        r, g, b = image.split()

        r = r.point(lambda p: min(255, int(p * 1.05 + 5)))
        b = b.point(lambda p: int(p * 0.94))

        return Image.merge("RGB", (r, g, b))

    return image


def process_image(upload):

    image = Image.open(upload.stream)
    image = ImageOps.exif_transpose(image).convert("RGB")

    # ---------------- CROP ----------------

    crop_percent = number("crop", 0, 0, 45) / 100

    if crop_percent:

        x_margin = int(image.width * crop_percent)
        y_margin = int(image.height * crop_percent)

        if (
            image.width - 2 * x_margin >= 1
            and image.height - 2 * y_margin >= 1
        ):

            image = image.crop((
                x_margin,
                y_margin,
                image.width - x_margin,
                image.height - y_margin
            ))

    # ---------------- ROTATE ----------------

    rotation = number("rotation", 0, -3600, 3600)

    if rotation:
        image = image.rotate(
            -rotation,
            expand=True,
            resample=Image.Resampling.BICUBIC
        )

    # ---------------- FLIP ----------------

    if flag("flip_horizontal"):
        image = ImageOps.mirror(image)

    if flag("flip_vertical"):
        image = ImageOps.flip(image)

    # ---------------- RESIZE ----------------

    width = int(number("resize_width", 0, 0, 12000))
    height = int(number("resize_height", 0, 0, 12000))

    if width > 0 and height > 0:
        image = image.resize(
            (width, height),
            Image.Resampling.LANCZOS
        )

    # ---------------- BRIGHTNESS ----------------

    brightness = number("brightness", 50, 0, 100) * 2 / 100

    image = ImageEnhance.Brightness(image).enhance(brightness)

    # ---------------- CONTRAST ----------------

    contrast = number("contrast", 50, 0, 100) * 2 / 100

    image = ImageEnhance.Contrast(image).enhance(contrast)

    # ---------------- SATURATION ----------------

    saturation = number("saturation", 50, 0, 100) * 2 / 100

    image = ImageEnhance.Color(image).enhance(saturation)

    # ---------------- SHADOW ----------------

    shadow = number("shadow", 50, 0, 100)

    if shadow != 50:

        strength = (shadow - 50) / 100

        gray = ImageOps.grayscale(image)

        shadow_mask = gray.point(
            lambda p: int(255 * (1 - p / 255) ** 1.5)
        )

        overlay_color = (
            (255, 255, 255)
            if strength > 0
            else (0, 0, 0)
        )

        overlay = Image.new("RGB", image.size, overlay_color)

        amount = min(0.5, abs(strength))

        blended = Image.blend(image, overlay, amount)

        image = Image.composite(
            blended,
            image,
            shadow_mask
        )

    # ---------------- TEMPERATURE ----------------

    temperature = number("temperature", 50, 0, 100)

    if temperature != 50:

        shift = (temperature - 50) * 0.9

        r, g, b = image.split()

        r = r.point(
            lambda p: max(0, min(255, int(p + shift)))
        )

        b = b.point(
            lambda p: max(0, min(255, int(p - shift)))
        )

        image = Image.merge("RGB", (r, g, b))

    # ---------------- BLUR ----------------

    blur = number("blur", 0, 0, 100) / 10

    if blur > 0:
        image = image.filter(
            ImageFilter.GaussianBlur(radius=blur)
        )

    # ---------------- SHARPNESS ----------------

    sharpness_value = number("sharpness", 50, 0, 100)

    sharpness = sharpness_value * 2 / 100

    if sharpness != 1:
        image = ImageEnhance.Sharpness(image).enhance(sharpness)

    # ---------------- VIGNETTE ----------------

    vignette = number("vignette", 0, 0, 100)

    if vignette:

        width, height = image.size

        mask = Image.new("L", (width, height))
        pixels = mask.load()

        center_x = width / 2
        center_y = height / 2

        max_distance = math.sqrt(
            center_x ** 2 + center_y ** 2
        )

        strength = vignette / 100

        for y in range(height):
            for x in range(width):

                distance = math.sqrt(
                    (x - center_x) ** 2
                    + (y - center_y) ** 2
                ) / max_distance

                edge = max(0, (distance - 0.25) / 0.75)

                pixels[x, y] = int(
                    255 * min(1, edge * strength)
                )

        black = Image.new("RGB", image.size, (0, 0, 0))

        image = Image.composite(
            black,
            image,
            mask
        )

    # ---------------- EFFECTS ----------------

    effect = request.form.get("effect", "none")

    image = apply_effect(image, effect)

    return image


@app.route("/preview", methods=["POST"])
@app.route("/download", methods=["POST"])
def render_image():

    upload = request.files.get("image")

    if upload is None or not upload.filename:
        return jsonify({
            "error": "Please upload an image first."
        }), 400

    try:

        image = process_image(upload)

        output = BytesIO()

        image.save(
            output,
            format="JPEG",
            quality=94,
            optimize=True
        )

        output.seek(0)

        is_download = request.path == "/download"

        return send_file(
            output,
            mimetype="image/jpeg",
            as_attachment=is_download,
            download_name="light_studio_edited_image.jpg"
        )

    except Exception:

        app.logger.exception("Image processing failed")

        return jsonify({
            "error": "The image could not be processed."
        }), 400


@app.errorhandler(413)
def file_too_large(error):

    return jsonify({
        "error": "Image is too large. Maximum size is 25 MB."
    }), 413


if __name__ == "__main__":
    app.run(debug=True)