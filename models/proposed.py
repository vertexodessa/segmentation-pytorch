import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.utils.tensorboard
import torchvision

import models
import models.backbone


class Proposed(nn.Module):
    def __init__(self, backbone: str, output_stride: int, num_classes: int) -> None:
        super(Proposed, self).__init__()
        # Backbone
        if backbone == 'ResNet101':
            self.backbone = models.backbone.resnet101.ResNet101(output_stride)
        elif backbone == 'Xception':
            self.backbone = models.backbone.xception.Xception(output_stride)
            self.backbone.load_state_dict(torch.load('weights/xception_65_imagenet.pth'))
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
        self.decoder = Decoder(num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        size = x.size()[2:]

        x = self.backbone(x)
        x = self.aspp(x)
        x = self.decoder(x, self.backbone.low_level_feature)
        x = F.interpolate(x, size=size, mode='bilinear', align_corners=False)
        return x


class Decoder(nn.Module):
    def __init__(self, num_classes: int) -> None:
        super(Decoder, self).__init__()
        self.compress_low_level_feature1 = self.make_compressor(512, 64)
        self.compress_low_level_feature2 = self.make_compressor(256, 32)
        self.decode1 = self.make_decoder(256 + 64, 256)
        self.decode2 = self.make_decoder(256 + 32, 256)
        self.classifier = nn.Conv2d(256, num_classes, kernel_size=1)

    def forward(self, x: torch.Tensor, low_level_feature: list[torch.Tensor]) -> torch.Tensor:
        low_level_feature1 = self.compress_low_level_feature1(low_level_feature.pop())
        x = F.interpolate(x, size=low_level_feature1.size()[2:], mode='bilinear', align_corners=False)
        x = torch.cat((x, low_level_feature1), dim=1)
        x = self.decode1(x)

        low_level_feature2 = self.compress_low_level_feature2(low_level_feature.pop())
        x = F.interpolate(x, size=low_level_feature2.size()[2:], mode='bilinear', align_corners=False)
        x = torch.cat((x, low_level_feature2), dim=1)
        x = self.decode2(x)

        x = self.classifier(x)
        return x

    def make_compressor(self, in_channels: int, out_channels: int):
        return nn.Sequential(
            nn.Conv2d(in_channels, out_channels, kernel_size=1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True)
        )

    def make_decoder(self, in_channels: int, out_channels: int) -> nn.Sequential:
        return nn.Sequential(
            nn.Conv2d(in_channels, out_channels, kernel_size=5, stride=1, padding=2, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True)
        )


if __name__ == '__main__':
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = Proposed('ResNet101', output_stride=16, num_classes=20).to(device)
    models.test.test_model(model, (3, 400, 800), device)