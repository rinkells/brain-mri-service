"""Grad-CAM for layer4, the last convolutional block of a torchvision ResNet."""

import base64
import io

import numpy as np
import torch
from PIL import Image
from torchvision.models import ResNet

from app.preprocessing import square_padding

OVERLAY_ALPHA = 0.45  # same heat map opacity as in the training notebook figures
MAX_OVERLAY_SIDE = 1024  # larger scans are downscaled so the returned PNG stays small


def _features(model: ResNet, batch: torch.Tensor) -> torch.Tensor:
    """Run the network up to and including layer4."""
    x = model.conv1(batch)
    x = model.bn1(x)
    x = model.relu(x)
    x = model.maxpool(x)
    x = model.layer1(x)
    x = model.layer2(x)
    x = model.layer3(x)
    return model.layer4(x)


def _head(model: ResNet, features: torch.Tensor) -> torch.Tensor:
    """Run what follows layer4: global average pooling and the classifier."""
    return model.fc(torch.flatten(model.avgpool(features), 1))


def grad_cam(
    model: ResNet, batch: torch.Tensor, class_idx: int | None = None
) -> tuple[torch.Tensor, np.ndarray]:
    """Return the logits and the layer4 Grad-CAM map for one preprocessed image.

    ``batch`` has shape (1, 3, H, W). The map explains ``class_idx`` or, by default,
    the predicted class. It has the spatial size of the layer4 output (7x7 for a
    224x224 input) and values in [0, 1]. Must not be called under ``inference_mode``.
    """
    if batch.shape[0] != 1:
        raise ValueError("Grad-CAM expects a batch with exactly one image")

    # The gradient of a logit with respect to the layer4 output depends only on the
    # layers after layer4, so the backbone runs without building an autograd graph.
    with torch.no_grad():
        activations = _features(model, batch)
    activations.requires_grad_(True)

    with torch.enable_grad():
        logits = _head(model, activations)
        if class_idx is None:
            class_idx = int(logits.argmax(dim=1))
        (gradients,) = torch.autograd.grad(logits[0, class_idx], activations)

    weights = gradients.mean(dim=(2, 3), keepdim=True)
    cam = torch.relu((weights * activations.detach()).sum(dim=1))[0]
    cam = (cam - cam.min()) / (cam.max() - cam.min() + 1e-8)
    return logits.detach(), cam.cpu().numpy()


def _jet(values: np.ndarray) -> np.ndarray:
    """Map values in [0, 1] to RGB in [0, 1]: blue (low) -> green -> red (high)."""
    centres = np.array([3.0, 2.0, 1.0], dtype=np.float32)  # peaks of R, G, B
    return np.clip(1.5 - np.abs(values[..., None] * 4.0 - centres), 0.0, 1.0)


def overlay_heatmap(
    image: Image.Image, cam: np.ndarray, alpha: float = OVERLAY_ALPHA
) -> Image.Image:
    """Blend the Grad-CAM map over the scan and return an RGB picture.

    The map covers the padded square the model saw, so it is upscaled to that square
    and the padding is cropped away, which aligns the map with the original scan.
    """
    base = image.convert("RGB")
    if max(base.size) > MAX_OVERLAY_SIDE:
        base.thumbnail((MAX_OVERLAY_SIDE, MAX_OVERLAY_SIDE))

    width, height = base.size
    side = max(width, height)
    left, top = square_padding(width, height)

    heat = Image.fromarray(cam.astype(np.float32)).resize((side, side), Image.Resampling.BILINEAR)
    heat = np.asarray(heat.crop((left, top, left + width, top + height)))
    colours = _jet(np.clip(heat, 0.0, 1.0))

    pixels = np.asarray(base, dtype=np.float32) / 255.0
    blended = (1.0 - alpha) * pixels + alpha * colours
    return Image.fromarray((blended * 255.0).round().astype(np.uint8))


def encode_png_base64(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")
