"""Loading of the trained weights and inference."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torchvision.models import ResNet, resnet18

from app.config import CLASS_NAMES, ClassName
from app.gradcam import grad_cam
from app.preprocessing import build_transform


class ModelLoadError(RuntimeError):
    """The model cannot be prepared, so the service must not start."""


def resolve_device(name: str) -> torch.device:
    try:
        device = torch.device(name)
    except RuntimeError as exc:
        raise ModelLoadError(f"Unknown DEVICE {name!r}: use e.g. 'cpu' or 'cuda'.") from exc

    if device.type == "cuda" and not torch.cuda.is_available():
        raise ModelLoadError(
            f"DEVICE={name} was requested, but CUDA is not available. Use DEVICE=cpu."
        )
    return device


def load_model(path: Path, device: torch.device) -> ResNet:
    """Build ResNet18 with one output per class and load the trained state_dict."""
    if not path.is_file():
        raise ModelLoadError(
            f"Weights file not found: {path}. Put the weights there or set MODEL_PATH."
        )

    model = resnet18(weights=None, num_classes=len(CLASS_NAMES))
    try:
        # weights_only=True refuses anything but plain tensors, so a tampered
        # checkpoint cannot execute code while it is being loaded.
        state_dict = torch.load(path, weights_only=True, map_location="cpu")
        model.load_state_dict(state_dict)
        model.to(device)
    except Exception as exc:
        raise ModelLoadError(
            f"Cannot load {path} as a ResNet18 state_dict with {len(CLASS_NAMES)} outputs: {exc}"
        ) from exc

    return model.eval().requires_grad_(False)


@dataclass(frozen=True)
class Prediction:
    predicted_class: ClassName
    probabilities: dict[ClassName, float]  # in CLASS_NAMES order
    cam: np.ndarray | None = None  # layer4 Grad-CAM map for the predicted class


class BrainMRIClassifier:
    def __init__(self, model: ResNet, device: torch.device) -> None:
        self.model = model
        self.device = device
        self._transform = build_transform()

    @classmethod
    def from_file(cls, path: Path, device_name: str) -> "BrainMRIClassifier":
        device = resolve_device(device_name)
        return cls(load_model(path, device), device)

    def predict(self, image: Image.Image, with_cam: bool = False) -> Prediction:
        """Classify one RGB scan; optionally explain the prediction with Grad-CAM."""
        batch = self._transform(image).unsqueeze(0).to(self.device)

        cam = None
        if with_cam:
            logits, cam = grad_cam(self.model, batch)
        else:
            with torch.inference_mode():
                logits = self.model(batch)

        probabilities = torch.softmax(logits[0], dim=0).tolist()
        predicted = CLASS_NAMES[int(logits[0].argmax())]
        return Prediction(predicted, dict(zip(CLASS_NAMES, probabilities)), cam)
