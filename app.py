from flask import Flask, render_template, request, jsonify
import numpy as np

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/calculate', methods=['POST'])
def calculate():
    data = request.json
    initial_roller_value = float(data['initial_roller_value'])
    end_roller_value = float(data['end_roller_value'])
    number_of_stitches = int(data['number_of_stitches'])
    decay_rate = float(data['decay_rate'])

    roller_values = [max(initial_roller_value * np.exp(-decay_rate * i), end_roller_value) for i in range(number_of_stitches + 1)]
    return jsonify(roller_values)

@app.route('/sizing', methods=['GET', 'POST'])
def sizing():
    if request.method == 'POST':
        try:
            data = request.get_json()
            original_width_px = float(data['original_width_px'])
            original_height_px = float(data['original_height_px'])
            final_width_in = float(data['final_width_in'])
            final_height_in = float(data['final_height_in'])

            # Calculate the machine's scale factors
            scale_horizontal = final_width_in / original_width_px
            scale_vertical = final_height_in / original_height_px

            # Compute the correction factor to get square stitches
            correction_factor = scale_horizontal / scale_vertical

            # Calculate new vertical resolution
            new_height_px = original_height_px * correction_factor

            # Prepare the results
            results = {
                "original_dimensions": f"{original_width_px} x {original_height_px} pixels",
                "scale_factors": f"horizontal = {scale_horizontal:.5f} in/px, vertical = {scale_vertical:.5f} in/px",
                "correction_factor": f"{correction_factor:.3f}",
                "new_dimensions": f"{original_width_px} x {round(new_height_px)} pixels"
            }

            return jsonify(results)
        except Exception as e:
            print(f"Error processing request: {e}")
            return jsonify({"error": "Invalid input"}), 400

    return render_template('sizing_form.html')

if __name__ == '__main__':
    app.run(debug=True)
