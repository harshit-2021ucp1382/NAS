import torch
from model import Transformer
from config_reader import Config
from preprocess import DataModule
from utils.misc import measure_time
import pytorch_lightning as pl
from pytorch_lightning import Trainer
from pytorch_lightning.strategies import DeepSpeedStrategy
from pytorch_lightning.callbacks import LearningRateMonitor
from pytorch_lightning.profilers import PyTorchProfiler
from pytorch_lightning.loggers import TensorBoardLogger
from pytorch_lightning.callbacks import ModelCheckpoint


def train_model(action, train_folder):
    torch.set_float32_matmul_precision("high")
    lr_monitor = LearningRateMonitor(logging_interval="step")
    config = Config()

    config.train["num_heads"] = int(action[1])
    config.train["num_layers"] = int(action[0])
    config.train["embedding_dimension"] = config.train["num_heads"] * 64
    config.train["train_bin_path"] = train_folder

    start_time = measure_time()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger = TensorBoardLogger("logs/", name="transformer")
    version = logger.version
    profiler_log_dir = f"logs/profiler/version_{version}"
    profiler = PyTorchProfiler(
        on_trace_ready=torch.profiler.tensorboard_trace_handler(profiler_log_dir),
        trace_memory=True,
        export_to_chrome=True,
    )
    if config.deepspeed is not None:
        strategy = DeepSpeedStrategy(config=config.deepspeed)
    else:
        strategy = "ddp"
    dataModule = DataModule(config.train, config.preprocess)
    checkpoint = ModelCheckpoint(
        monitor="val_loss",
        dirpath=f"logs/checkpoints/",
        filename="checkpoint-step-{step:08d}",
        save_top_k=-1,
        mode="min",
    )

    trainer = Trainer(
        accelerator="auto",
        devices=config.train["gpu_cores"],
        max_epochs=config.train["max_epochs"],
        max_steps=config.train["max_iterations"],
        # val_check_interval=config.train["eval_every"],
        min_epochs=config.train["min_epochs"],
        precision=config.train["precision"],
        log_every_n_steps=config.train["log_steps"],
        strategy=strategy,
        logger=logger,
        profiler=profiler,
        callbacks=[lr_monitor, checkpoint],
        gradient_clip_val=config.train["gradient_clip_val"],
    )

    print(f"[{measure_time(start_time)}]Loading data on {trainer.global_rank}...")
    dataModule.setup()
    print(f"[{measure_time(start_time)}]Data loaded on {trainer.global_rank}.")

    print(f"[{measure_time(start_time)}]Initializing model on {trainer.global_rank}...")
    model = (
        Transformer(config.train, dataModule.vocab_size, config.dtype)
        .to(device)
        .to(config.dtype)
    )
    print(f"[{measure_time(start_time)}]Model initialized on {trainer.global_rank}.")

    print(f"[{measure_time(start_time)}]Starting training on {trainer.global_rank}...")
    trainer.fit(model, dataModule)
    print(f"[{measure_time(start_time)}]Training complete on {trainer.global_rank}.")

    loss1 = model.loss1.cpu().item()
    loss2 = model.loss2.cpu().item()
    loss3 = model.loss3.cpu().item()
    loss = (loss1 + loss2 + loss3) / 3
    with torch.no_grad():
        torch.cuda.empty_cache()
    return loss
