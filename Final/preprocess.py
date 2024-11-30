import pytorch_lightning as pl
from lightning.data import (
    StreamingDataset,
    StreamingDataLoader,
)
from litdata import TokensLoader


class DataModule(pl.LightningDataModule):
    def __init__(self, train_config, preprocess_config):
        super().__init__()
        self.train_config = train_config
        self.preprocess_config = preprocess_config

    def setup(self, random_float, stage: str = None):
        self.vocab_size = self.preprocess_config["vocab_size"]
        self.train = StreamingDataset(
            input_dir=self.train_config["train_bin_path"],
            item_loader=TokensLoader(
                block_size=self.train_config["context_length"] + 1
            ),
            shuffle=False,
            subsample=random_float,
        )
        self.val = StreamingDataset(
            input_dir=self.train_config["train_bin_path"],
            item_loader=TokensLoader(
                block_size=self.train_config["context_length"] + 1
            ),
            shuffle=False,
            subsample=0.1,
        )
        self.test = self.train

    def train_dataloader(self):
        return StreamingDataLoader(
            self.train,
            batch_size=self.train_config["batch_size"],
            pin_memory=True,
            num_workers=1,
        )

    def val_dataloader(self):
        return StreamingDataLoader(
            self.val,
            batch_size=self.train_config["batch_size"],
            pin_memory=True,
            num_workers=1,
        )

    def test_dataloader(self):
        return StreamingDataLoader(
            self.test,
            batch_size=self.train_config["batch_size"],
            pin_memory=True,
            num_workers=1,
        )
