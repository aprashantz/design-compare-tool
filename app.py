from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
import os
import json
import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim
from flask_cors import CORS  # Add this import
import uuid  # Add this import
from datetime import datetime  # Add this import at the top of the file

app = Flask(__name__)
CORS(app)  # Add this line to enable CORS

# Configuration
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max-limit


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload_files():
    try:
        if 'session_id' not in request.form:
            return jsonify({'error': 'Missing session ID'}), 400

        session_id = request.form['session_id']
        single_comparison_dir = os.path.join(
            app.config['UPLOAD_FOLDER'], 'single_comparisons', session_id)
        os.makedirs(single_comparison_dir, exist_ok=True)

        print("Request method:", request.method)
        print("Content-Type:", request.content_type)
        print("Files received:", request.files)
        print("Form data:", request.form)

        if 'figma_image' not in request.files or 'built_image' not in request.files:
            missing = [f for f in ['figma_image', 'built_image']
                       if f not in request.files]
            return jsonify({'error': f'Missing files: {", ".join(missing)}'}), 400

        figma_image = request.files['figma_image']
        built_image = request.files['built_image']

        print("Figma image:", figma_image.filename)
        print("Built image:", built_image.filename)

        if figma_image.filename == '' or built_image.filename == '':
            empty = [f for f in ['figma_image', 'built_image']
                     if request.files[f].filename == '']
            return jsonify({'error': f'Empty filenames: {", ".join(empty)}'}), 400

        figma_filename = secure_filename(figma_image.filename)
        built_filename = secure_filename(built_image.filename)

        figma_path = os.path.join(single_comparison_dir, figma_filename)
        built_path = os.path.join(single_comparison_dir, built_filename)

        figma_image.save(figma_path)
        built_image.save(built_path)

        comparison_result = compare_images(
            figma_path, built_path, single_comparison_dir)

        comparison_result['session_id'] = session_id

        return jsonify(comparison_result)

    except Exception as e:
        print("Error occurred:", str(e))
        return jsonify({'error': str(e)}), 500


@app.route('/bulk_upload', methods=['POST'])
def bulk_upload_files():
    try:
        if 'screens' not in request.form:
            return jsonify({'error': 'Missing screens data'}), 400

        screens = json.loads(request.form['screens'])
        now = datetime.now()
        bulk_comparison_dir = os.path.join(
            app.config['UPLOAD_FOLDER'], 'bulk_comparisons', now.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(bulk_comparison_dir, exist_ok=True)

        results = []

        for screen in screens:
            figma_image = request.files.get(screen['figma_screenshot'])
            app_image = request.files.get(screen['app_screenshot'])

            if not figma_image or not app_image:
                return jsonify({'error': f"Missing image for screen {screen['name']}"}), 400

            figma_filename = secure_filename(f"{screen['name']}_figma.png")
            app_filename = secure_filename(f"{screen['name']}_app.png")

            figma_path = os.path.join(bulk_comparison_dir, figma_filename)
            app_path = os.path.join(bulk_comparison_dir, app_filename)

            figma_image.save(figma_path)
            app_image.save(app_path)

            comparison_result = compare_images(
                figma_path, app_path, bulk_comparison_dir)
            comparison_result['screen_name'] = screen['name']
            results.append(comparison_result)

        return jsonify(results)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


def compare_images(figma_path, built_path, output_dir):
    # Read images
    figma_img = cv2.imread(figma_path)
    built_img = cv2.imread(built_path)

    # Ensure images are the same size
    figma_img = cv2.resize(figma_img, (built_img.shape[1], built_img.shape[0]))

    # Convert images to grayscale
    figma_gray = cv2.cvtColor(figma_img, cv2.COLOR_BGR2GRAY)
    built_gray = cv2.cvtColor(built_img, cv2.COLOR_BGR2GRAY)

    # Compute SSIM between the two images
    (score, diff) = ssim(figma_gray, built_gray, full=True)

    # The diff image contains the actual image differences
    diff = (diff * 255).astype("uint8")

    # Threshold the difference image, followed by finding contours
    thresh = cv2.threshold(
        diff, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    contours = cv2.findContours(
        thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = contours[0] if len(contours) == 2 else contours[1]

    # Create a mask image that we will use to visualize the differences
    mask = np.zeros(figma_img.shape, dtype='uint8')
    filled_after = figma_img.copy()

    for c in contours:
        area = cv2.contourArea(c)
        if area > 40:
            x, y, w, h = cv2.boundingRect(c)
            figma_img = cv2.rectangle(
                figma_img, (x, y), (x + w, y + h), (0, 255, 0), 2)
            built_img = cv2.rectangle(
                built_img, (x, y), (x + w, y + h), (0, 0, 255), 2)

            # Compare the region in both images
            figma_region = figma_gray[y:y+h, x:x+w]
            built_region = built_gray[y:y+h, x:x+w]

            if np.mean(figma_region) > np.mean(built_region):
                # More white in Figma, use green
                cv2.drawContours(filled_after, [c], 0, (0, 255, 0), -1)
            else:
                # More white in built, use red
                cv2.drawContours(filled_after, [c], 0, (0, 0, 255), -1)

    # Create the comparison image
    comparison = np.hstack((figma_img, built_img, filled_after))

    # Update the comparison image path
    comparison_filename = f'comparison_{datetime.now().strftime("%Y%m%d_%H%M%S")}.jpg'
    comparison_path = os.path.join(output_dir, comparison_filename)
    cv2.imwrite(comparison_path, comparison)

    return {
        'similarity': f'{score * 100:.2f}',
        'message': f'The images are {score * 100:.2f}% similar based on structural similarity.',
        'comparison_image': os.path.relpath(comparison_path, app.config['UPLOAD_FOLDER'])
    }


if __name__ == '__main__':
    app.run(debug=True)
