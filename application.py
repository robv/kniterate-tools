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


if __name__ == '__main__':
    app.run(debug=True)
