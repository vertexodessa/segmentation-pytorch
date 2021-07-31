from typing import List

import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision

import models
import models.backbone
import utils


class Attention_DeepLabv3plus(nn.Module):
    def __init__(self, backbone: str, output_stride: int, num_classes: int):
        super(Attention_DeepLabv3plus, self).__init__()
        self.low_level_feature = []

        # Backbone
        if backbone == 'ResNet101':
            self.backbone = models.backbone.resnet101.ResNet101(output_stride)
            self.backbone.layer1.register_forward_hook(utils.hooks.get_feature_maps(self.low_level_feature))
            self.backbone.layer2.register_forward_hook(utils.hooks.get_feature_maps(self.low_level_feature))
        elif backbone == 'Xception':
            self.backbone = models.backbone.xception.xception(output_stride, pretrained=True)
            self.backbone.block2.sepconv2.register_forward_hook(utils.hooks.get_feature_maps(self.low_level_feature))
            self.backbone.block3.sepconv2.register_forward_hook(utils.hooks.get_feature_maps(self.low_level_feature))
        else:
            raise NotImplementedError('Wrong backbone.')

        # ASPP
        if output_stride == 16:
            atrous_rates = (6, 12, 18)
        elif output_stride == 8:
            atrous_rates = (12, 24, 36)
        else:
            raise NotImplementedError('Wrong output_stride.')
        self.aspp = torchvision.models.segmentation.deeplabv3.ASPP(2048, atrous_rates, 256)

        # Decoder
        self.decoder = Decoder(backbone, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        size = x.size()[2:]

        x = self.backbone(x)
        x = self.aspp(x)
        x = self.decoder(x, self.low_level_feature)
        x = F.interpolate(x, size=size, mode='bilinear', align_corners=False)
        return x

    def freeze_bn(self):
        for m in self.modules():
            if isinstance(m, nn.BatchNorm2d):
                m.eval()
                for param in m.parameters():
                    param.requires_grad = False


class Decoder(nn.Module):
    def __init__(self, backbone: str, num_classes: int):
        super(Decoder, self).__init__()
        if backbone == 'ResNet101':
            self.compress_low_level_feature1 = self.make_compressor(512, 256)
        elif backbone == 'Xception':
            self.compress_low_level_feature1 = self.make_compressor(728, 256)
        else:
            raise NotImplementedError('Wrong backbone.')

        self.compress_low_level_feature2 = self.make_compressor(256, 256)
        self.attention_block1 = AttentionBlock(256)
        self.attention_block2 = AttentionBlock(256)
        self.classifier = nn.Conv2d(256, num_classes, kernel_size=1)

    def forward(self, x: torch.Tensor, low_level_feature: List[torch.Tensor]) -> torch.Tensor:
        low_level_feature1 = self.compress_low_level_feature1(low_level_feature.pop())
        x = F.interpolate(x, size=low_level_feature1.size()[2:], mode='bilinear', align_corners=False)
        x = self.attention_block1(x, low_level_feature1)

        low_level_feature2 = self.compress_low_level_feature2(low_level_feature.pop())
        x = F.interpolate(x, size=low_level_feature2.size()[2:], mode='bilinear', align_corners=False)
        x = self.attention_block2(x, low_level_feature2)

        x = self.classifier(x)
        return x


class AttentionBlock(nn.Module):
    def __init__(self, in_channels: int):
        super(AttentionBlock, self).__init__()
        self.channel_attention = ChannelAttention(in_channels)
        self.spatial_attention = SpatialAttention(in_channels)

    def forward(self, x: torch.Tensor, low_level_feature: torch.Tensor) -> torch.Tensor:
        ca_vector = self.channel_attention(x)
        sa_matrix = self.spatial_attention(low_level_feature)
        out = x + low_level_feature
        ca = out * ca_vector
        sa = out * sa_matrix
        return ca + sa


class ChannelAttention(nn.Sequential):
    def __init__(self, in_channels: int, reduction_ratio=4):
        super(ChannelAttention, self).__init__(
            nn.AdaptiveAvgPool2d(1),
            nn.Conv2d(in_channels, in_channels // reduction_ratio, 1, bias=False),
            nn.BatchNorm2d(in_channels // reduction_ratio),
            nn.ReLU(inplace=True),
            nn.Conv2d(in_channels // reduction_ratio, in_channels, 1, bias=False),
            nn.BatchNorm2d(in_channels),
            nn.Sigmoid()
        )


class SpatialAttention(nn.Module):
    def __init__(self, kernel_size=7):
        super(SpatialAttention, self).__init__()
        self.conv = nn.Conv2d(2, 1, kernel_size, padding=kernel_size // 2, bias=False)
        self.bn = nn.BatchNorm2d(1)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        max_out = torch.max(x, dim=1, keepdim=True)[0]
        avg_out = torch.mean(x, dim=1, keepdim=True)
        x = torch.cat((max_out, avg_out), dim=1)
        x = self.conv(x)
        x = self.bn(x)
        x = self.sigmoid(x)
        return x


if __name__ == '__main__':
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = Attention_DeepLabv3plus('Xception', output_stride=16, num_classes=19).to(device)
    models.test.test_model(model, (3, 512, 1024), device)