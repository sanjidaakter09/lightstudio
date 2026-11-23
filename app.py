from io import BytesIO
import math
import random
import re

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_file
)

from PIL import (
    Image,
    ImageChops,
    ImageColor,
    ImageDraw,
    ImageEnhance,
    ImageFilter,
    ImageOps,
    ImageFile
)

app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

# Reject truncated or corrupted images.
ImageFile.LOAD_TRUNCATED_IMAGES = False

# Maximum supported image dimensions.
MAX_DIMENSION = 12000


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return render_template("index.html")


# =========================================================
# SMALL HELPERS
# =========================================================

def number(name, default, minimum=None, maximum=None):
    """Safely read numeric form values."""

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
    """Read boolean form values."""

    return request.form.get(name, "false").lower() in {
        "1", "true", "yes", "on"
    }


def longest(image):
    """Longest side of the image. Used so effects look the same
    on the small preview and on the full-size download."""

    return max(image.size)


def table(function):
    """Build a 256-value lookup table from a function."""

    return [
        max(0, min(255, int(function(i))))
        for i in range(256)
    ]


def apply_lut(image, function):
    """Apply one function to every color channel."""

    return image.point(table(function) * len(image.getbands()))


def map_channels(image, red=None, green=None, blue=None):
    """Apply a different function to each color channel."""

    r, g, b = image.split()

    if red:
        r = r.point(table(red))

    if green:
        g = g.point(table(green))

    if blue:
        b = b.point(table(blue))

    return Image.merge("RGB", (r, g, b))


def solid(image, color):
    return Image.new("RGB", image.size, color)


def split_tone(image, shadow_color, highlight_color, amount):
    """Tint dark areas and bright areas with two different colors."""

    gray = ImageOps.grayscale(image)
    inverse = ImageOps.invert(gray)

    shadows = Image.blend(image, solid(image, shadow_color), amount)
    highlights = Image.blend(image, solid(image, highlight_color), amount)

    image = Image.composite(shadows, image, inverse)
    image = Image.composite(highlights, image, gray)

    return image


def add_grain(image, amount):
    """Film grain. amount is 0 to 100."""

    if amount <= 0:
        return image

    sigma = 5 + amount / 100 * 55

    noise = Image.effect_noise(image.size, sigma).convert("RGB")

    return ImageChops.add(image, noise, 1.0, -128)


def add_glow(image, amount, radius):
    """Soft dreamy glow."""

    blurred = image.filter(ImageFilter.GaussianBlur(radius))
    screened = ImageChops.screen(image, blurred)

    return Image.blend(image, screened, amount)


def read_color():
    """Frame color from the form, always a safe hex color."""

    value = request.form.get("border_color", "#ffffff")

    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value or ""):
        value = "#ffffff"

    return ImageColor.getrgb(value)


# =========================================================
# IMAGE LOADING
# =========================================================

def load_uploaded_image(upload):
    """Load uploaded image and normalize orientation and color."""

    image = Image.open(upload.stream)

    # Apply EXIF orientation.
    image = ImageOps.exif_transpose(image)

    # Convert to RGB, including transparent image formats.
    if image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")

        background = Image.new(
            "RGBA",
            rgba.size,
            (255, 255, 255, 255)
        )

        image = Image.alpha_composite(
            background,
            rgba
        ).convert("RGB")

    else:
        image = image.convert("RGB")

    if image.width <= 0 or image.height <= 0:
        raise ValueError("Invalid image dimensions.")

    if image.width > MAX_DIMENSION or image.height > MAX_DIMENSION:
        raise ValueError("Image dimensions exceed the supported limit.")

    return image


# =========================================================
# TRANSFORM TOOLS
# Order: mirror -> rotate 90 -> straighten -> crop -> resize
# =========================================================

def apply_mirror(image):
    """Mirror left-right (flip tools are built from this + rotation)."""

    if flag("mirror"):
        return ImageOps.mirror(image)

    return image


def apply_rotation(image):
    """Rotate clockwise in 90-degree steps."""

    rotation = int(number("rotation", 0, -3600, 3600)) % 360

    if rotation == 90:
        return image.transpose(Image.Transpose.ROTATE_270)

    if rotation == 180:
        return image.transpose(Image.Transpose.ROTATE_180)

    if rotation == 270:
        return image.transpose(Image.Transpose.ROTATE_90)

    return image


def apply_straighten(image):
    """Rotate by a small angle and cut away the empty corners."""

    angle = number("straighten", 0, -45, 45)

    if abs(angle) < 0.05:
        return image

    width, height = image.size
    radians = math.radians(abs(angle))

    # Largest centered rectangle (same shape) that fits in the rotated image.
    scale = min(
        width / (width * math.cos(radians) + height * math.sin(radians)),
        height / (width * math.sin(radians) + height * math.cos(radians))
    )

    rotated = image.rotate(
        -angle,
        expand=True,
        resample=Image.Resampling.BICUBIC,
        fillcolor=(255, 255, 255)
    )

    new_width = max(1, int(width * scale))
    new_height = max(1, int(height * scale))

    left = (rotated.width - new_width) // 2
    top = (rotated.height - new_height) // 2

    return rotated.crop((
        left,
        top,
        left + new_width,
        top + new_height
    ))


def apply_crop(image):
    """Crop using fractions (0 to 1) of the current image."""

    crop_w = number("crop_w", 0, 0, 1)
    crop_h = number("crop_h", 0, 0, 1)

    if crop_w <= 0 or crop_h <= 0:
        return image

    crop_x = number("crop_x", 0, 0, 1)
    crop_y = number("crop_y", 0, 0, 1)

    width, height = image.size

    left = int(round(crop_x * width))
    top = int(round(crop_y * height))
    right = min(width, int(round((crop_x + crop_w) * width)))
    bottom = min(height, int(round((crop_y + crop_h) * height)))

    if right - left < 1 or bottom - top < 1:
        return image

    return image.crop((left, top, right, bottom))


def apply_resize(image):
    """Resize image to requested dimensions."""

    width = int(number("resize_width", 0, 0, MAX_DIMENSION))
    height = int(number("resize_height", 0, 0, MAX_DIMENSION))

    if width <= 0 or height <= 0:
        return image

    if (width, height) == image.size:
        return image

    return image.resize(
        (width, height),
        Image.Resampling.LANCZOS
    )


def apply_corners(image):
    """Rounded corners. Corners are filled with the frame color."""

    percent = number("corner_radius", 0, 0, 100)

    if percent <= 0:
        return image

    width, height = image.size
    radius = int(min(width, height) / 2 * percent / 100)

    if radius < 1:
        return image

    # Smooth edges on normal sizes, plain on huge images (speed).
    ss = 3 if longest(image) <= 2000 else 1

    mask = Image.new("L", (width * ss, height * ss), 0)

    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width * ss - 1, height * ss - 1),
        radius=radius * ss,
        fill=255
    )

    if ss > 1:
        mask = mask.resize((width, height), Image.Resampling.LANCZOS)

    background = Image.new("RGB", image.size, read_color())

    return Image.composite(image, background, mask)


def apply_border(image):
    """Add a colored frame around the image."""

    percent = number("border_size", 0, 0, 100)

    pixels = int(longest(image) * 0.12 * percent / 100)

    if pixels < 1:
        return image

    return ImageOps.expand(image, border=pixels, fill=read_color())


# =========================================================
# ADJUSTMENTS
# =========================================================

def apply_auto_enhance(image):

    if not flag("auto_enhance"):
        return image

    return ImageOps.autocontrast(image, cutoff=1)


def apply_brightness(image):

    factor = number("brightness", 50, 0, 100) / 50

    return ImageEnhance.Brightness(image).enhance(factor)


def apply_contrast(image):

    factor = number("contrast", 50, 0, 100) / 50

    return ImageEnhance.Contrast(image).enhance(factor)


def apply_saturation(image):

    factor = number("saturation", 50, 0, 100) / 50

    return ImageEnhance.Color(image).enhance(factor)


def apply_vibrance(image):
    """Boost weak colors more than strong colors."""

    value = number("vibrance", 50, 0, 100)

    if value == 50:
        return image

    k = (value - 50) / 50

    hue, sat, val = image.convert("HSV").split()

    if k > 0:
        sat = sat.point(table(
            lambda p: p + k * 0.8 * (255 - p) * (1 - p / 255)
        ))
    else:
        sat = sat.point(table(lambda p: p * (1 + k * 0.8)))

    return Image.merge("HSV", (hue, sat, val)).convert("RGB")


def apply_hue(image):

    value = number("hue", 0, 0, 100)

    if value <= 0:
        return image

    shift = int(value / 100 * 256)

    hue, sat, val = image.convert("HSV").split()

    hue = hue.point([(i + shift) % 256 for i in range(256)])

    return Image.merge("HSV", (hue, sat, val)).convert("RGB")


def apply_highlights(image):
    """Below 50 darkens bright areas, above 50 brightens them."""

    value = number("highlights", 50, 0, 100)

    if value == 50:
        return image

    gray = ImageOps.grayscale(image)
    mask = gray.point(table(lambda p: 255 * (p / 255) ** 2))

    color = (255, 255, 255) if value > 50 else (0, 0, 0)
    amount = abs(value - 50) / 50 * 0.5

    toned = Image.blend(image, solid(image, color), amount)

    return Image.composite(toned, image, mask)


def apply_shadow(image):

    shadow = number("shadow", 50, 0, 100)

    if shadow == 50:
        return image

    gray = ImageOps.grayscale(image)

    shadow_mask = gray.point(
        table(lambda p: 255 * (1 - p / 255) ** 1.5)
    )

    if shadow > 50:
        overlay = (255, 255, 255)
        amount = (shadow - 50) / 50 * 0.65
    else:
        overlay = (0, 0, 0)
        amount = (50 - shadow) / 50 * 0.65

    blended = Image.blend(image, solid(image, overlay), amount)

    return Image.composite(blended, image, shadow_mask)


def apply_temperature(image):

    temperature = number("temperature", 50, 0, 100)

    if temperature == 50:
        return image

    shift = (temperature - 50) * 0.65

    return map_channels(
        image,
        red=lambda p: p + shift,
        blue=lambda p: p - shift
    )


def apply_tint(image):
    """Green (low) to magenta (high)."""

    value = number("tint", 50, 0, 100)

    if value == 50:
        return image

    shift = (value - 50) * 0.5

    return map_channels(image, green=lambda p: p - shift)


def apply_clarity(image):
    """Local contrast, makes details pop."""

    value = number("clarity", 0, 0, 100)

    if value <= 0:
        return image

    return image.filter(ImageFilter.UnsharpMask(
        radius=max(2, longest(image) / 120),
        percent=int(value * 1.6),
        threshold=0
    ))


def apply_sharpness(image):

    factor = number("sharpness", 20, 0, 100) / 20

    return ImageEnhance.Sharpness(image).enhance(factor)


def apply_blur(image):

    blur = number("blur", 0, 0, 100)

    if blur <= 0:
        return image

    # Radius depends on image size, so preview matches download.
    radius = blur / 100 * 0.02 * longest(image)

    return image.filter(ImageFilter.GaussianBlur(radius=radius))


def apply_glow(image):

    value = number("glow", 0, 0, 100)

    if value <= 0:
        return image

    return add_glow(image, value / 100 * 0.7, longest(image) / 70)


def apply_fade(image):
    """Lifts the blacks for a matte look."""

    value = number("fade", 0, 0, 100)

    if value <= 0:
        return image

    lift = value / 100 * 70
    scale = (255 - lift) / 255

    return apply_lut(image, lambda p: p * scale + lift)


def apply_vignette(image):

    vignette = number("vignette", 0, 0, 100)

    if vignette <= 0:
        return image

    strength = vignette / 100

    # Built-in gradient: 0 in the center, 255 at the edges (very fast).
    mask = Image.radial_gradient("L").resize(
        image.size,
        Image.Resampling.BILINEAR
    )

    mask = mask.point(table(
        lambda p: 255 * min(
            1,
            max(0, (p / 255 - 0.25) / 0.75) * strength
        )
    ))

    return Image.composite(solid(image, (0, 0, 0)), image, mask)


def apply_grain(image):

    return add_grain(image, number("grain", 0, 0, 100))


# =========================================================
# EFFECTS
# =========================================================

def fx_cool(img):

    return map_channels(
        img,
        red=lambda p: p * 0.92,
        blue=lambda p: p * 1.10
    )


def fx_sepia(img):

    gray = ImageOps.grayscale(img)

    return ImageOps.colorize(gray, "#3b2418", "#f4dfb5").convert("RGB")


def fx_negative(img):

    return ImageOps.invert(img)


def fx_grayscale(img):

    return ImageOps.grayscale(img).convert("RGB")


def fx_blackwhite(img):

    gray = ImageOps.grayscale(img)

    return gray.point(table(lambda p: 255 if p >= 128 else 0)).convert("RGB")


def fx_cinematic(img):

    img = map_channels(
        img,
        red=lambda p: p * 1.04 + 3,
        blue=lambda p: p * 1.08 + 2
    )

    img = ImageEnhance.Contrast(img).enhance(1.12)

    return ImageEnhance.Color(img).enhance(0.85)


def fx_vintage(img):

    gray = ImageOps.grayscale(img)

    toned = ImageOps.colorize(gray, "#49352a", "#e8d4a9").convert("RGB")

    return Image.blend(img, toned, 0.45)


def fx_retro(img):

    img = ImageEnhance.Color(img).enhance(1.18)
    img = ImageEnhance.Contrast(img).enhance(0.92)

    return map_channels(
        img,
        red=lambda p: p * 1.05 + 5,
        blue=lambda p: p * 0.94
    )


def fx_tealorange(img):

    img = split_tone(img, (0, 110, 130), (255, 150, 60), 0.30)
    img = ImageEnhance.Contrast(img).enhance(1.12)

    return ImageEnhance.Color(img).enhance(1.15)


def fx_film(img):

    img = map_channels(
        img,
        red=lambda p: p * 0.96 + 18,
        green=lambda p: p * 0.92 + 16,
        blue=lambda p: p * 0.85 + 20
    )

    img = ImageEnhance.Color(img).enhance(0.9)

    return add_grain(img, 35)


def fx_cyberpunk(img):

    img = split_tone(img, (40, 0, 140), (255, 40, 180), 0.35)
    img = ImageEnhance.Contrast(img).enhance(1.2)

    return ImageEnhance.Color(img).enhance(1.35)


def fx_vaporwave(img):

    img = split_tone(img, (90, 30, 160), (255, 150, 210), 0.35)
    img = apply_lut(img, lambda p: p * 0.9 + 22)

    return ImageEnhance.Color(img).enhance(1.2)


def fx_goldenhour(img):

    img = map_channels(
        img,
        red=lambda p: p * 1.10 + 8,
        green=lambda p: p * 1.03,
        blue=lambda p: p * 0.82
    )

    img = ImageEnhance.Brightness(img).enhance(1.05)

    return ImageEnhance.Contrast(img).enhance(1.05)


def fx_moody(img):

    img = ImageEnhance.Color(img).enhance(0.7)
    img = ImageEnhance.Contrast(img).enhance(1.15)
    img = ImageEnhance.Brightness(img).enhance(0.92)

    return split_tone(img, (10, 50, 60), (210, 170, 130), 0.22)


def fx_noir(img):

    gray = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=2)

    return ImageEnhance.Contrast(gray.convert("RGB")).enhance(1.35)


def fx_duotone(img):

    gray = ImageOps.grayscale(img)

    return ImageOps.colorize(gray, "#241058", "#ff7ac6").convert("RGB")


def fx_matte(img):

    img = apply_lut(img, lambda p: p * (215 / 255) + 40)
    img = ImageEnhance.Color(img).enhance(0.9)

    return ImageEnhance.Contrast(img).enhance(0.95)


def fx_polaroid(img):

    img = map_channels(
        img,
        red=lambda p: p * 0.94 + 14,
        green=lambda p: p * 0.92 + 14,
        blue=lambda p: p * 0.84 + 20
    )

    img = ImageEnhance.Color(img).enhance(0.9)

    return ImageEnhance.Contrast(img).enhance(1.05)


def fx_dreamy(img):

    img = add_glow(img, 0.6, longest(img) / 60)
    img = ImageEnhance.Brightness(img).enhance(1.05)
    img = ImageEnhance.Color(img).enhance(1.1)

    return ImageEnhance.Contrast(img).enhance(0.95)


def fx_pixelate(img):

    width, height = img.size
    block = max(4, int(max(width, height) / 90))

    small = img.resize(
        (max(1, width // block), max(1, height // block)),
        Image.Resampling.BILINEAR
    )

    return small.resize((width, height), Image.Resampling.NEAREST)


def fx_posterize(img):

    img = ImageOps.posterize(img, 3)

    return ImageEnhance.Color(img).enhance(1.2)


def fx_glitch(img):

    width, height = img.size
    shift = max(2, int(max(width, height) * 0.012))

    red, green, blue = img.split()

    red = ImageChops.offset(red, -shift, 0)
    blue = ImageChops.offset(blue, shift, 0)

    img = Image.merge("RGB", (red, green, blue))

    # Fixed seed: preview and download get the same glitch bands.
    rng = random.Random(42)

    for _ in range(4):

        top = int(rng.random() * height * 0.9)
        band_height = max(1, int(height * (0.02 + rng.random() * 0.04)))
        offset = int((rng.random() - 0.5) * width * 0.1)

        band = img.crop((0, top, width, min(height, top + band_height)))
        band = ImageChops.offset(band, offset, 0)

        img.paste(band, (0, top))

    return img


def fx_sketch(img):

    gray = ImageOps.grayscale(img)

    smooth = gray.filter(
        ImageFilter.GaussianBlur(max(1, longest(img) / 800))
    )

    edges = smooth.filter(ImageFilter.FIND_EDGES)
    edges = edges.point(table(lambda p: p * 4))

    sketch = ImageOps.autocontrast(ImageOps.invert(edges), cutoff=2)

    return sketch.convert("RGB")


def fx_solarize(img):

    return ImageOps.solarize(img, 128)


EFFECTS = {
    "cool": fx_cool,
    "sepia": fx_sepia,
    "negative": fx_negative,
    "grayscale": fx_grayscale,
    "blackwhite": fx_blackwhite,
    "cinematic": fx_cinematic,
    "vintage": fx_vintage,
    "retro": fx_retro,
    "tealorange": fx_tealorange,
    "film": fx_film,
    "cyberpunk": fx_cyberpunk,
    "vaporwave": fx_vaporwave,
    "goldenhour": fx_goldenhour,
    "moody": fx_moody,
    "noir": fx_noir,
    "duotone": fx_duotone,
    "matte": fx_matte,
    "polaroid": fx_polaroid,
    "dreamy": fx_dreamy,
    "pixelate": fx_pixelate,
    "posterize": fx_posterize,
    "glitch": fx_glitch,
    "sketch": fx_sketch,
    "solarize": fx_solarize,
}


def apply_effect(image):

    name = (request.form.get("effect", "none") or "none").lower()
    function = EFFECTS.get(name)

    if function is None:
        return image

    strength = number("effect_strength", 100, 0, 100) / 100

    if strength <= 0:
        return image

    result = function(image).convert("RGB")

    if strength >= 1:
        return result

    return Image.blend(image, result, strength)


# =========================================================
# MAIN IMAGE PROCESSING PIPELINE
# =========================================================

def process_image(upload):

    image = load_uploaded_image(upload)

    # 1. Geometry: mirror, rotate, straighten.
    image = apply_mirror(image)
    image = apply_rotation(image)
    image = apply_straighten(image)

    # The crop tool asks for the image with geometry only.
    if flag("geometry_only"):
        return image

    # 2. Crop and resize.
    image = apply_crop(image)
    image = apply_resize(image)

    # 3. Adjustments.
    image = apply_auto_enhance(image)
    image = apply_brightness(image)
    image = apply_contrast(image)
    image = apply_saturation(image)
    image = apply_vibrance(image)
    image = apply_hue(image)
    image = apply_highlights(image)
    image = apply_shadow(image)
    image = apply_temperature(image)
    image = apply_tint(image)
    image = apply_clarity(image)
    image = apply_sharpness(image)
    image = apply_blur(image)
    image = apply_glow(image)
    image = apply_fade(image)
    image = apply_vignette(image)
    image = apply_grain(image)

    # 4. Creative effect.
    image = apply_effect(image)

    # 5. Rounded corners and frame (last, so effects don't touch them).
    image = apply_corners(image)
    image = apply_border(image)

    return image


# =========================================================
# IMAGE RESPONSE
# =========================================================

def create_image_response(image, is_download):

    output = BytesIO()

    if is_download:

        image.save(
            output,
            format="JPEG",
            quality=95,
            optimize=True
        )

    else:

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
# PREVIEW ROUTE
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

    except Exception as error:

        app.logger.exception("Preview processing failed")

        return jsonify({
            "error": str(error) or "The image preview could not be processed."
        }), 400


# =========================================================
# DOWNLOAD ROUTE
# =========================================================

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

    except Exception as error:

        app.logger.exception("Download processing failed")

        return jsonify({
            "error": str(error) or "The image could not be downloaded."
        }), 400


# =========================================================
# ERROR HANDLERS
# =========================================================

@app.errorhandler(413)
def file_too_large(error):

    return jsonify({
        "error": "Image is too large. Maximum size is 25 MB."
    }), 413


@app.errorhandler(500)
def internal_server_error(error):

    app.logger.exception("Internal server error")

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