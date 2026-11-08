
from flask import Flask, render_template, request, jsonify, send_file
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from io import BytesIO

app = Flask(__name__)


@app.route("/")
def home():
    return render_template("index.html")


def process_image(
    file,
    brightness=100,
    contrast=100,
    grayscale=False,
    black_white=False,
    blur=0,
    sharpness=1
):
    image = Image.open(file.stream)
    image = ImageOps.exif_transpose(image).convert("RGB")

    brightness = max(0, min(float(brightness), 200))
    contrast = max(0, min(float(contrast), 200))
    blur = max(0, min(float(blur), 10))
    sharpness = max(0, min(float(sharpness), 5))

    image = ImageEnhance.Brightness(image).enhance(brightness / 100)
    image = ImageEnhance.Contrast(image).enhance(contrast / 100)

    if black_white:
        image = ImageOps.grayscale(image)
        image = image.point(lambda pixel: 255 if pixel >= 128 else 0)
        image = image.convert("RGB")

    elif grayscale:
        image = ImageOps.grayscale(image).convert("RGB")

    if blur > 0:
        image = image.filter(
            ImageFilter.GaussianBlur(radius=blur)
        )

    if sharpness != 1:
        image = ImageEnhance.Sharpness(image).enhance(sharpness)

    return image


@app.route("/sharpen", methods=["POST"])
def sharpen_image():
    if "image" not in request.files:
        return jsonify({"error": "No image uploaded"}), 400

    file = request.files["image"]

    if file.filename == "":
        return jsonify({"error": "No image selected"}), 400

    try:
        amount = float(request.form.get("amount", 1.0))

        image = process_image(
            file,
            sharpness=amount
        )

        output = BytesIO()
        image.save(output, format="JPEG", quality=95)
        output.seek(0)

        return send_file(
            output,
            mimetype="image/jpeg",
            as_attachment=False,
            download_name="sharpened_image.jpg"
        )

    except (ValueError, TypeError):
        return jsonify({"error": "Invalid sharpness value"}), 400

    except Exception:
        app.logger.exception("Sharpness processing failed")
        return jsonify({"error": "Unable to process this image"}), 400


@app.route("/download", methods=["POST"])
def download_image():
    if "image" not in request.files:
        return jsonify({"error": "No image uploaded"}), 400

    file = request.files["image"]

    if file.filename == "":
        return jsonify({"error": "No image selected"}), 400

    try:
        grayscale = (
            request.form.get("grayscale", "false").lower() == "true"
        )

        black_white = (
            request.form.get("black_white", "false").lower() == "true"
        )

        image = process_image(
            file,
            brightness=request.form.get("brightness", 100),
            contrast=request.form.get("contrast", 100),
            grayscale=grayscale,
            black_white=black_white,
            blur=request.form.get("blur", 0),
            sharpness=request.form.get("sharpness", 1)
        )

        output = BytesIO()
        image.save(output, format="JPEG", quality=95)
        output.seek(0)

        return send_file(
            output,
            mimetype="image/jpeg",
            as_attachment=True,
            download_name="light_studio_edited_image.jpg"
        )

    except (ValueError, TypeError):
        return jsonify({"error": "Invalid editing values"}), 400

    except Exception:
        app.logger.exception("Image download processing failed")
        return jsonify(
            {"error": "Unable to download the edited image"}
        ), 400


if __name__ == "__main__":
    app.run(debug=True)

