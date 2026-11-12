import base64
import io

import numpy as np
import pytest
import torch
from PIL import Image
from torchvision.models import resnet18

from app.gradcam import MAX_OVERLAY_SIDE, encode_png_base64, grad_cam, overlay_heatmap
from app.preprocessing import build_transform
from tests.helpers import make_image


@pytest.fixture(scope="module")
def model():
    torch.manual_seed(0)
    return resnet18(weights=None, num_classes=4).eval()


@pytest.fixture(scope="module")
def batch():
    return build_transform()(make_image(512, 512, "RGB")).unsqueeze(0)


def hook_based_grad_cam(model, batch, class_idx):
    """Textbook Grad-CAM with hooks and backward(), as in the training notebook."""
    acts, grads = [], []

    def forward_hook(module, inputs, output):
        acts.append(output)
        output.register_hook(grads.append)

    handle = model.layer4[-1].register_forward_hook(forward_hook)
    model.zero_grad()
    logits = model(batch)
    logits[0, class_idx].backward()
    handle.remove()
    model.zero_grad()

    weights = grads[0].mean(dim=(2, 3), keepdim=True)
    cam = torch.relu((weights * acts[0]).sum(dim=1))[0]
    cam = (cam - cam.min()) / (cam.max() - cam.min() + 1e-8)
    return cam.detach().numpy()


# --- grad_cam ------------------------------------------------------------------


def test_logits_equal_plain_forward_pass(model, batch):
    logits, _ = grad_cam(model, batch)

    with torch.no_grad():
        expected = model(batch)

    assert torch.allclose(logits, expected, atol=1e-6)
    assert not logits.requires_grad


def test_cam_has_layer4_resolution_and_unit_range(model, batch):
    _, cam = grad_cam(model, batch)

    assert cam.shape == (7, 7)
    assert cam.dtype == np.float32
    assert cam.min() >= 0.0
    assert cam.max() <= 1.0


@pytest.mark.parametrize("class_idx", range(4))
def test_cam_matches_hook_based_reference(model, batch, class_idx):
    _, cam = grad_cam(model, batch, class_idx)

    assert np.allclose(cam, hook_based_grad_cam(model, batch, class_idx), atol=1e-5)


def test_cam_is_not_trivially_empty(batch):
    # With positive classifier weights every active location supports class 0.
    torch.manual_seed(0)
    positive = resnet18(weights=None, num_classes=4).eval()
    with torch.no_grad():
        positive.fc.weight[0].fill_(1.0)

    _, cam = grad_cam(positive, batch, class_idx=0)

    assert cam.max() == pytest.approx(1.0, abs=1e-4)
    assert cam.min() == pytest.approx(0.0, abs=1e-4)


def test_default_target_is_predicted_class(model, batch):
    logits, cam = grad_cam(model, batch)
    predicted = int(logits.argmax(dim=1))

    _, expected = grad_cam(model, batch, class_idx=predicted)

    assert np.array_equal(cam, expected)


def test_different_classes_give_different_maps(model, batch):
    maps = [grad_cam(model, batch, class_idx=c)[1] for c in range(4)]

    assert any(not np.allclose(maps[0], other) for other in maps[1:])


def test_model_is_left_untouched(model, batch):
    grad_cam(model, batch)

    assert not model.training
    assert all(p.grad is None for p in model.parameters())


def test_works_with_frozen_parameters_and_inside_no_grad(batch):
    torch.manual_seed(0)
    frozen = resnet18(weights=None, num_classes=4).eval().requires_grad_(False)

    with torch.no_grad():
        logits, cam = grad_cam(frozen, batch)

    assert logits.shape == (1, 4)
    assert cam.shape == (7, 7)


def test_input_batch_is_not_modified(model, batch):
    before = batch.clone()

    grad_cam(model, batch)

    assert torch.equal(batch, before)
    assert not batch.requires_grad


def test_rejects_batch_with_several_images(model, batch):
    with pytest.raises(ValueError):
        grad_cam(model, torch.cat([batch, batch]))


# --- overlay_heatmap -----------------------------------------------------------


def cam_with(row=None, col=None):
    cam = np.zeros((7, 7), dtype=np.float32)
    cam[row if row is not None else slice(None), col if col is not None else slice(None)] = 1.0
    return cam


@pytest.mark.parametrize("size", [(512, 512), (640, 480), (213, 236)])
def test_overlay_keeps_scan_size(size):
    overlay = overlay_heatmap(make_image(*size, "RGB"), cam_with(3, 3))

    assert overlay.mode == "RGB"
    assert overlay.size == size


def test_overlay_puts_heat_where_the_map_is_hot():
    black = Image.new("RGB", (512, 512))

    overlay = np.asarray(overlay_heatmap(black, cam_with(0, 0))).astype(int)  # top-left cell

    red, blue = overlay[..., 0], overlay[..., 2]
    assert red[5, 5] > 0 and blue[5, 5] == 0  # hot corner is red
    assert blue[-5, -5] > 0 and red[-5, -5] == 0  # cold corner is blue


def test_overlay_crops_padding_of_non_square_scan():
    # A 400x200 scan sits in rows 100..300 of the 400x400 square the model saw.
    black = Image.new("RGB", (400, 200))

    in_padding = np.asarray(overlay_heatmap(black, cam_with(row=0)))  # heat above the scan
    on_scan = np.asarray(overlay_heatmap(black, cam_with(row=3)))  # heat on the middle rows

    assert (in_padding[..., 0] == 0).all()  # nothing red is left after cropping
    assert (on_scan[100, :, 0] > 0).all()  # the middle row of the scan is red


def test_overlay_blends_scan_and_heat():
    white = Image.new("RGB", (64, 64), (255, 255, 255))

    overlay = np.asarray(overlay_heatmap(white, np.zeros((7, 7), dtype=np.float32), alpha=0.5))

    # Cold jet colour is (0, 0, 0.5): half of white plus half of that.
    assert np.abs(overlay[32, 32].astype(int) - np.array([128, 128, 191])).max() <= 1


def test_overlay_downscales_large_scans_and_keeps_original():
    large = make_image(2048, 1024, "RGB")

    overlay = overlay_heatmap(large, cam_with(3, 3))

    assert overlay.size == (MAX_OVERLAY_SIDE, MAX_OVERLAY_SIDE // 2)
    assert large.size == (2048, 1024)


def test_overlay_accepts_grayscale_scan():
    overlay = overlay_heatmap(make_image(128, 128, "L"), cam_with(3, 3))

    assert overlay.mode == "RGB"


# --- encode_png_base64 ---------------------------------------------------------


def test_base64_png_round_trip():
    image = make_image(40, 30, "RGB")

    decoded = Image.open(io.BytesIO(base64.b64decode(encode_png_base64(image), validate=True)))

    assert decoded.format == "PNG"
    assert np.array_equal(np.asarray(decoded), np.asarray(image))
