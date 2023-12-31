import torch
import torch.nn as nn
import torch.nn.functional as F
from models.act_funcs import act_selector
import math


class Reshape(nn.Module):
    def __init__(self, dims):
        super(Reshape, self).__init__()
        self.dims = dims

    def forward(self, x):
        return x.reshape(*self.dims)


class DoubleConv3x3(nn.Module):
    def __init__(self, in_channels, out_channels, hidden_channels=None, act_func="relu"):
        super(DoubleConv3x3, self).__init__()
        hidden_channels = hidden_channels or out_channels
        self.conv = nn.Sequential(
            nn.Conv2d(in_channels, hidden_channels, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(hidden_channels),
            act_selector(act_func),
            nn.Conv2d(hidden_channels, out_channels, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(out_channels),
            act_selector(act_func)
        )

    def forward(self, x):
        return self.conv(x)


class DownSample(nn.Module):
    def __init__(self, in_channels, out_channels, hidden_channels=None, act_func="relu"):
        super(DownSample, self).__init__()
        self.down_conv = nn.Sequential(
            nn.MaxPool2d(kernel_size=2),
            DoubleConv3x3(in_channels, out_channels, hidden_channels=hidden_channels, act_func=act_func)
        )

    def forward(self, x):
        return self.down_conv(x)


class Attention_block(nn.Module):
    def __init__(self, F_g, F_l, F_int):
        super(Attention_block, self).__init__()
        self.W_g = nn.Sequential(
            nn.Conv2d(F_g, F_int, kernel_size=1, stride=1, padding=0, bias=False),
            nn.BatchNorm2d(F_int)
        )

        self.W_x = nn.Sequential(
            nn.Conv2d(F_l, F_int, kernel_size=1, stride=1, padding=0, bias=False),
            nn.BatchNorm2d(F_int)
        )

        self.psi = nn.Sequential(
            nn.Conv2d(F_int, 1, kernel_size=1, stride=1, padding=0, bias=False),
            nn.BatchNorm2d(1),
            nn.Sigmoid()
        )

        self.relu = nn.ReLU(inplace=True)

    def forward(self, g, x):
        g1 = self.W_g(g)
        x1 = self.W_x(x)
        psi = self.relu(g1 + x1)
        psi = self.psi(psi)

        return x * psi


class UpSample(nn.Module):
    """
    Args:
        in_channels: the channels after skip-connect
    """
    def __init__(self, in_channels, out_channels, hidden_channels=None, act_func="relu", bilinear=True, use_attn=True):
        super(UpSample, self).__init__()
        self.use_attn = use_attn
        if bilinear:
            self.up = nn.Sequential(
                nn.Upsample(scale_factor=2, mode='bilinear', align_corners=True),
                nn.Conv2d(in_channels, in_channels // 2, kernel_size=3, stride=1, padding=1, bias=False)
            )
        else:
            self.up = nn.ConvTranspose2d(in_channels, in_channels // 2, kernel_size=2, stride=2)

        if use_attn:
            self.attn_block = Attention_block(in_channels//2, in_channels // 2, in_channels // 4)

        self.conv = DoubleConv3x3(in_channels, out_channels, hidden_channels=hidden_channels, act_func=act_func)

    def forward(self, x_1, x_2):
        """
        Input shape: (B, C, H, W)
        :param x_1: the feature map to Up-sample
        :param x_2: the feature map from previous stage
        :return:
        """
        x_1 = self.up(x_1)
        diff_H = x_2.shape[-2] - x_1.shape[-2]
        diff_W = x_2.shape[-1] - x_1.shape[-1]
        x_1 = F.pad(x_1, pad=[diff_W // 2, diff_W - diff_W // 2,
                            diff_H // 2, diff_H - diff_H // 2])
        if self.use_attn:
            x_2 = self.attn_block(x_1, x_2)
        x = torch.concat([x_2, x_1], dim=-3)
        return self.conv(x)


class PositionalEmbedding(nn.Module):
    def __init__(self, dim, scale=1.0):
        super(PositionalEmbedding, self).__init__()
        assert dim % 2 == 0
        self.dim = dim
        self.scale = scale

    def forward(self, x):
        device = x.device
        half_dim = self.dim // 2
        emb = math.log(10000) / half_dim
        emb = torch.exp(torch.arange(half_dim, device=device) * -emb)
        emb = torch.outer(x * self.scale, emb)
        emb = torch.cat((emb.sin(), emb.cos()), dim=-1)
        return emb


class TimeEmbedding(nn.Module):
    def __init__(self, dim, scale=1.0, act_func='relu'):
        super(TimeEmbedding, self).__init__()
        self.embedding = PositionalEmbedding(dim, scale)
        self.w = nn.Sequential(
            nn.Linear(dim, 2 * dim),
            act_selector(act_func),
            nn.Linear(2 * dim, dim)
        )

    def forward(self, x):
        x = self.embedding(x)
        x = self.w(x)
        return x
