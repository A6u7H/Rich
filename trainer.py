import os
import torch
import logging
import numpy as np
import matplotlib.pyplot as plt

from tqdm import tqdm
from typing import Callable, Any, List
from torch.nn import functional as F
from torch.utils.data import DataLoader

logger = logging.getLogger(__name__)


class Trainer:
    def __init__(
        self,
        gan_model,
        max_epoch: int = 10000,
        display_step: int = 1000,
        critic_step: int = 8,
        device: str = "cuda",
        save_path: str = ".",
        freeze_generator: bool = False,
        neptune_logger: Callable = None,
    ):
        self.gan_model = gan_model
        self.max_epoch = max_epoch
        self.display_step = display_step
        self.critic_step = critic_step
        self.device = device
        self.save_path = save_path
        self.freeze_generator = freeze_generator
        self.neptune_logger = neptune_logger
        (
            self.generator_optimizer,
            self.critic_optimizer,
        ) = gan_model.configure_optimizers()

    def calculate_inference_time(self, dummy_shape, repetitions):
        dummy_input = torch.randn(*dummy_shape, dtype=torch.float).to(self.device)
        # INIT LOGGERS
        starter, ender = torch.cuda.Event(enable_timing=True), torch.cuda.Event(
            enable_timing=True
        )
        timings = np.zeros((repetitions, 1))
        # GPU-WARM-UP
        for _ in range(10):
            _ = self.gan_model.generator_model(dummy_input)
        with torch.no_grad():
            for rep in range(repetitions):
                starter.record()
                _ = self.gan_model.generator_model(dummy_input)
                ender.record()
                # WAIT FOR GPU SYNC
                torch.cuda.synchronize()
                curr_time = starter.elapsed_time(ender)
                timings[rep] = curr_time
        mean_syn = np.sum(timings) / repetitions
        std_syn = np.std(timings)
        self.neptune_logger.log_metric("mean_time", mean_syn)
        self.neptune_logger.log_metric("std_time", std_syn)

    def fit(self, train_loader=None, validation_loader=None, start=0):
        for epoch in tqdm(range(self.max_epoch)):
            for iteration, batch in enumerate(train_loader):
                for _ in range(self.critic_step):
                    self.critic_optimizer.zero_grad()

                    critic_losses = self.gan_model.train_critic(batch)
                    critic_total_loss = critic_losses["C/loss"]
                    critic_total_loss.backward(retain_graph=True)

                    self.critic_optimizer.step()

                self.generator_optimizer.zero_grad()
                
                if not self.freeze_generator:
                    generator_losses = self.gan_model.train_generator(batch)
                    generator_total_loss = generator_losses["G/loss"]
                    generator_total_loss.backward(retain_graph=True)
                    self.generator_optimizer.step()

                    for key, value in generator_losses.items():
                        self.neptune_logger.log_metric(key, value.item())

                for key, value in critic_losses.items():
                    self.neptune_logger.log_metric(key, value.item())

                if iteration % self.display_step == 0:
                    name = f"epoch_{epoch}_iter_{iteration}.pt"
                    if not os.path.exists(self.save_path):
                        os.mkdir(self.save_path)
                    save_path_with_iter = os.path.join(self.save_path, name)
                    self.gan_model.save_models(save_path_with_iter, epoch)
                    outputs = self.gan_model.evaluate(validation_loader)
                    self.neptune_logger.log_image(
                        "Histograms", outputs["histogram"]
                    )
                    self.neptune_logger.log_metric("rocauc", outputs["rocauc"])
