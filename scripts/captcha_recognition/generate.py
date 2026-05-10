from captcha.image import ImageCaptcha
import random


def generate_data(num, count, chars, path, width, height):
    for i in range(num):
        generator = ImageCaptcha(width=width, height=height)
        random_str = ""
        for j in range(count):
            choose = random.choice(chars)
            random_str += choose

            img = generator.generate_image(random_str)
            generator.create_noise_dots(img, "#000000", 4, 40)
            generator.create_noise_curve(img, "#000000")
            file_name = path + random_str + "_" + str(i) + ".jpg"
            img.save(file_name)


import json
import os

if __name__ == "__main__":
    with open("config.json", "r") as f:
        config = json.load(f)

    train_data_path = config["train_data_path"]
    test_data_path = config["test_data_path"]

    train_num = config["train_num"]
    test_num = config["test_num"]

    characters = config["characters"]
    digit_num = config["digit_num"]
    img_width = config["img_width"]
    img_height = config["img_height"]

    if not os.path.exists(train_data_path):
        os.makedirs(train_data_path)
    if not os.path.exists(test_data_path):
        os.makedirs(test_data_path)

    generate_data(train_num, digit_num, characters, train_data_path, img_width, img_height)
    generate_data(test_num, digit_num, characters, test_data_path, img_width, img_height)
