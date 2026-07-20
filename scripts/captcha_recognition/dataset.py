from torch.utils.data import Dataset
from PIL import Image
import torch
import os


class CaptchaDataset(Dataset):
    def __init__(self, data_dir, transform, characters):
        self.file_list = list()
        files = os.listdir(data_dir)
        for file in files:
            path = os.path.join(data_dir, file)
            self.file_list.append(path)

        self.transform = transform
        self.char2int = {}

        for i, char in enumerate(characters):
            self.char2int[char] = i

    def __len__(self):
        return len(self.file_list)

    def __getitem__(self, index):
        file_path = self.file_list[index]
        image = Image.open(file_path).convert("L")
        image = self.transform(image)
        label_char = os.path.basename(file_path).split("_")[0]

        label = list()
        for char in label_char:
            label.append(self.char2int[char])

        label = torch.tensor(label, dtype=torch.long)
        return image, label


from torch.utils.data import DataLoader
from torchvision import transforms
import json

if __name__ == "__main__":
    with open("config.json", "r") as f:
        config = json.load(f)

    height = config["resize_height"]
    width = config["resize_width"]

    transform = transforms.Compose([
        transforms.Resize((height, width)),
        transforms.ToTensor()])

    data_path = config["train_data_path"]
    characters = config["characters"]
    batch_size = config["batch_size"]
    epoch_num = config["epoch_num"]

    dataset = CaptchaDataset(data_path, transform, characters)
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    for epoch in range(epoch_num):
        print(f"epoch = {epoch}")
        for batch_idx, (data, label) in enumerate(dataloader):
            print(f"batch_idx = {batch_idx}, label = {label}")
