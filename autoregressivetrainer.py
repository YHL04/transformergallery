

import torch
import torch.nn as nn
from torch.optim import Adam

import time

from optim_schedule import ScheduledOptim


class AutoregressiveTrainer:

    def __init__(self,
                 model,
                 dataloader,
                 lr,
                 batch_size,
                 seqlen,
                 burnin,
                 rollout,
                 warmup_steps,
                 device,
                 ):

        self.model = nn.DataParallel(model)
        if device == "cuda":
            self.model = self.model.cuda()
        self.opt = Adam(self.model.parameters(), lr=lr)
        self.opt_schedule = ScheduledOptim(self.opt, self.model.module.d_model, n_warmup_steps=warmup_steps)

        self.criterion = nn.CrossEntropyLoss(ignore_index=-1)

        self.dataloader = dataloader
        self.batch_size = batch_size

        self.seqlen = seqlen
        self.burnin = burnin
        self.rollout = rollout
        self.length = burnin + rollout

        self.log = open(f"logs/lm", "w")
        self.start = time.time()
        self.updates = 0

    def run_epoch(self, epoch):
        """
        Main training loop for one epoch.
        Logs and prints time and loss.
        """

        for i, batch in enumerate(self.dataloader):
            if batch.size(0) != self.batch_size:
                continue

            # batch (bsz, block_len, seq_len)
            loss = self.step(batch)

            self.log.write(f"{time.time() - self.start}, {loss}\n")
            self.log.flush()

            self.updates += 1

            if i % 5 == 0:
                print(f"Epoch: {epoch} \t "
                      f"Time: {time.time() - self.start} \t "
                      f"Loss: {loss} \t "
                      f"Sec/Update: {(time.time() - self.start) / self.updates}")

            if i % 10000 == 0:
                torch.save(self.model, "saved/final")

    def step(self, batch):
        """
        A training step that does backpropagation at each rollout timestep.
        To train long sequence transformer models such as Transformer XL.
        """
        total_loss = 0
        inputs, targets = batch[:, :, :-1], batch[:, :, 1:]

        self.model.module.reset()
        for t in range(self.rollout):
            expected = self.model(inputs[:, t, :])
            loss = self.cross_entropy_loss(expected, targets[:, t, :])
            self.opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), 0.1)
            self.opt.step()

            total_loss += loss.item()

        return total_loss / (self.rollout * self.seqlen)

    def cross_entropy_loss(self, expected, target):
        """
        cross entropy loss

        Parameters:
            expected (batch_size, max_len, vocab_size)
            target (batch_size, max_len)
        """
        assert target.shape == (target.size(0), target.size(1))
        assert expected.shape == (target.size(0), target.size(1), expected.size(2))

        loss = self.criterion(expected.reshape(-1, expected.size(2)), target.reshape(-1))
        return loss.mean()
