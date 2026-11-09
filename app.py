from io import BytesIO
import math

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_file
)

from PIL import (
    Image,
    ImageEnhance,
    ImageFilter,
    ImageOps,
    ImageFile
)

app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

# Avoid accepting truncated or corrupted images.
ImageFile.LOAD_TRUNCATED_IMAGES = False


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return render_template("index.html")


# =========================================================
# INPUT HELPERS
# =========================================================

def number(name, default, minimum=None, maximum=None):
    """Safely read a numeric value from the submitted form."""

    try:
        value = float(request.form.get(name, default))
    except (TypeError, ValueError):
        value = float(default)

    if not math.isfinite(value):
        value = float(default)

    if minimum is not None:
        value = max(minimum, value)

    if maximum is not None:
        value = min(maximum, value)

    return value


def flag(name):
    """Read a boolean value from the submitted form."""

    return request.form.get(name, "false").lower() in {
        "1", "true", "yes", "on"
    }


# =========================================================
# CROP
# =========================================================

def apply_crop(image):

    x = int(number("crop_x", 0, 0, image.width))
    y = int(number("crop_y", 0, 0, image.height))

    width = int(number("crop_width", 0, 0, image.width))
    height = int(number("crop_height", 0, 0, image.height))

    if width <= 0 or height <= 0:
        return image

    right = min(image.width, x + width)
    bottom = min(image.height, y + height)

    if right <= x or bottom <= y:
        return image

    return image.crop((x, y, right, bottom))


# =========================================================
# ROTATE
# =========================================================

def apply_rotation(image):

    rotation = number("rotation", 0, -3600, 3600)
    rotation = rotation % 360

    if rotation == 0:
        return image

    return image.rotate(
        -rotation,
        expand=True,
        resample=Image.Resampling.BICUBIC
    )


# =========================================================
# FLIP
# =========================================================

def apply_flip(image):

    if flag("flip_horizontal"):
        image = ImageOps.mirror(image)

    if flag("flip_vertical"):
        image = ImageOps.flip(image)

    return image


# =========================================================
# RESIZE
# =========================================================

def apply_resize(image):

    width = int(number("resize_width", 0, 0, 12000))
    height = int(number("resize_height", 0, 0, 12000))

    if width <= 0 or height <= 0:
        return image

    if (width, height) == image.size:
        return image

    return image.resize(
        (width, height),
        Image.Resampling.LANCZOS
    )


# =========================================================
# BRIGHTNESS
# Frontend: 0–100
# Default: 50
# =========================================================

def apply_brightness(image):

    brightness = number("brightness", 50, 0, 100)

    factor = brightness / 50

    return ImageEnhance.Brightness(image).enhance(factor)


# =========================================================
# CONTRAST
# Frontend: 0–100
# Default: 50
# =========================================================

def apply_contrast(image):

    contrast = number("contrast", 50, 0, 100)

    factor = contrast / 50

    return ImageEnhance.Contrast(image).enhance(factor)


# =========================================================
# SATURATION
# Frontend: 0–100
# Default: 50
# =========================================================

def apply_saturation(image):

    saturation = number("saturation", 50, 0, 100)

    factor = saturation / 50

    return ImageEnhance.Color(image).enhance(factor)


# =========================================================
# SHADOW
# Frontend: 0–100
# Default: 50
# =========================================================

def apply_shadow(image):

    shadow = number("shadow", 50, 0, 100)

    if shadow == 50:
        return image

    gray = ImageOps.grayscale(image)

    shadow_mask = gray.point(
        lambda p: int(255 * (1 - p / 255) ** 1.5)
    )

    if shadow > 50:

        overlay = Image.new(
            "RGB",
            image.size,
            (255, 255, 255)
        )

        amount = (shadow - 50) / 50 * 0.65

    else:

        overlay = Image.new(
            "RGB",
            image.size,
            (0, 0, 0)
        )

        amount = (50 - shadow) / 50 * 0.65

    blended = Image.blend(
        image,
        overlay,
        amount
    )

    return Image.composite(
        blended,
        image,
        shadow_mask
    )


# =========================================================
# TEMPERATURE
# Frontend: 0–100
# Default: 50
# =========================================================

def apply_temperature(image):

    temperature = number("temperature", 50, 0, 100)

    if temperature == 50:
        return image

    shift = (temperature - 50) * 0.65

    red, green, blue = image.split()

    red = red.point(
        lambda p: max(0, min(255, int(p + shift)))
    )

    blue = blue.point(
        lambda p: max(0, min(255, int(p - shift)))
    )

    return Image.merge(
        "RGB",
        (red, green, blue)
    )


# =========================================================
# BLUR
# Frontend: 0–100
# Default: 0
# =========================================================

def apply_blur(image):

    blur = number("blur", 0, 0, 100)

    if blur <= 0:
        return image

    return image.filter(
        ImageFilter.GaussianBlur(
            radius=blur / 5
        )
    )


# =========================================================
# SHARPNESS
# Frontend: 0–100
# Default: 20
# =========================================================

def apply_sharpness(image):

    sharpness = number("sharpness", 20, 0, 100)

    factor = sharpness / 20

    return ImageEnhance.Sharpness(
        image
    ).enhance(factor)


# =========================================================
# VIGNETTE
# Frontend: 0–100
# Default: 0
# =========================================================

def apply_vignette(image):

    vignette = number("vignette", 0, 0, 100)

    if vignette <= 0:
        return image

    width, height = image.size

    # Generate the vignette mask at a smaller resolution.
    scale = min(1, 512 / max(width, height))

    small_width = max(1, int(width * scale))
    small_height = max(1, int(height * scale))

    mask = Image.new(
        "L",
        (small_width, small_height)
    )

    pixels = mask.load()

    center_x = small_width / 2
    center_y = small_height / 2

    max_distance = math.sqrt(
        center_x ** 2 + center_y ** 2
    )

    strength = vignette / 100

    for y in range(small_height):

        for x in range(small_width):

            distance = math.sqrt(
                (x - center_x) ** 2 +
                (y - center_y) ** 2
            ) / max_distance

            edge = max(
                0,
                (distance - 0.25) / 0.75
            )

            pixels[x, y] = int(
                255 * min(1, edge * strength)
            )

    mask = mask.resize(
        (width, height),
        Image.Resampling.BILINEAR
    )

    black = Image.new(
        "RGB",
        image.size,
        (0, 0, 0)
    )

    return Image.composite(
        black,
        image,
        mask
    )


# =========================================================
# EFFECTS
# =========================================================

def apply_effect(image, effect):

    effect = (effect or "none").lower()

    if effect == "cool":

        red, green, blue = image.split()

        red = red.point(
            lambda p: int(p * 0.92)
        )

        blue = blue.point(
            lambda p: min(255, int(p * 1.10))
        )

        return Image.merge(
            "RGB",
            (red, green, blue)
        )

    elif effect == "sepia":

        gray = ImageOps.grayscale(image)

        return ImageOps.colorize(
            gray,
            "#3b2418",
            "#f4dfb5"
        ).convert("RGB")

    elif effect == "negative":

        return ImageOps.invert(image)

    elif effect == "grayscale":

        return ImageOps.grayscale(
            image
        ).convert("RGB")

    elif effect == "blackwhite":

        gray = ImageOps.grayscale(image)

        gray = gray.point(
            lambda p: 255 if p >= 128 else 0
        )

        return gray.convert("RGB")

    elif effect == "cinematic":

        red, green, blue = image.split()

        red = red.point(
            lambda p: min(255, int(p * 1.04 + 3))
        )

        blue = blue.point(
            lambda p: min(255, int(p * 1.08 + 2))
        )

        image = Image.merge(
            "RGB",
            (red, green, blue)
        )

        image = ImageEnhance.Contrast(
            image
        ).enhance(1.12)

        return ImageEnhance.Color(
            image
        ).enhance(0.85)

    elif effect == "vintage":

        gray = ImageOps.grayscale(image)

        toned = ImageOps.colorize(
            gray,
            "#49352a",
            "#e8d4a9"
        ).convert("RGB")

        return Image.blend(
            image,
            toned,
            0.45
        )

    elif effect == "retro":

        image = ImageEnhance.Color(
            image
        ).enhance(1.18)

        image = ImageEnhance.Contrast(
            image
        ).enhance(0.92)

        red, green, blue = image.split()

        red = red.point(
            lambda p: min(255, int(p * 1.05 + 5))
        )

        blue = blue.point(
            lambda p: int(p * 0.94)
        )

        return Image.merge(
            "RGB",
            (red, green, blue)
        )

    return image


# =========================================================
# MAIN IMAGE PROCESSING PIPELINE
# =========================================================

def process_image(upload):

    image = Image.open(upload.stream)

    # Correct orientation using EXIF metadata.
    image = ImageOps.exif_transpose(image)

    # Convert to RGB for consistent processing and JPEG output.
    image = image.convert("RGB")

    # 1. Crop
    image = apply_crop(image)

    # 2. Rotate
    image = apply_rotation(image)

    # 3. Flip
    image = apply_flip(image)

    # 4. Resize
    image = apply_resize(image)

    # 5. Brightness
    image = apply_brightness(image)

    # 6. Contrast
    image = apply_contrast(image)

    # 7. Saturation
    image = apply_saturation(image)

    # 8. Shadow
    image = apply_shadow(image)

    # 9. Temperature
    image = apply_temperature(image)

    # 10. Blur
    image = apply_blur(image)

    # 11. Sharpness
    image = apply_sharpness(image)

    # 12. Vignette
    image = apply_vignette(image)

    # 13. Effects
    effect = request.form.get("effect", "none")

    image = apply_effect(image, effect)

    return image


# =========================================================
# IMAGE RESPONSE
# =========================================================

def create_image_response(image, is_download):

    output = BytesIO()

    if is_download:

        # Full-resolution output for downloads.
        image.save(
            output,
            format="JPEG",
            quality=95,
            optimize=True
        )

    else:

        # Smaller preview output for faster browser updates.
        preview = image.copy()

        preview.thumbnail(
            (1600, 1600),
            Image.Resampling.LANCZOS
        )

        preview.save(
            output,
            format="JPEG",
            quality=82,
            optimize=False
        )

    output.seek(0)

    return send_file(
        output,
        mimetype="image/jpeg",
        as_attachment=is_download,
        download_name="light_studio_edited_image.jpg"
    )


# =========================================================
# PREVIEW AND DOWNLOAD ROUTES
# =========================================================

@app.route("/preview", methods=["POST"])
def preview_image():

    upload = request.files.get("image")

    if upload is None or not upload.filename:

        return jsonify({
            "error": "Please upload an image first."
        }), 400

    try:

        image = process_image(upload)

        return create_image_response(
            image,
            is_download=False
        )

    except Exception:

        app.logger.exception(
            "Preview processing failed"
        )

        return jsonify({
            "error": "The image preview could not be processed."
        }), 400


@app.route("/download", methods=["POST"])
def download_image():

    upload = request.files.get("image")

    if upload is None or not upload.filename:

        return jsonify({
            "error": "Please upload an image first."
        }), 400

    try:

        image = process_image(upload)

        return create_image_response(
            image,
            is_download=True
        )

    except Exception:

        app.logger.exception(
            "Download processing failed"
        )

        return jsonify({
            "error": "The image could not be downloaded."
        }), 400


# =========================================================
# FILE SIZE ERROR
# =========================================================

@app.errorhandler(413)
def file_too_large(error):

    return jsonify({
        "error": "Image is too large. Maximum size is 25 MB."
    }), 413


# =========================================================
# GENERAL ERROR HANDLER
# =========================================================

@app.errorhandler(500)
def internal_server_error(error):

    app.logger.exception(
        "Internal server error"
    )

    return jsonify({
        "error": "An internal server error occurred."
    }), 500


# =========================================================
# RUN APPLICATION
# =========================================================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )