import torch
import torchmetrics
import torch.nn as nn
import pytorch_lightning as pl
import torchmetrics.text
import torchmetrics.text.perplexity
from model.Decoder import Decoder
from model.PositionalEncoding import Learned, RoPE, Cosine, RotaryEmbedding
from model.Loss import ChunkedCrossEntropyLoss, CrossEntropyLoss
from model.Normalizations import LayerNorm
from deepspeed.ops.adam import FusedAdam
from torch.optim import Adam


import math
from torch.optim.lr_scheduler import CosineAnnealingWarmRestarts


class CosineAnnealingWarmRestartsDecay(CosineAnnealingWarmRestarts):
    def __init__(
        self, optimizer, T_0, T_mult=1, eta_min=0, last_epoch=-1, verbose=False, decay=1
    ):
        super().__init__(
            optimizer,
            T_0,
            T_mult=T_mult,
            eta_min=eta_min,
            last_epoch=last_epoch,
            verbose=verbose,
        )
        self.decay = decay
        self.initial_lrs = self.base_lrs

    def step(self, epoch=None):
        if epoch == None:
            if self.T_cur + 1 == self.T_i:
                if self.verbose:
                    print("multiplying base_lrs by {:.4f}".format(self.decay))
                self.base_lrs = [base_lr * self.decay for base_lr in self.base_lrs]
        else:
            if epoch < 0:
                raise ValueError(
                    "Expected non-negative epoch, but got {}".format(epoch)
                )
            if epoch >= self.T_0:
                if self.T_mult == 1:
                    n = int(epoch / self.T_0)
                else:
                    n = int(
                        math.log(
                            (epoch / self.T_0 * (self.T_mult - 1) + 1), self.T_mult
                        )
                    )
            else:
                n = 0

            self.base_lrs = [
                initial_lrs * (self.decay**n) for initial_lrs in self.initial_lrs
            ]

        super().step(epoch)


class Transformer(pl.LightningModule):
    def __init__(self, config, vocabSize, dtype):
        super().__init__()
        self.config = config
        self.batchSize = config["batch_size"]
        self.contextLength = config["context_length"]
        self.embeddingDim = config["embedding_dimension"]
        self.numHeads = config["num_heads"]
        self.numLayers = config["num_layers"]
        self.dropout = config["dropout"]
        self.vocabSize = vocabSize
        self.external_dtype = dtype

        self.inputEmbed = nn.Embedding(
            self.vocabSize, self.embeddingDim, dtype=self.external_dtype
        )
        if config["positional_encoding"] == "rope":
            self.pe = RoPE(self.contextLength, self.embeddingDim, self.external_dtype)
        elif config["positional_encoding"] == "rotary":
            self.pe = RotaryEmbedding(
                config["embedding_dimension"] // config["num_heads"],
                base=10000,
                max_seq_len=config["context_length"],
                precision=self.external_dtype,
                save_inv_freqs=False,
            )
        else:
            self.pe = Learned(
                self.contextLength, self.embeddingDim, self.external_dtype
            )

        self.decoder = Decoder(
            self.batchSize,
            self.contextLength,
            self.embeddingDim,
            self.numHeads,
            self.numLayers,
            self.dropout,
            self.external_dtype,
            self.config,
        )
        self.final_norm = LayerNorm(self.embeddingDim, eps=self.config["norm_eps"])
        self.linear = nn.Linear(
            self.embeddingDim, self.vocabSize, bias=False, dtype=self.external_dtype
        )

        self.loss_fn = ChunkedCrossEntropyLoss(ignore_index=0)

        self.ppl = torchmetrics.text.Perplexity()
        self.loss1 = 0
        self.loss2 = 0
        self.loss3 = 0

    def forward(self, x):
        # print("\n<<< x before embed  >>>\n", x)
        x = self.inputEmbed(x)
        x = self.pe(x)
        # print("\n<<< x right after embed  >>>\n", x)
        x = self.decoder(x)

        x = self.final_norm(x)
        # print("\n<<< x right before final linear layer  >>>\n",x)
        x = self.linear(x)
        # print("\n<<< x right after final linear layer  >>>\n",x)
        return x

    def training_step(self, batch, batch_idx):
        x = batch[:, : self.contextLength]
        y = batch[:, 1:].long()
        output = self.forward(x)
        loss = self.loss_fn(
            output.reshape(output.shape[0] * output.shape[1], self.vocabSize), y
        )
        self.log("loss", loss, prog_bar=True, sync_dist=True)
        self.loss3 = self.loss2
        self.loss2 = self.loss1
        self.loss1 = loss
        return loss

    def validation_step(self, batch, batch_idx):
        x = batch[:, : self.contextLength]
        y = batch[:, 1:].long()

        # print("\n<<<  AT VAL  >>>\n",x.shape)
        # exit()
        output = self.forward(x)
        loss = self.loss_fn(
            output.reshape(output.shape[0] * output.shape[1], self.vocabSize), y
        )
        # accuracy = self.accuracy(output[:,-1,:], y[:,-1])
        # print(output)
        # print(y)
        # print(output.shape)
        # print(y.shape)
        # exit()
        val_ppl = self.ppl(output, y)
        dict_log = {
            "val_loss": loss,
            "val_ppl": val_ppl,
        }
        self.log_dict(dict_log, sync_dist=True)
        return loss

    def test_step(self, batch, batch_idx):
        x = batch[:, : self.contextLength]
        y = batch[:, 1:].long()
        output = self.forward(x)
        loss = self.loss_fn(
            output.reshape(output.shape[0] * output.shape[1], self.vocabSize), y
        )
        test_ppl = self.ppl(output, y)
        # accuracy = self.accuracy(output[:,-1, :], y[:,-1])
        # f1_score = self.f1_score(output[:,-1, :], y[:,-1])
        # precision = self.precision(output[:,-1, :], y[:,-1])
        # recall = self.recall(output[:,-1, :], y[:,-1])
        dict_log = {
            "test_loss": loss,
            "test_ppl": test_ppl,
            # "test_accuracy": accuracy,
            # "test_f1_score": f1_score,
            # "test_precision": precision,
            # "test_recall": recall,
        }
        self.log_dict(dict_log, sync_dist=True)
        return loss

    def predict_step(self, x):
        with torch.no_grad():
            output = self.forward(x)
        # output = nn.Softmax(dim=2)(output)
        return output

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(
            self.parameters(),
            lr=self.config["lr"],
            weight_decay=self.config["weight_decay"],
            betas=(0.9, 0.95),
            eps=1e-8,
        )
        # optimizer =FusedAdam(
        #     self.parameters(),
        #     lr=self.config["lr"],
        #     weight_decay=self.config["weight_decay"],
        #     betas=(0.9, 0.95),
        #     eps=1e-8,
        # )
        lr_scheduler = {
            "scheduler": CosineAnnealingWarmRestartsDecay(
                optimizer,
                T_0=self.config["T_0"],
                T_mult=self.config["T_mult"],
                eta_min=self.config["eta_min"],
                decay=self.config["decay"],
            ),
            "name": "lr_scheduler",
            "interval": "step",
        }
        return [optimizer], [lr_scheduler]
