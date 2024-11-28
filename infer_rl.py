import torch
import json
import time
import numpy as np

from stable_baselines3 import DDPG, DQN
from stable_baselines3.common.callbacks import BaseCallback, CheckpointCallback

from env import Environment

train_file_list = [f"dataset/train-{i}" for i in range(1, 51)]

chunk_sizes = []
for file in train_file_list:
    with open(f"{file}/index.json") as f:
        index = json.load(f)
        chunk_sizes.append(int(index["config"]["chunk_size"]))


class TensorboardCallback(BaseCallback):
    def __init__(self, verbose=0):
        super(TensorboardCallback, self).__init__(verbose)

    def _on_step(self):
        self.logger.record("train/reward", self.locals["rewards"])
        return True


checkpoint_callback = CheckpointCallback(
    save_freq=1, save_path="./logs/", name_prefix="ddpg_nas"
)

# Initialize environment
env = Environment(train_file_list, chunk_sizes)

# Load or initialize the model
model = DDPG.load("ddpg_transformer_100")  # Load the pre-trained model

# Test the model
test_chunk_size = 15749
observation, _ = env.reset(
    test_chunk_size
)  # Reset the environment with the test chunk size

action, _states = model.predict(observation, deterministic=True)
for i in range(len(action)):
    action[i] = int(action[i])
print(f"Predicted action for test_chunk_size {test_chunk_size}: {action}")

# Optional: Train the model further
# Uncomment the lines below if you want to continue training
# model.learn(total_timesteps=50, callback=[TensorboardCallback(), checkpoint_callback])
# model.save("ddpg_transformer")